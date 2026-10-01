"""History relationships and existing suspension/strike rules, in temporary DBs."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.discipline import utc_now
from database.db_manager import CBVMSDatabase
from tests.evidence_fixture import picture_evidence
from ui.suspensions_panel import strike_explanation, saved_year_level


class SavedYearLevelTests(unittest.TestCase):
    def test_year_and_section_formats_and_unknown_values(self):
        for expected, values in {
            "1st Year": [1, "1", "1A", "1st Year", "First Year", "first year - section A"],
            "2nd Year": [2, "2B", "2ND YEAR - A", "Second Year", "Year 2"],
            "3rd Year": [3, "3A", "3rd Year", "Third Year", "3 / B"],
            "4th Year": [4, "4A", "4th Year", "Fourth Year - C"],
            None: [None, "", "   ", "Unknown", "2023-00883", "2023", "5A"],
        }.items():
            for value in values:
                with self.subTest(value=value):
                    self.assertEqual(saved_year_level(value), expected)


class SuspensionHistoryTests(unittest.TestCase):
    SID = "2023-00883"
    OTHER = "2023-883"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / "test.db")
        self.db.initialize()
        for sid in (self.SID, self.OTHER):
            self.db.insert_student(sid, "Same Name", "BSIT", "3A", b"", b"")

    def violation(self, sid=None, status="pending_review", code="wrong_uniform"):
        return self.db.log_violation(sid or self.SID, "Same Name", code, status=status)

    def test_legacy_and_pending_history_is_visible_without_fabricating_strikes(self):
        # Reproduce the reported record mix without using or rewriting production data.
        with self.db.connect() as conn:
            legacy = conn.execute("SELECT id FROM academic_terms WHERE school_year='Unassigned'").fetchone()[0]
            for i in range(33):
                conn.execute("""INSERT INTO violations
                    (student_id, student_name, violation_type, violation_code, status, semester_id)
                    VALUES (?, 'Same Name', 'Wrong uniform (82%)', 'wrong_uniform', ?, ?)""",
                    (self.SID, "reviewed" if i == 0 else "unreviewed", legacy))
        self.violation()
        self.violation()
        self.violation(self.OTHER, "confirmed")
        rows = self.db.get_discipline_history_for_student(self.SID)
        self.assertEqual(len(rows), 35)
        self.assertEqual({row["student_id"] for row in rows}, {self.SID})
        self.assertEqual(sum(row["status"] == "pending_review" for row in rows), 2)
        self.assertTrue(all(row["strike_id"] is None for row in rows))
        self.assertEqual(self.db.get_strike_count(self.SID, "wrong_uniform"), 0)
        self.assertEqual(self.db.get_suspension_history(self.SID), [])
        self.assertEqual(len(self.db.get_discipline_history_for_student(self.OTHER)), 1)

    def test_threshold_assignment_appeal_and_semester_remain_separate(self):
        ids = [self.violation(status="confirmed") for _ in range(3)]
        self.violation(status="dismissed")
        self.violation(code="earring", status="confirmed")
        self.db.process_expired_deadlines(now=utc_now()+timedelta(days=6))
        # A legacy pending appeal/strike conflict can be resolved by a human decision.
        with self.db.connect() as conn:
            conn.execute("INSERT INTO appeals (violation_id,student_id,reason) VALUES (?,?,?)",
                (ids[0],self.SID,'Historical appeal already submitted'))
        self.assertEqual(self.db.get_strike_count(self.SID, "wrong_uniform"), 3)
        self.assertEqual(self.db.get_strike_count(self.SID, "earring"), 1)
        self.assertTrue(any(row["action_required"] for row in self.db.get_strike_summary(self.SID)))
        self.assertEqual(self.db.get_suspension_history(self.SID), [])
        args = dict(reason="OSA reviewed the cases", starts_at=utc_now()-timedelta(minutes=1),
                    ends_at=None, imposed_by="osa", violation_id=ids[0])
        suspension_id = self.db.impose_suspension(self.SID, **args)
        with self.assertRaises(ValueError):
            self.db.impose_suspension(self.SID, **args)
        for vid in ids:
            self.db.confirm_violation(vid)
        self.assertEqual(len(self.db.get_suspension_history(self.SID)), 1)
        appeal = self.db.get_appeal_for_violation(ids[0])["id"]
        self.assertTrue(self.db.update_appeal_decision(appeal, "approved", "Evidence accepted", decided_by="admin"))
        self.assertEqual(self.db.get_strike_count(self.SID, "wrong_uniform"), 2)
        self.assertEqual(self.db.get_active_suspension(self.SID)["id"], suspension_id)
        rows = {row["id"]: row for row in self.db.get_discipline_history_for_student(self.SID)}
        self.assertEqual(rows[ids[0]]["appeal_status"], "approved")
        self.assertEqual(rows[ids[0]]["strike_active"], 0)
        self.db.set_current_academic_term("Semester 2", "2099-2100")
        term_id = self.db.get_current_academic_term()["id"]
        self.assertEqual(self.db.get_strike_count(self.SID, "wrong_uniform"), 0)
        self.assertEqual(strike_explanation(rows[ids[1]], term_id), "Active strike in a previous semester")
        self.assertEqual(len(self.db.get_discipline_history_for_student(self.SID)), 5)

    def test_missing_actor_and_cross_student_violation_are_rejected(self):
        foreign = self.violation(self.OTHER)
        args = dict(reason="Reviewed", starts_at=utc_now(), ends_at=None, imposed_by="osa")
        with self.assertRaises(ValueError):
            self.db.impose_suspension(self.SID, **dict(args, imposed_by=""))
        with self.assertRaises(ValueError):
            self.db.impose_suspension(self.SID, **dict(args, violation_id=foreign))
        self.assertEqual(self.db.get_suspension_history(self.SID), [])

    def test_student_login_never_routes_to_administrator_dashboard(self):
        from auth.login import run_login
        from ui.student_portal import _NAV_ITEMS
        student_login = MagicMock()
        student_login.result = {"role": "student", "student_id": self.SID,
                                "display_name": "Same Name", "username": "learner"}
        portal = MagicMock(logged_out=False)
        auth = MagicMock()
        with patch("auth.login.CBVMSLoginWindow", return_value=student_login), \
                patch("ui.student_portal.StudentPortal", return_value=portal) as factory:
            self.assertIsNone(run_login(auth))
            factory.assert_called_once_with(student_id=self.SID, display_name="Same Name", database=auth._db)
        self.assertNotIn("suspensions", [key for key, _ in _NAV_ITEMS])
        admin_login = MagicMock()
        admin_login.result = {"role": "admin", "username": "osa"}
        with patch("auth.login.CBVMSLoginWindow", return_value=admin_login):
            self.assertEqual(run_login(MagicMock()), "osa")


if __name__ == "__main__":
    unittest.main()
