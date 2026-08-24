"""Database-level regression coverage for the semester discipline workflow.

The suite intentionally uses only the public ``CBVMSDatabase`` API except when
asserting persisted audit/idempotency invariants.  Every test owns a temporary
SQLite database, and every workflow timestamp is an injected, aware UTC value.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.discipline import (
    AUTO_CONFIRMED,
    CONFIRMED,
    DISMISSED,
    PENDING_REVIEW,
    format_db_datetime,
)
from database.db_manager import CBVMSDatabase


UTC = timezone.utc
DETECTED_AT = datetime(2099, 8, 1, 2, 0, 0, tzinfo=UTC)
REVIEW_DEADLINE = DETECTED_AT + timedelta(days=5)


class ViolationWorkflowTests(unittest.TestCase):
    """End-to-end workflow tests against a real temporary SQLite database."""

    STUDENT_A = "2099-00001"
    STUDENT_B = "2099-00002"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cbvms_workflow_")
        self.db_path = Path(self._tmp.name) / "cbvms.db"
        self.db = CBVMSDatabase(self.db_path)
        self.db.initialize()
        self._insert_student(self.STUDENT_A, "Alice Test")
        self._insert_student(self.STUDENT_B, "Bob Test")
        self.term_1 = self.db.get_current_academic_term()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _insert_student(self, student_id: str, name: str) -> int:
        return self.db.insert_student(
            student_id=student_id,
            name=name,
            course="BSIT",
            year_and_section="3A",
            encoding=b"",
            photo=b"",
            gender="Unknown",
        )

    def _detect(
        self,
        *,
        student_id: str | None = None,
        student_name: str = "Alice Test",
        code: str = "wrong_uniform",
        display: str = "Wrong uniform (82%)",
        detected_at: datetime = DETECTED_AT,
        status: str = PENDING_REVIEW,
    ) -> int:
        return self.db.log_violation(
            student_id=student_id or self.STUDENT_A,
            student_name=student_name,
            violation_type=display,
            violation_code=code,
            detected_at=detected_at,
            status=status,
        )

    def _violation(self, violation_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()
        self.assertIsNotNone(row)
        return dict(row)

    def _strike(self, violation_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM strikes WHERE violation_id = ?", (violation_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def _count(self, table: str, where: str = "", params: tuple = ()) -> int:
        # Table names and WHERE fragments are constants owned by this test module.
        sql = f"SELECT COUNT(*) AS count FROM {table}"
        if where:
            sql += f" WHERE {where}"
        with self.db.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["count"])

    def _confirm(
        self,
        violation_id: int,
        *,
        at: datetime | None = None,
        auto: bool = False,
    ) -> bool:
        return self.db.confirm_violation(
            violation_id,
            decided_by="system:auto_review" if auto else "admin",
            confirmed_at=at or (DETECTED_AT + timedelta(days=1)),
            auto=auto,
        )

    def _submit_appeal(
        self,
        violation_id: int,
        *,
        at: datetime,
        student_id: str | None = None,
        reason: str = "A detailed, timely explanation for administrator review.",
    ) -> int | None:
        """Submit through the production API while controlling only the server clock."""

        with patch("database.db_manager.utc_now", return_value=at):
            return self.db.insert_appeal(
                violation_id,
                student_id or self.STUDENT_A,
                reason,
            )

    # ------------------------------------------------------------------
    # Administrative review: prompt tests 1-5 plus exact boundaries
    # ------------------------------------------------------------------

    def test_01_camera_violation_starts_pending_without_strike_or_notice(self) -> None:
        violation_id = self._detect(status="unreviewed")

        row = self._violation(violation_id)
        self.assertEqual(row["status"], PENDING_REVIEW)
        self.assertEqual(row["violation_code"], "wrong_uniform")
        self.assertEqual(row["timestamp"], format_db_datetime(DETECTED_AT))
        self.assertEqual(row["review_deadline"], format_db_datetime(REVIEW_DEADLINE))
        self.assertEqual(row["semester_id"], self.term_1["id"])
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 0)
        self.assertEqual(self._count("student_notifications"), 0)
        self.assertEqual(self.db.get_notifications_for_student(self.STUDENT_A), [])

    def test_02_admin_confirms_early_and_delivers_one_strike_and_notice(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)

        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        row = self._violation(violation_id)
        self.assertEqual(row["status"], CONFIRMED)
        self.assertEqual(row["confirmed_at"], format_db_datetime(confirmed_at))
        self.assertEqual(
            row["appeal_deadline"],
            format_db_datetime(confirmed_at + timedelta(days=5)),
        )
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 1)
        notices = self.db.get_notifications_for_student(self.STUDENT_A)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["event_key"], f"violation:{violation_id}:confirmed")

    def test_03_admin_dismisses_during_review_without_strike_or_delivery(self) -> None:
        violation_id = self._detect()
        decided_at = DETECTED_AT + timedelta(days=1)

        self.assertTrue(
            self.db.dismiss_violation(
                violation_id,
                decided_by="admin",
                reason="False positive",
                decided_at=decided_at,
            )
        )

        row = self._violation(violation_id)
        self.assertEqual(row["status"], DISMISSED)
        self.assertEqual(row["dismissal_reason"], "False positive")
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 0)
        self.assertEqual(self.db.get_visible_violations_for_student(
            self.STUDENT_A, now=decided_at
        ), [])
        self.assertEqual(self.db.get_notifications_for_student(self.STUDENT_A), [])

    def test_04_no_admin_action_for_five_days_auto_confirms(self) -> None:
        violation_id = self._detect()

        result = self.db.process_expired_deadlines(now=REVIEW_DEADLINE)

        self.assertEqual(result["auto_confirmed"], 1)
        self.assertEqual(self._violation(violation_id)["status"], AUTO_CONFIRMED)
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 1)
        self.assertEqual(len(self.db.get_notifications_for_student(self.STUDENT_A)), 1)

    def test_05_restart_after_deadline_processes_persisted_pending_record(self) -> None:
        violation_id = self._detect()
        restarted = CBVMSDatabase(self.db_path)
        reopened_at = REVIEW_DEADLINE + timedelta(days=7)

        with patch("database.db_manager.utc_now", return_value=reopened_at):
            restarted.initialize()

        row = self._violation(violation_id)
        self.assertEqual(row["status"], AUTO_CONFIRMED)
        self.assertEqual(row["confirmed_at"], format_db_datetime(REVIEW_DEADLINE))
        self.assertEqual(
            row["appeal_deadline"],
            format_db_datetime(REVIEW_DEADLINE + timedelta(days=5)),
        )
        self.assertEqual(
            row["appeal_window_closed_at"], format_db_datetime(reopened_at)
        )
        self.assertEqual(restarted.get_strike_count(self.STUDENT_A, "wrong_uniform"), 1)
        visible = restarted.get_visible_violations_for_student(
            self.STUDENT_A, now=reopened_at
        )
        self.assertEqual(len(visible), 1)
        self.assertFalse(visible[0]["can_appeal"])
        self.assertEqual(visible[0]["appeal_window_status"], "expired")
        notices = restarted.get_notifications_for_student(self.STUDENT_A)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["created_at"], format_db_datetime(REVIEW_DEADLINE))

    def test_review_processor_does_not_confirm_before_exact_deadline(self) -> None:
        violation_id = self._detect()

        before = self.db.process_expired_deadlines(
            now=REVIEW_DEADLINE - timedelta(seconds=1)
        )
        self.assertEqual(before["auto_confirmed"], 0)
        self.assertEqual(self._violation(violation_id)["status"], PENDING_REVIEW)

        exact = self.db.process_expired_deadlines(now=REVIEW_DEADLINE)
        self.assertEqual(exact["auto_confirmed"], 1)
        self.assertEqual(self._violation(violation_id)["status"], AUTO_CONFIRMED)

    def test_dismissal_after_review_deadline_is_not_allowed(self) -> None:
        violation_id = self._detect()

        dismissed = self.db.dismiss_violation(
            violation_id,
            decided_by="admin",
            reason="Too late",
            decided_at=REVIEW_DEADLINE + timedelta(seconds=1),
        )

        self.assertFalse(dismissed)
        row = self._violation(violation_id)
        self.assertEqual(row["status"], AUTO_CONFIRMED)
        self.assertEqual(row["confirmed_at"], format_db_datetime(REVIEW_DEADLINE))
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 1)
        notices = self.db.get_notifications_for_student(self.STUDENT_A)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["event_key"], f"violation:{violation_id}:confirmed")

    def test_confirmed_and_dismissed_transitions_cannot_be_reversed(self) -> None:
        dismissed_id = self._detect()
        self.assertTrue(
            self.db.dismiss_violation(
                dismissed_id,
                decided_by="admin",
                reason="Verified false detection",
                decided_at=DETECTED_AT + timedelta(hours=1),
            )
        )
        self.assertFalse(
            self._confirm(dismissed_id, at=DETECTED_AT + timedelta(hours=2))
        )
        self.assertEqual(self._violation(dismissed_id)["status"], DISMISSED)
        self.assertIsNone(self._strike(dismissed_id))

        confirmed_id = self._detect(detected_at=DETECTED_AT + timedelta(minutes=1))
        self.assertTrue(
            self._confirm(confirmed_id, at=DETECTED_AT + timedelta(hours=1))
        )
        self.assertFalse(
            self.db.dismiss_violation(
                confirmed_id,
                decided_by="admin",
                reason="A confirmed record cannot be reversed through review dismissal",
                decided_at=DETECTED_AT + timedelta(hours=2),
            )
        )
        self.assertEqual(self._violation(confirmed_id)["status"], CONFIRMED)
        self.assertEqual(self._strike(confirmed_id)["is_active"], 1)
        self.assertEqual(self._count("strikes"), 1)

    # ------------------------------------------------------------------
    # Strike ledger: prompt tests 6-9
    # ------------------------------------------------------------------

    def test_06_three_confirmed_uniform_violations_make_three_strikes(self) -> None:
        for offset in range(3):
            detected = DETECTED_AT + timedelta(hours=offset)
            violation_id = self._detect(detected_at=detected)
            self.assertTrue(self._confirm(violation_id, at=detected + timedelta(hours=1)))

        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 3)
        summary = {item["violation_code"]: item for item in self.db.get_strike_summary(
            self.STUDENT_A
        )}
        self.assertTrue(summary["wrong_uniform"]["action_required"])
        self.assertEqual(self._count("strike_events"), 1)

    def test_07_strikes_are_counted_per_category(self) -> None:
        specs = [
            ("wrong_uniform", "Wrong uniform (80%)"),
            ("wrong_uniform", "Wrong uniform (90%)"),
            ("earring", "Earring detected (88%)"),
        ]
        for offset, (code, display) in enumerate(specs):
            detected = DETECTED_AT + timedelta(hours=offset)
            violation_id = self._detect(
                code=code, display=display, detected_at=detected
            )
            self.assertTrue(self._confirm(violation_id, at=detected + timedelta(minutes=5)))

        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 2)
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "earring"), 1)

    def test_08_same_violation_processed_repeatedly_has_one_ledger_row(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)

        self.assertTrue(self._confirm(violation_id, at=confirmed_at))
        self.assertTrue(self._confirm(violation_id, at=confirmed_at + timedelta(hours=1)))
        self.db.ensure_notifications_for_student(self.STUDENT_A)
        self.db.ensure_notifications_for_student(self.STUDENT_A)

        self.assertEqual(self._count("strikes", "violation_id = ?", (violation_id,)), 1)
        self.assertEqual(
            self._count(
                "student_notifications",
                "event_key = ?",
                (f"violation:{violation_id}:confirmed",),
            ),
            1,
        )

    def test_09_fourth_uniform_violation_counts_without_duplicate_third_event(self) -> None:
        ids: list[int] = []
        for offset in range(4):
            detected = DETECTED_AT + timedelta(hours=offset)
            violation_id = self._detect(detected_at=detected)
            ids.append(violation_id)
            self.assertTrue(self._confirm(violation_id, at=detected + timedelta(minutes=5)))

        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 4)
        self.assertEqual(self._count("strike_events"), 1)
        self.assertEqual(
            self._count("student_notifications", "event_key LIKE 'strike_event:%:reached'"),
            1,
        )
        self.assertEqual(self._count("strikes"), len(ids))

    # ------------------------------------------------------------------
    # Appeals: prompt tests 10-16 plus authorization/boundaries
    # ------------------------------------------------------------------

    def test_10_confirmed_violation_allows_appeal_within_five_days(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        eligibility = self.db.get_appeal_eligibility(
            violation_id,
            self.STUDENT_A,
            now=confirmed_at + timedelta(days=4),
        )
        appeal_id = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=4),
            reason="The camera identified the wrong garment; evidence is attached.",
        )

        self.assertTrue(eligibility["eligible"])
        self.assertIsNotNone(appeal_id)

    def test_11_appeal_after_deadline_is_rejected_by_backend(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        appeal_id = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=5, seconds=1),
            reason="This request is intentionally submitted after the persisted deadline.",
        )

        self.assertIsNone(appeal_id)
        self.assertEqual(self._count("appeals"), 0)

    def test_appeal_submission_timestamp_cannot_be_supplied_by_caller(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        with self.assertRaises(TypeError):
            self.db.insert_appeal(
                violation_id,
                self.STUDENT_A,
                "A caller must not be able to forge a backdated submission timestamp.",
                submitted_at=confirmed_at + timedelta(days=1),
            )
        self.assertEqual(self._count("appeals"), 0)

    def test_12_timely_day_five_appeal_remains_valid_for_late_admin_review(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        deadline = confirmed_at + timedelta(days=5)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))
        appeal_id = self._submit_appeal(
            violation_id,
            at=deadline,
            reason="Submitted exactly at the deadline with a specific supporting explanation.",
        )
        self.assertIsNotNone(appeal_id)

        self.db.process_expired_deadlines(now=deadline + timedelta(days=4))
        self.assertTrue(
            self.db.update_appeal_decision(
                int(appeal_id),
                "rejected",
                "Evidence did not overturn the detection.",
                decided_at=deadline + timedelta(days=4),
            )
        )
        self.assertEqual(self.db.get_appeal_for_violation(violation_id)["status"], "rejected")

    def test_13_pending_appeal_keeps_strike_active(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))
        appeal_id = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=1),
            reason="A sufficiently detailed and timely explanation for administrator review.",
        )

        self.assertIsNotNone(appeal_id)
        self.assertEqual(self.db.get_appeal_for_violation(violation_id)["status"], "pending")
        self.assertEqual(self._strike(violation_id)["is_active"], 1)

    def test_14_approved_appeal_deactivates_exact_strike(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))
        appeal_id = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=1),
            reason="The submitted photo and explanation show that this was a false detection.",
        )

        self.assertTrue(
            self.db.update_appeal_decision(
                int(appeal_id),
                "approved",
                "Appeal evidence verified.",
                decided_at=confirmed_at + timedelta(days=8),
            )
        )

        strike = self._strike(violation_id)
        self.assertEqual(strike["is_active"], 0)
        self.assertEqual(strike["deactivation_reason"], "appeal_approved")
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 0)

    def test_15_rejected_appeal_leaves_strike_active(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))
        appeal_id = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=1),
            reason="The student requests review but the original evidence remains available.",
        )

        self.assertTrue(
            self.db.update_appeal_decision(
                int(appeal_id),
                "rejected",
                "Original evidence remains conclusive.",
                decided_at=confirmed_at + timedelta(days=8),
            )
        )

        self.assertEqual(self._strike(violation_id)["is_active"], 1)
        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 1)

    def test_16_approved_appeal_reduces_three_to_two_and_resolves_condition(self) -> None:
        violation_ids: list[int] = []
        for offset in range(3):
            detected = DETECTED_AT + timedelta(hours=offset)
            violation_id = self._detect(detected_at=detected)
            violation_ids.append(violation_id)
            self.assertTrue(self._confirm(violation_id, at=detected + timedelta(minutes=10)))

        appealed_id = violation_ids[1]
        confirmed_at = datetime.fromisoformat(self._violation(appealed_id)["confirmed_at"]).replace(
            tzinfo=UTC
        )
        appeal_id = self._submit_appeal(
            appealed_id,
            at=confirmed_at + timedelta(days=1),
            reason="The middle detection is disputed with detailed supporting evidence.",
        )
        self.assertTrue(
            self.db.update_appeal_decision(
                int(appeal_id),
                "approved",
                "Evidence accepted.",
                decided_at=confirmed_at + timedelta(days=2),
            )
        )

        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 2)
        with self.db.connect() as conn:
            event = conn.execute("SELECT * FROM strike_events").fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(event["is_active"], 0)
        self.assertEqual(event["resolution_reason"], "appeal_approved")
        summary = {item["violation_code"]: item for item in self.db.get_strike_summary(
            self.STUDENT_A
        )}
        self.assertFalse(summary["wrong_uniform"]["action_required"])

    def test_resolved_third_strike_reactivates_auditably_without_duplicate_event(self) -> None:
        violation_ids: list[int] = []
        for offset in range(3):
            detected = DETECTED_AT + timedelta(minutes=offset)
            violation_id = self._detect(detected_at=detected)
            violation_ids.append(violation_id)
            self.assertTrue(self._confirm(violation_id, at=detected + timedelta(minutes=5)))

        appealed_id = violation_ids[0]
        first_confirmed = datetime.fromisoformat(
            self._violation(appealed_id)["confirmed_at"]
        ).replace(tzinfo=UTC)
        appeal_id = self._submit_appeal(
            appealed_id,
            at=first_confirmed + timedelta(days=1),
            reason="A detailed appeal resolves the original third-strike condition.",
        )
        resolved_at = first_confirmed + timedelta(days=2)
        self.assertTrue(
            self.db.update_appeal_decision(
                int(appeal_id),
                "approved",
                "Evidence accepted.",
                decided_at=resolved_at,
            )
        )

        next_detected = DETECTED_AT + timedelta(days=3)
        next_id = self._detect(detected_at=next_detected)
        reactivated_at = next_detected + timedelta(hours=1)
        self.assertTrue(self._confirm(next_id, at=reactivated_at))

        with self.db.connect() as conn:
            events = conn.execute("SELECT * FROM strike_events").fetchall()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["is_active"], 1)
        self.assertEqual(event["resolved_at"], format_db_datetime(resolved_at))
        self.assertEqual(event["resolution_reason"], "appeal_approved")
        self.assertEqual(event["reactivated_at"], format_db_datetime(reactivated_at))
        self.assertEqual(event["reactivation_count"], 1)
        self.assertEqual(
            self._count(
                "student_notifications",
                "event_key LIKE 'strike_event:%:reached'",
            ),
            1,
        )
        summary = {
            item["violation_code"]: item
            for item in self.db.get_strike_summary(self.STUDENT_A)
        }
        self.assertTrue(summary["wrong_uniform"]["action_required"])

    def test_appeal_exact_deadline_is_allowed_then_one_second_late_is_not(self) -> None:
        exact_id = self._detect()
        late_id = self._detect(detected_at=DETECTED_AT + timedelta(minutes=1))
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(exact_id, at=confirmed_at))
        self.assertTrue(self._confirm(late_id, at=confirmed_at))
        deadline = confirmed_at + timedelta(days=5)

        exact = self._submit_appeal(
            exact_id,
            at=deadline,
            reason="This detailed appeal is submitted exactly at its authoritative deadline.",
        )
        late = self._submit_appeal(
            late_id,
            at=deadline + timedelta(seconds=1),
            reason="This detailed appeal is submitted one second beyond its deadline.",
        )

        self.assertIsNotNone(exact)
        self.assertIsNone(late)

    def test_expired_appeal_window_stays_visible_but_is_backend_ineligible(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        deadline = confirmed_at + timedelta(days=5)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        at_deadline = self.db.process_expired_deadlines(now=deadline)
        after_deadline = self.db.process_expired_deadlines(
            now=deadline + timedelta(seconds=1)
        )
        repeated = self.db.process_expired_deadlines(
            now=deadline + timedelta(days=1)
        )

        self.assertEqual(at_deadline["appeal_windows_expired"], 0)
        self.assertEqual(after_deadline["appeal_windows_expired"], 1)
        self.assertEqual(repeated["appeal_windows_expired"], 0)
        row = self._violation(violation_id)
        self.assertEqual(
            row["appeal_window_closed_at"],
            format_db_datetime(deadline + timedelta(seconds=1)),
        )
        eligibility = self.db.get_appeal_eligibility(
            violation_id,
            self.STUDENT_A,
            now=deadline + timedelta(seconds=1),
        )
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["reason"], "deadline_expired")
        visible = self.db.get_visible_violations_for_student(
            self.STUDENT_A, now=deadline + timedelta(seconds=1)
        )
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["id"], violation_id)
        self.assertEqual(visible[0]["appeal_window_status"], "expired")

    def test_student_cannot_appeal_another_students_violation(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        eligibility = self.db.get_appeal_eligibility(
            violation_id, self.STUDENT_B, now=confirmed_at + timedelta(days=1)
        )
        appeal_id = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=1),
            student_id=self.STUDENT_B,
            reason="A different account must never be able to appeal this violation.",
        )

        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["reason"], "not_owner")
        self.assertIsNone(appeal_id)

    def test_duplicate_appeal_and_repeat_decision_are_idempotently_rejected(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))
        first = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=1),
            reason="The first detailed appeal is the only submission that should be accepted.",
        )
        second = self._submit_appeal(
            violation_id,
            at=confirmed_at + timedelta(days=2),
            reason="A duplicate submission for the same violation must not be inserted.",
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

        decided_at = confirmed_at + timedelta(days=3)
        self.assertTrue(
            self.db.update_appeal_decision(
                int(first), "rejected", "Final decision.", decided_at=decided_at
            )
        )
        self.assertFalse(
            self.db.update_appeal_decision(
                int(first), "approved", "Must not overwrite.", decided_at=decided_at
            )
        )
        self.assertEqual(self._count("appeals"), 1)
        self.assertEqual(self._count("decision_history", "appeal_id = ?", (first,)), 1)
        self.assertEqual(
            self._count(
                "student_notifications",
                "event_key LIKE ?",
                (f"appeal:{first}:%",),
            ),
            1,
        )

    # ------------------------------------------------------------------
    # Semester/persistence/legacy: prompt tests 17-20 and migration safety
    # ------------------------------------------------------------------

    def test_17_switching_semester_resets_logical_count_without_deleting_history(self) -> None:
        term_1_id = int(self.term_1["id"])
        violation_ids: list[int] = []
        for offset in range(2):
            detected = DETECTED_AT + timedelta(hours=offset)
            violation_id = self._detect(detected_at=detected)
            violation_ids.append(violation_id)
            self.assertTrue(self._confirm(violation_id, at=detected + timedelta(minutes=5)))

        term_2 = self.db.set_current_academic_term("Semester 2", "2099-2100")
        self.assertIsNotNone(term_2)

        self.assertEqual(self.db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 0)
        self.assertEqual(
            self.db.get_strike_count(
                self.STUDENT_A, "wrong_uniform", semester_id=term_1_id
            ),
            2,
        )
        self.assertEqual(self._count("violations"), len(violation_ids))
        self.assertEqual(self._count("strikes"), len(violation_ids))

        new_id = self._detect(detected_at=DETECTED_AT + timedelta(days=10))
        self.assertEqual(self._violation(new_id)["semester_id"], term_2["id"])

    def test_18_restart_preserves_strike_and_workflow_state(self) -> None:
        violation_id = self._detect()
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        restarted = CBVMSDatabase(self.db_path)
        with patch("database.db_manager.utc_now", return_value=confirmed_at):
            restarted.initialize()

        self.assertEqual(restarted.get_strike_count(self.STUDENT_A, "wrong_uniform"), 1)
        visible = restarted.get_visible_violations_for_student(
            self.STUDENT_A, now=confirmed_at
        )
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["id"], violation_id)
        self.assertEqual(visible[0]["strike_active"], 1)

    def test_19_repeated_expiration_processing_creates_no_duplicate_side_effects(self) -> None:
        violation_ids = [
            self._detect(detected_at=DETECTED_AT + timedelta(minutes=offset))
            for offset in range(3)
        ]
        now = REVIEW_DEADLINE + timedelta(minutes=3)

        first = self.db.process_expired_deadlines(now=now)
        second = self.db.process_expired_deadlines(now=now)
        third = self.db.process_expired_deadlines(now=now + timedelta(days=20))

        self.assertEqual(first["auto_confirmed"], 3)
        self.assertEqual(second["auto_confirmed"], 0)
        self.assertEqual(third["auto_confirmed"], 0)
        self.assertEqual(self._count("strikes"), len(violation_ids))
        self.assertEqual(self._count("strike_events"), 1)
        self.assertEqual(
            self._count("student_notifications"),
            len(violation_ids) + 1,  # one confirmation each + one threshold event
        )
        self.assertEqual(
            self._count("student_notifications", "event_key IS NOT NULL"),
            len(violation_ids) + 1,
        )

    def test_deadline_processor_preserves_dismissal_and_all_appeal_states(self) -> None:
        dismissed_id = self._detect()
        self.assertTrue(
            self.db.dismiss_violation(
                dismissed_id,
                reason="False detection",
                decided_at=DETECTED_AT + timedelta(hours=1),
            )
        )

        appeal_specs = (
            ("wrong_uniform", "Wrong uniform (85%)", "pending"),
            ("earring", "Earring detected (88%)", "approved"),
            ("missing_id", "Missing ID", "rejected"),
        )
        appeal_ids: dict[str, int] = {}
        violation_ids: dict[str, int] = {}
        for offset, (code, display, outcome) in enumerate(appeal_specs, start=1):
            detected_at = DETECTED_AT + timedelta(minutes=offset)
            violation_id = self._detect(
                code=code, display=display, detected_at=detected_at
            )
            confirmed_at = detected_at + timedelta(hours=1)
            self.assertTrue(self._confirm(violation_id, at=confirmed_at))
            appeal_id = self._submit_appeal(
                violation_id,
                at=confirmed_at + timedelta(days=1),
                reason=f"A timely and detailed {outcome} appeal for processor safety.",
            )
            self.assertIsNotNone(appeal_id)
            appeal_ids[outcome] = int(appeal_id)
            violation_ids[outcome] = violation_id

        self.assertTrue(
            self.db.update_appeal_decision(
                appeal_ids["approved"],
                "approved",
                "Evidence accepted.",
                decided_at=DETECTED_AT + timedelta(days=3),
            )
        )
        self.assertTrue(
            self.db.update_appeal_decision(
                appeal_ids["rejected"],
                "rejected",
                "Evidence did not overturn the detection.",
                decided_at=DETECTED_AT + timedelta(days=3),
            )
        )
        counts_before = {
            table: self._count(table)
            for table in ("violations", "strikes", "appeals", "decision_history", "student_notifications")
        }

        late = DETECTED_AT + timedelta(days=30)
        self.assertEqual(
            self.db.process_expired_deadlines(now=late),
            {"auto_confirmed": 0, "appeal_windows_expired": 0},
        )
        self.assertEqual(
            self.db.process_expired_deadlines(now=late + timedelta(days=1)),
            {"auto_confirmed": 0, "appeal_windows_expired": 0},
        )

        self.assertEqual(self._violation(dismissed_id)["status"], DISMISSED)
        self.assertEqual(
            self.db.get_appeal_for_violation(violation_ids["pending"])["status"],
            "pending",
        )
        self.assertEqual(
            self.db.get_appeal_for_violation(violation_ids["approved"])["status"],
            "approved",
        )
        self.assertEqual(
            self.db.get_appeal_for_violation(violation_ids["rejected"])["status"],
            "rejected",
        )
        self.assertEqual(self._strike(violation_ids["pending"])["is_active"], 1)
        self.assertEqual(self._strike(violation_ids["approved"])["is_active"], 0)
        self.assertEqual(self._strike(violation_ids["rejected"])["is_active"], 1)
        for violation_id in violation_ids.values():
            self.assertIsNone(self._violation(violation_id)["appeal_window_closed_at"])
        self.assertEqual(
            {
                table: self._count(table)
                for table in (
                    "violations",
                    "strikes",
                    "appeals",
                    "decision_history",
                    "student_notifications",
                )
            },
            counts_before,
        )

    def test_workflow_records_cannot_be_deleted_from_audit_ledger(self) -> None:
        pending_id = self._detect()

        confirmed_id = self._detect(detected_at=DETECTED_AT + timedelta(minutes=1))
        confirmed_at = DETECTED_AT + timedelta(hours=1)
        self.assertTrue(self._confirm(confirmed_id, at=confirmed_at))
        appeal_id = self._submit_appeal(
            confirmed_id,
            at=confirmed_at + timedelta(days=1),
            reason="This appeal must remain linked to its historical violation.",
        )
        self.assertIsNotNone(appeal_id)

        dismissed_id = self._detect(detected_at=DETECTED_AT + timedelta(minutes=2))
        self.assertTrue(
            self.db.dismiss_violation(
                dismissed_id,
                reason="False detection retained for audit",
                decided_at=DETECTED_AT + timedelta(hours=1),
            )
        )
        counts_before = {
            table: self._count(table)
            for table in ("violations", "strikes", "appeals", "student_notifications")
        }

        self.assertFalse(self.db.delete_violation(confirmed_id))
        self.assertEqual(self.db.delete_violations([pending_id, dismissed_id]), 0)
        self.assertEqual(self.db.delete_all_violations(), 0)

        self.assertEqual(
            {
                table: self._count(table)
                for table in ("violations", "strikes", "appeals", "student_notifications")
            },
            counts_before,
        )
        self.assertIsNotNone(self.db.get_appeal_for_violation(confirmed_id))
        self.assertIsNotNone(self._violation(pending_id))
        self.assertIsNotNone(self._violation(dismissed_id))

    def test_20_legacy_confidence_display_backfills_stable_code_without_crash(self) -> None:
        legacy_db, legacy_path = self._make_legacy_database()
        legacy_db.initialize()

        with legacy_db.connect() as conn:
            rows = conn.execute(
                "SELECT violation_type, violation_code FROM violations ORDER BY id"
            ).fetchall()

        self.assertEqual(rows[0]["violation_type"], "Wrong uniform (82%)")
        self.assertEqual(rows[0]["violation_code"], "wrong_uniform")
        self.assertEqual(rows[1]["violation_code"], "wrong_uniform")
        self.assertTrue(legacy_path.exists())

    def test_legacy_unreviewed_never_auto_confirms_or_floods_student(self) -> None:
        legacy_db, _ = self._make_legacy_database()
        with patch("database.db_manager.utc_now", return_value=DETECTED_AT):
            legacy_db.initialize()

        result = legacy_db.process_expired_deadlines(now=DETECTED_AT + timedelta(days=100))
        with legacy_db.connect() as conn:
            pending = conn.execute(
                "SELECT * FROM violations WHERE status = 'unreviewed'"
            ).fetchone()
        self.assertIsNotNone(pending)
        self.assertIsNone(pending["review_deadline"])
        self.assertEqual(result["auto_confirmed"], 0)
        self.assertEqual(legacy_db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 0)
        # The eager legacy notice remains stored for audit but is not delivered by the portal query.
        self.assertEqual(self._raw_count(legacy_db, "student_notifications"), 1)
        self.assertEqual(legacy_db.get_notifications_for_student(self.STUDENT_A), [])
        self.assertFalse(
            legacy_db.confirm_violation(
                int(pending["id"]), confirmed_at=DETECTED_AT + timedelta(days=1)
            )
        )
        self.assertFalse(
            legacy_db.dismiss_violation(
                int(pending["id"]), decided_at=DETECTED_AT + timedelta(days=1)
            )
        )
        with legacy_db.connect() as conn:
            unchanged = conn.execute(
                "SELECT status FROM violations WHERE id = ?", (pending["id"],)
            ).fetchone()
        self.assertEqual(unchanged["status"], "unreviewed")
        self.assertEqual(self._raw_count(legacy_db, "strikes"), 0)
        self.assertEqual(self._raw_count(legacy_db, "student_notifications"), 1)

    def test_pending_category_does_not_leak_into_student_strike_summary(self) -> None:
        violation_id = self._detect(code="earring", display="Earring detected (91%)")

        pending_codes = {
            item["violation_code"] for item in self.db.get_strike_summary(self.STUDENT_A)
        }
        self.assertNotIn("earring", pending_codes)

        self.assertTrue(self._confirm(violation_id, at=DETECTED_AT + timedelta(days=1)))
        confirmed = {
            item["violation_code"]: item["active_count"]
            for item in self.db.get_strike_summary(self.STUDENT_A)
        }
        self.assertEqual(confirmed["earring"], 1)

    def test_legacy_reviewed_stays_visible_without_retroactive_strike_or_notice(self) -> None:
        legacy_db, _ = self._make_legacy_database()
        with patch("database.db_manager.utc_now", return_value=DETECTED_AT):
            legacy_db.initialize()

        visible = legacy_db.get_visible_violations_for_student(
            self.STUDENT_A, now=DETECTED_AT
        )
        reviewed = [item for item in visible if item["status"] == "reviewed"]

        self.assertEqual(len(reviewed), 1)
        self.assertIsNotNone(reviewed[0]["confirmed_at"])
        self.assertIsNone(reviewed[0]["strike_id"])
        self.assertEqual(legacy_db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 0)
        self.assertEqual(self._raw_count(legacy_db, "student_notifications"), 1)

    def test_legacy_reviewed_seven_day_notice_is_neutralized_on_migration(self) -> None:
        legacy_db, _ = self._make_legacy_database(include_reviewed_notice=True)
        with patch("database.db_manager.utc_now", return_value=DETECTED_AT):
            legacy_db.initialize()

        notices = legacy_db.get_notifications_for_student(self.STUDENT_A)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["title"], "Historical Violation Record")
        self.assertNotIn("7 day", notices[0]["message"].lower())
        self.assertIn("did not create a retroactive strike", notices[0]["message"].lower())
        self.assertEqual(legacy_db.get_strike_count(self.STUDENT_A, "wrong_uniform"), 0)

    def test_unknown_person_is_excluded_from_strikes_appeals_and_student_notices(self) -> None:
        violation_id = self._detect(
            student_id="unknown",
            student_name="Unknown",
            code="unknown_person",
            display="Unknown person",
        )
        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        row = self._violation(violation_id)
        self.assertEqual(row["status"], CONFIRMED)
        self.assertIsNone(row["appeal_deadline"])
        self.assertIsNone(self._strike(violation_id))
        self.assertEqual(self._count("strike_events"), 0)
        self.assertEqual(self._count("student_notifications"), 0)
        self.assertIsNone(
            self._submit_appeal(
                violation_id,
                at=confirmed_at + timedelta(days=1),
                student_id="unknown",
                reason="An unknown identity cannot own or submit a disciplinary appeal.",
            )
        )

    def test_repeated_initialize_is_idempotent(self) -> None:
        violation_id = self._detect()
        initial_terms = self._count("academic_terms")
        initial_deadline = self._violation(violation_id)["review_deadline"]

        with patch("database.db_manager.utc_now", return_value=DETECTED_AT):
            self.db.initialize()
            self.db.initialize()

        self.assertEqual(self._count("academic_terms"), initial_terms)
        self.assertEqual(self._violation(violation_id)["review_deadline"], initial_deadline)
        self.assertEqual(self._count("strikes"), 0)
        self.assertEqual(self._count("student_notifications"), 0)
        with self.db.connect() as conn:
            current_terms = conn.execute(
                "SELECT COUNT(*) AS count FROM academic_terms WHERE is_current = 1"
            ).fetchone()["count"]
        self.assertEqual(current_terms, 1)

    def test_confirmation_reuses_legacy_eager_notice_and_assigns_event_key_once(self) -> None:
        violation_id = self._detect()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO student_notifications
                   (student_id, title, message, violation_id, created_at)
                   VALUES (?, 'Legacy eager notice', 'Pending detection', ?, ?)""",
                (self.STUDENT_A, violation_id, format_db_datetime(DETECTED_AT)),
            )
            conn.commit()

        confirmed_at = DETECTED_AT + timedelta(days=1)
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))
        self.assertTrue(self._confirm(violation_id, at=confirmed_at))

        with self.db.connect() as conn:
            notices = conn.execute(
                "SELECT * FROM student_notifications WHERE violation_id = ?",
                (violation_id,),
            ).fetchall()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["event_key"], f"violation:{violation_id}:confirmed")
        self.assertEqual(notices[0]["is_read"], 0)

    def test_notification_linked_to_missing_violation_never_leaks_to_student(self) -> None:
        notification_id = self.db.insert_notification(
            self.STUDENT_A,
            "Orphaned workflow notice",
            "This linked notice must remain hidden without its violation record.",
            violation_id=999_999,
            created_at=DETECTED_AT,
        )

        self.assertIsNotNone(notification_id)
        self.assertEqual(self._count("student_notifications"), 1)
        self.assertEqual(self.db.get_notifications_for_student(self.STUDENT_A), [])
        self.assertEqual(self.db.get_unread_notification_count(self.STUDENT_A), 0)

    @staticmethod
    def _raw_count(db: CBVMSDatabase, table: str) -> int:
        with db.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _make_legacy_database(
        self, *, include_reviewed_notice: bool = False
    ) -> tuple[CBVMSDatabase, Path]:
        """Create a pre-feature schema containing unreviewed/reviewed history."""

        path = Path(self._tmp.name) / f"legacy-{self.id().rsplit('.', 1)[-1]}.db"
        conn = sqlite3.connect(path)
        try:
            conn.executescript(
                """
                CREATE TABLE students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    course TEXT,
                    year_and_section TEXT,
                    gender TEXT DEFAULT 'Unknown',
                    email TEXT DEFAULT '',
                    encoding BLOB,
                    photo BLOB,
                    enrolled_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE violations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT,
                    student_name TEXT,
                    violation_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    snapshot BLOB,
                    status TEXT NOT NULL DEFAULT 'unreviewed'
                );
                CREATE TABLE student_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    violation_id INTEGER,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            conn.execute(
                """INSERT INTO students
                   (student_id, name, course, year_and_section, encoding, photo)
                   VALUES (?, 'Alice Test', 'BSIT', '3A', ?, ?)""",
                (self.STUDENT_A, b"", b""),
            )
            conn.execute(
                """INSERT INTO violations
                   (student_id, student_name, violation_type, timestamp, status)
                   VALUES (?, 'Alice Test', 'Wrong uniform (82%)',
                           '2020-01-01 00:00:00', 'unreviewed')""",
                (self.STUDENT_A,),
            )
            pending_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """INSERT INTO violations
                   (student_id, student_name, violation_type, timestamp, status)
                   VALUES (?, 'Alice Test', 'Wrong uniform (91%)',
                           '2020-01-02 00:00:00', 'reviewed')""",
                (self.STUDENT_A,),
            )
            reviewed_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """INSERT INTO student_notifications
                   (student_id, title, message, violation_id, created_at)
                   VALUES (?, 'Violation Detected: Wrong Uniform',
                           'Legacy eager notification', ?, '2020-01-01 00:00:00')""",
                (self.STUDENT_A, pending_id),
            )
            if include_reviewed_notice:
                conn.execute(
                    """INSERT INTO student_notifications
                       (student_id, title, message, violation_id, created_at)
                       VALUES (?, 'Violation Detected: Wrong Uniform',
                               'You have 7 days from detection to submit an appeal.',
                               ?, '2020-01-02 00:00:00')""",
                    (self.STUDENT_A, reviewed_id),
                )
            conn.commit()
        finally:
            conn.close()
        return CBVMSDatabase(path), path


if __name__ == "__main__":
    unittest.main(verbosity=2)
