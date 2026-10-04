"""Real SQLite coverage for cancellation guards and separate security events."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from core.discipline import format_db_datetime
from database.db_manager import CBVMSDatabase


class LivePersistenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="cbvms_live_persistence_")
        self.addCleanup(folder.cleanup)
        self.db = CBVMSDatabase(Path(folder.name) / "test.db")
        self.db.initialize(process_deadlines=False)
        self.db.insert_student("S1", "Student One", "BSIT", "3A", b"encoding", b"photo")
        self.db.insert_student("G1", "Graduate One", "BSIT", "4A", b"encoding", b"photo")
        self.db.update_student_details("G1", student_status="Graduate", contacts={},
                                       changed_by="admin", reason="Graduation verified")
        self.observed = datetime(2099, 9, 1, 8, tzinfo=timezone.utc)

    def _count(self, table):
        with self.db.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def _operations(self):
        return (
            ("violations", lambda guard: self.db.log_violation(
                "S1", "Student One", "Suspected uniform violation",
                violation_code="wrong_uniform", detected_at=self.observed,
                snapshot_jpeg=b"selected body", valid_if=guard)),
            ("attendance", lambda guard: self.db.record_attendance(
                "S1", observed_at=self.observed, valid_if=guard)),
            ("premises_entries", lambda guard: self.db.record_premises_entry(
                "G1", observed_at=self.observed, valid_if=guard)),
            ("security_events", lambda guard: self.db.log_security_event(
                "camera-session:track-1", observed_at=self.observed,
                snapshot_jpeg=b"unknown face", valid_if=guard)),
        )

    def test_cancelled_work_is_rejected_for_every_write(self):
        for table, operation in self._operations():
            with self.subTest(table=table):
                guard = Mock(return_value=False)
                self.assertFalse(operation(guard))
                guard.assert_called_once_with()
                self.assertEqual(self._count(table), 0)

    def test_cancellation_before_commit_rolls_back_every_insert(self):
        for table, operation in self._operations():
            with self.subTest(table=table):
                guard = Mock(side_effect=[True, False])
                self.assertFalse(operation(guard))
                self.assertEqual(guard.call_count, 2)
                self.assertEqual(self._count(table), 0)

    def test_guard_is_checked_inside_the_write_transaction(self):
        original_connect = self.db.connect
        for table, operation in self._operations():
            with self.subTest(table=table):
                connection = original_connect()
                observations = []

                def guard():
                    observations.append(connection.in_transaction)
                    return True

                with patch.object(self.db, "connect", return_value=connection):
                    self.assertTrue(operation(guard))
                self.assertEqual(observations, [True, True])
                self.assertEqual(self._count(table), 1)

    def test_guard_exception_rolls_back_the_insert(self):
        for table, operation in self._operations():
            with self.subTest(table=table):
                guard = Mock(side_effect=[True, RuntimeError("session changed")])
                with self.assertRaisesRegex(RuntimeError, "session changed"):
                    operation(guard)
                self.assertEqual(self._count(table), 0)

    def test_cancellation_while_waiting_for_sqlite_lock_is_rejected(self):
        # No production database is touched. Hold a real write lock while a
        # monitor write is queued, then invalidate its source before releasing it.
        cancelled = threading.Event()
        attempting_lock = threading.Event()
        result = []
        errors = []
        original_connect = self.db.connect
        guard = Mock(side_effect=lambda: not cancelled.is_set())

        def traced_connect():
            connection = original_connect()
            connection.set_trace_callback(
                lambda sql: attempting_lock.set() if sql == "BEGIN IMMEDIATE" else None
            )
            return connection

        def worker():
            try:
                result.append(self.db.record_attendance(
                    "S1", observed_at=self.observed, valid_if=guard))
            except Exception as exc:
                errors.append(exc)

        with original_connect() as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with patch.object(self.db, "connect", side_effect=traced_connect):
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                try:
                    self.assertTrue(attempting_lock.wait(2))
                    self.assertEqual(guard.call_count, 0)
                    cancelled.set()
                finally:
                    blocker.rollback()
                    thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result, [False])
        guard.assert_called_once_with()
        self.assertEqual(self._count("attendance"), 0)

    def test_cancelled_updates_preserve_existing_sighting_times(self):
        self.db.record_attendance("S1", observed_at=self.observed)
        self.db.record_premises_entry("G1", observed_at=self.observed)
        newer = self.observed + timedelta(seconds=30)
        self.assertFalse(self.db.record_attendance(
            "S1", observed_at=newer, valid_if=Mock(side_effect=[True, False])))
        self.assertFalse(self.db.record_premises_entry(
            "G1", observed_at=newer, valid_if=Mock(side_effect=[True, False])))
        self.assertEqual(self.db.get_attendance_report()[0]["last_seen"],
                         format_db_datetime(self.observed)+".000000")
        self.assertEqual(self.db.get_premises_entries()[0]["last_seen"],
                         format_db_datetime(self.observed))
        # A valid deduplicated entry still updates last_seen without creating a
        # second entry. The historical boolean means "new entry created".
        self.assertFalse(self.db.record_premises_entry(
            "G1", observed_at=newer, valid_if=lambda: True))
        self.assertEqual(self._count("premises_entries"), 1)
        self.assertEqual(self.db.get_premises_entries()[0]["last_seen"],
                         format_db_datetime(newer))

    def test_security_event_has_evidence_without_disciplinary_effects(self):
        event_id = self.db.log_security_event(
            "camera-1:track-7", observed_at=self.observed,
            snapshot_jpeg=b"frozen face evidence", valid_if=lambda: True)
        with self.db.connect() as conn:
            event = dict(conn.execute("SELECT * FROM security_events WHERE id=?",
                                      (event_id,)).fetchone())
        self.assertEqual(event["presence_id"], "camera-1:track-7")
        self.assertEqual(event["observed_at"], format_db_datetime(self.observed))
        self.assertEqual(event["event_code"], "unknown_person")
        self.assertEqual(event["snapshot"], b"frozen face evidence")
        self.db.process_expired_deadlines(now=self.observed + timedelta(days=30))
        for table in ("violations", "strikes", "strike_events", "attendance",
                      "premises_entries", "student_suspensions", "student_notifications"):
            with self.subTest(table=table):
                self.assertEqual(self._count(table), 0)

    def test_security_migration_is_additive_and_preserves_legacy_unknown_rows(self):
        old_id = self.db.log_violation("unknown", "Unknown", "Unknown Person",
                                       detected_at=self.observed)
        security_id = self.db.log_security_event("new-presence", observed_at=self.observed)
        self.db.initialize(process_deadlines=False)
        self.db.initialize(process_deadlines=False)
        with self.db.connect() as conn:
            legacy = conn.execute("SELECT student_id FROM violations WHERE id=?", (old_id,)).fetchone()
            security = conn.execute("SELECT presence_id FROM security_events WHERE id=?",
                                     (security_id,)).fetchone()
        self.assertEqual(legacy["student_id"], "unknown")
        self.assertEqual(security["presence_id"], "new-presence")
        self.assertEqual(self.db.get_student_by_student_id("S1")["photo"], b"photo")

    def test_security_event_rejects_missing_presence_or_invalid_time(self):
        with self.assertRaises(ValueError):
            self.db.log_security_event(" ")
        with self.assertRaises(ValueError):
            self.db.log_security_event("presence", observed_at="invalid")
        self.assertEqual(self._count("security_events"), 0)


if __name__ == "__main__":
    unittest.main()
