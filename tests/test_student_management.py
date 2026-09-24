"""Student standing, entry-only monitoring, and suspension regression coverage."""
import sqlite3
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
from database.db_manager import CBVMSDatabase
from core.student_status import validate_contacts
from ui.dashboard import CBVMSDashboard
from core.tracker import FaceTracker


class StudentManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / "test.db")
        self.db.initialize()
        self.pk = self.db.insert_student("S1", "Student One", "BSIT", "3A", b"", b"")
        self.now = datetime(2099, 1, 1, tzinfo=timezone.utc)

    def change_status(self, status):
        self.db.update_student_details("S1", student_status=status, contacts={},
                                       changed_by="osa.person", reason="Updated enrollment")

    def suspend(self, end=None):
        return self.db.impose_suspension("S1", reason="Reviewed consequence", starts_at=self.now,
                                        ends_at=end, imposed_by="osa.person")

    def test_additive_migration_keeps_old_data_and_is_repeatable(self):
        legacy = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(legacy) as conn:
            conn.execute("""CREATE TABLE students (id INTEGER PRIMARY KEY, student_id TEXT UNIQUE,
                name TEXT, course TEXT, year_and_section TEXT, gender TEXT, email TEXT,
                encoding BLOB, photo BLOB, enrolled_at TEXT)""")
            conn.execute("INSERT INTO students VALUES (1, 'OLD', 'Old Student', 'BSIT', '1A', 'Unknown', 'old@example.com', X'0102', X'0304', '2020-01-01')")
        conn.close()
        db = CBVMSDatabase(legacy)
        db.initialize()
        db.initialize()
        old = db.get_student_by_student_id("OLD")
        self.assertEqual(old["student_status"], "Enrolled")
        self.assertEqual(old["email"], "old@example.com")
        self.assertEqual(old["photo"], b"\x03\x04")
        self.assertEqual(old["mobile_number"], "")

    def test_status_changes_and_contacts_are_audited_and_validated(self):
        self.db.update_student_details("S1", student_status="Graduate",
            contacts={"mobile_number": "09171234567"},
            changed_by="osa.person", reason="Graduated")
        student = self.db.get_student(self.pk)
        self.assertEqual(student["mobile_number"], "09171234567")
        self.assertEqual(self.db.get_student_status_history("S1")[0]["changed_by"], "osa.person")
        for contacts in ({"email": "bad"}, {"mobile_number": "letters"}):
            with self.assertRaises(ValueError):
                validate_contacts(contacts)
        with self.assertRaises(ValueError):
            self.db.update_student_details("S1", student_status="Visitor", contacts={}, changed_by="osa", reason="x")

    def test_entry_only_status_blocks_attendance_and_new_violations(self):
        for status in ("Graduate", "Unenrolled"):
            self.change_status(status)
            self.assertFalse(self.db.record_attendance("S1"))
            self.assertIsNone(self.db.log_violation("S1", "Student One", "wrong_uniform"))
        self.assertTrue(self.db.record_premises_entry("S1", observed_at=self.now))
        self.assertFalse(self.db.record_premises_entry("S1", observed_at=self.now + timedelta(seconds=1)))
        self.assertEqual(self.db.get_premises_entries()[0]["student_status"], "Unenrolled")
        self.change_status("Enrolled")
        self.assertTrue(self.db.record_attendance("S1"))
        self.assertFalse(self.db.record_premises_entry("S1"))
        self.assertIsNotNone(self.db.log_violation("S1", "Student One", "wrong_uniform"))

    def test_self_registration_needs_verification_not_unenrolled(self):
        self.db.insert_student("S2", "New Student", "BSIT", "1A", b"", b"", registration_pending=True)
        self.assertFalse(self.db.record_attendance("S2"))
        self.assertFalse(self.db.record_premises_entry("S2"))
        self.assertIsNone(self.db.log_violation("S2", "New Student", "wrong_uniform"))
        with self.assertRaises(ValueError):
            self.db.update_student_details("S2", student_status="Unenrolled", contacts={},
                                           changed_by="osa", reason="Not verified")
        self.db.update_student_details("S2", student_status="Enrolled", contacts={},
            changed_by="osa", reason="Checked school enrollment", verify_registration=True)
        self.assertTrue(self.db.record_attendance("S2"))

    def test_suspension_exact_start_and_end_and_history(self):
        end = self.now + timedelta(days=1)
        self.suspend(end)
        self.assertIsNone(self.db.get_active_suspension("S1", now=self.now - timedelta(seconds=1)))
        self.assertIsNotNone(self.db.get_active_suspension("S1", now=self.now))
        self.assertIsNotNone(self.db.get_active_suspension("S1", now=end - timedelta(seconds=1)))
        self.assertIsNone(self.db.get_active_suspension("S1", now=end))
        self.assertEqual(len(self.db.get_suspension_history("S1")), 1)
        self.assertEqual(self.db.get_student(self.pk)["student_status"], "Enrolled")

    def test_indefinite_suspension_lift_and_overlap(self):
        sid = self.suspend()
        with self.assertRaises(ValueError):
            self.suspend(self.now + timedelta(days=2))
        self.assertIsNotNone(self.db.get_active_suspension("S1", now=self.now + timedelta(days=365)))
        with patch("database.student_management.utc_now", return_value=self.now):
            self.assertTrue(self.db.lift_suspension(sid, lifted_by="osa.second", reason="Cleared"))
            self.assertFalse(self.db.lift_suspension(sid, lifted_by="osa.second", reason="Again"))
        self.assertIsNone(self.db.get_active_suspension("S1", now=self.now))
        self.assertEqual(self.db.get_suspension_history("S1")[0]["lifted_by"], "osa.second")

    def test_suspension_requires_reason_valid_dates_and_owned_violation(self):
        for kwargs in (dict(reason=""), dict(ends_at=self.now), dict(violation_id=999)):
            values = dict(reason="Review", starts_at=self.now, ends_at=None, imposed_by="osa")
            values.update(kwargs)
            with self.assertRaises(ValueError):
                self.db.impose_suspension("S1", **values)

    def test_existing_appeal_and_strike_survive_status_change(self):
        vid = self.db.log_violation("S1", "Student One", "wrong_uniform", detected_at=self.now)
        self.assertTrue(self.db.confirm_violation(vid, confirmed_at=self.now))
        with patch("database.db_manager.utc_now", return_value=self.now):
            from tests.evidence_fixture import picture_evidence
            aid = self.db.insert_appeal(vid, "S1", "Please review the recorded evidence.",
                                      evidence=picture_evidence())
        self.change_status("Graduate")
        self.assertEqual(self.db.get_appeal_for_violation(vid)["status"], "pending")
        self.assertTrue(self.db.update_appeal_decision(aid, "approved", "Evidence reviewed",
            decided_by="osa.person", decided_at=self.now + timedelta(hours=1)))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT is_active FROM strikes WHERE violation_id=?", (vid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM violations WHERE id=?", (vid,)).fetchone()[0], 1)

    def test_continuous_presence_is_one_entry_and_return_is_another(self):
        self.change_status("Graduate")
        self.assertTrue(self.db.record_premises_entry("S1", observed_at=self.now))
        for minute in (4, 8, 12):
            self.assertFalse(self.db.record_premises_entry("S1", observed_at=self.now + timedelta(minutes=minute)))
        self.assertEqual(len(self.db.get_premises_entries()), 1)
        self.assertTrue(self.db.record_premises_entry("S1", observed_at=self.now + timedelta(minutes=18)))
        self.assertEqual(len(self.db.get_premises_entries()), 2)

    def test_camera_reads_status_changes_and_suspension_without_restart(self):
        harness = types.SimpleNamespace(_database=self.db)
        det = {"matched": True, "student_id": "S1", "name": "Student One"}
        CBVMSDashboard._refresh_student_standing(harness, [det])
        self.assertTrue(det["discipline_eligible"])
        self.change_status("Graduate")
        self.suspend()
        with patch("database.student_management.utc_now", return_value=self.now):
            CBVMSDashboard._refresh_student_standing(harness, [det])
            CBVMSDashboard._record_attendance(harness, [det])
        self.assertFalse(det["discipline_eligible"])
        self.assertIn("Suspended", det["suspension_tag"])
        self.assertIsNotNone(self.db.get_premises_entries()[0]["suspension_id"])
        self.assertEqual(self.db.get_attendance_report(), [])

    def test_non_enrolled_camera_detection_skips_classifiers(self):
        trainer = MagicMock()
        harness = types.SimpleNamespace(_trainer=trainer, _person_detector=None,
            _checker=types.SimpleNamespace(check_uniform=False, check_earring=True),
            _uniform_ema={}, _log_db=MagicMock())
        det = {"matched": True, "student_id": "S1", "discipline_eligible": False}
        CBVMSDashboard._check_violations(harness, [det], np.zeros((40, 40, 3), dtype=np.uint8))
        trainer.predict.assert_not_called()
        harness._log_db.assert_not_called()

    def test_tracker_clears_old_violation_overlay_when_student_graduates(self):
        tracker = FaceTracker(min_hits=1)
        tracker.update([[0, 0, 30, 30]])
        tracker.assign_identities([dict(box=[0, 0, 30, 30], matched=True, student_id="S1",
            uniform_label="wrong_uniform", uniform_conf=0.9, violation="Wrong uniform")])
        tracker.assign_identities([dict(box=[0, 0, 30, 30], matched=True, student_id="S1",
            student_status="Graduate", discipline_eligible=False, suspension_tag="Suspended")])
        tr = tracker.renderable()[0]
        self.assertIsNone(tr.stable_uniform_label)
        self.assertIsNone(tr.violation)
        self.assertEqual(tr.suspension_tag, "Suspended")


if __name__ == "__main__":
    unittest.main()
