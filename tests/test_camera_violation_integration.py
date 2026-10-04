"""Camera assessments through the real administrative-review persistence boundary.

The dashboard's former independent logging/checking helpers no longer own this
workflow. Exercise LiveProcessor and an isolated SQLite database instead.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.db_manager import CBVMSDatabase
from tests.test_live_pipeline import PipelineFixture, detection


class CameraViolationIntegrationTests(unittest.TestCase):
    def database(self):
        directory = tempfile.TemporaryDirectory(prefix="cbvms_camera_integration_")
        self.addCleanup(directory.cleanup)
        database = CBVMSDatabase(Path(directory.name) / "cbvms.db")
        database.initialize()
        database.insert_student(student_id="S-1", name="Student S-1", course="BSIT",
                                year_and_section="3A", encoding=b"", photo=b"", gender="Male")
        return database

    def fixture(self):
        fixture = PipelineFixture()
        fixture.database = self.database()
        fixture.processor.database = fixture.database
        return fixture

    def test_attendance_records_recognized_presence_without_violation_and_throttles(self):
        fixture = self.fixture()
        fixture.trainer.predict_proba.return_value = {"correct_uniform": .9}
        fixture.recognizer.recognize_faces.return_value = [detection(), detection("", 350, embedding=1)]
        with patch.object(fixture.database, "record_attendance", wraps=fixture.database.record_attendance) as record:
            fixture.persist(fixture.analyze(1))
            record.assert_not_called()  # one frame cannot establish identity
            fixture.persist(fixture.analyze(2))
            fixture.persist(fixture.analyze(3))
            self.assertEqual(record.call_count, 1)
            self.assertEqual(record.call_args.args, ("S-1",))
            # Cooldown expires independently from temporary tracking identity.
            fixture.processor.attendance_cooldowns = {key: fixture.now-301 for key in fixture.processor.attendance_cooldowns}
            fixture.persist(fixture.analyze(4))
            self.assertEqual(record.call_count, 2)
        report = fixture.database.get_attendance_report()
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["student_id"], "S-1")
        self.assertEqual(report[0]["sighting_count"], 1)  # SQLite still deduplicates after the cache expires.
        self.assertEqual(fixture.database.get_violations_for_student("S-1"), [])

    def test_accepted_assessment_persists_pending_review_with_appeal_and_student_notice_without_strike(self):
        fixture = self.fixture()
        result = fixture.confirmed()
        fixture.persist(result)
        rows = fixture.database.get_violations_for_student("S-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["student_id"], "S-1")
        self.assertEqual(rows[0]["status"], "pending_review")
        self.assertEqual(rows[0]["violation_code"], "wrong_uniform")
        self.assertTrue(rows[0]["appeal_deadline"])
        self.assertTrue(fixture.database.get_appeal_eligibility(rows[0]["id"], "S-1")["eligible"])
        self.assertIsInstance(rows[0]["snapshot"], bytes)
        self.assertTrue(rows[0]["snapshot"])
        self.assertEqual(fixture.database.get_strike_count("S-1", "wrong_uniform"), 0)
        self.assertEqual(len(fixture.database.get_notifications_for_student("S-1")), 1)
        with fixture.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM student_notifications").fetchone()[0], 1)
        fixture.notifier.notify.assert_called_once()
        self.assertEqual(fixture.notifier.notify.call_args.args, ("Student S-1", "Suspected uniform violation"))
        self.assertEqual(fixture.processor.write_count, 1)

    def test_live_assessment_maps_exact_fixture_id_to_immediate_portal_appeal(self):
        fixture = self.fixture()
        sid = "2023-00883"
        fixture.database.insert_student(sid,"Fixture Student","BSIT","3A",b"",b"")
        fixture.recognizer.recognize_faces.return_value = [detection(sid)]
        fixture.persist(fixture.confirmed())
        rows = fixture.database.get_visible_violations_for_student(sid)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["can_appeal"])
        self.assertTrue(rows[0]["snapshot"])
        self.assertEqual(fixture.database.get_visible_violations_for_student("2023-883"), [])
        self.assertEqual(fixture.database.get_visible_violations_for_student("S-1"), [])
        self.assertEqual(fixture.database.get_strike_count(sid, "wrong_uniform"), 0)

    def test_one_assessment_persists_each_category_with_independent_cooldown(self):
        fixture = self.fixture()
        fixture.trainer.is_trained.side_effect = lambda module: True
        fixture.trainer.predict.return_value = ("with_earring", .9)
        result = fixture.confirmed()
        self.assertEqual(result.assessments[0].accepted_categories, ("wrong_uniform", "earring"))
        fixture.persist(result)
        fixture.persist(fixture.analyze(5))
        rows = fixture.database.get_violations_for_student("S-1")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["violation_code"] for row in rows}, {"wrong_uniform", "earring"})
        self.assertTrue(all(row["status"] == "pending_review" for row in rows))
        self.assertEqual(set(fixture.processor.cooldowns), {("S-1", "wrong_uniform"), ("S-1", "earring")})
        self.assertEqual(fixture.notifier.notify.call_count, 2)

    def test_unknown_people_are_security_events_separate_from_disciplinary_records(self):
        fixture = self.fixture()
        fixture.recognizer.recognize_faces.return_value = [detection("", 80), detection("", 350, embedding=1)]
        result = fixture.analyze(1)
        fixture.persist(result)
        fixture.persist(fixture.analyze(2))
        with fixture.database.connect() as connection:
            events = connection.execute("SELECT * FROM security_events ORDER BY id").fetchall()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM violations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0], 0)
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["presence_id"], events[1]["presence_id"])
        self.assertTrue(all(event["snapshot"] for event in events))

    def test_failed_insert_remains_retryable_and_success_is_not_duplicated(self):
        fixture = self.fixture()
        result = fixture.confirmed()
        original = fixture.database.log_violation
        failures = [True]
        def write(**kwargs):
            if failures:
                failures.pop()
                raise RuntimeError("database busy")
            return original(**kwargs)
        with patch.object(fixture.database, "log_violation", side_effect=write) as record:
            fixture.persist(result)
            self.assertNotIn(("S-1", "wrong_uniform"), fixture.processor.cooldowns)
            fixture.notifier.notify.assert_not_called()
            fixture.persist(fixture.analyze(5))
            fixture.persist(fixture.analyze(6))
            self.assertEqual(record.call_count, 2)
        self.assertEqual(len(fixture.database.get_violations_for_student("S-1")), 1)
        fixture.notifier.notify.assert_called_once()

    def test_database_rechecks_current_disciplinary_eligibility_after_analysis(self):
        fixture = self.fixture()
        result = fixture.confirmed()
        # Standing changes after recognition, before the transaction commits.
        with fixture.database.connect() as connection:
            connection.execute("UPDATE students SET student_status='Graduate' WHERE student_id='S-1'")
        fixture.persist(result)
        self.assertEqual(fixture.database.get_violations_for_student("S-1"), [])
        self.assertEqual(fixture.database.query_attendance(view="events")["count"], 1)
        self.assertEqual(fixture.database.query_attendance(view="events")["rows"][0]["student_status"], "Enrolled")
        self.assertFalse(fixture.processor.cooldowns)
        fixture.notifier.notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
