"""Exact acquisition replay protection; all writes use disposable SQLite."""
from dataclasses import replace
import sqlite3
import unittest

from database.db_manager import CBVMSDatabase
from database.security_events import migrate_security_events
from tests import test_attendance as attendance_fixtures
from tests.test_live_pipeline import PipelineFixture, detection


class EvidenceIdentityTests(unittest.TestCase):
    setUp = attendance_fixtures.AttendanceTests.setUp

    def test_same_acquisition_changed_tracker_is_one_durable_event(self):
        fx = PipelineFixture()
        fx.processor.database = self.db
        fx.recognizer.recognize_faces.return_value = [detection('')]
        result = fx.confirmed()
        fx.persist(result)
        original = self.db.query_unknown_sightings(username='admin')['rows'][0]
        retry = replace(result, assessments=(replace(result.assessments[0],
                                                     presence_id='person:1:999', track_id=999),))
        # Restart the DB wrapper and discard the bounded in-memory cooldown cache.
        fx.processor.database = CBVMSDatabase(self.path)
        fx.processor.unknown_cooldowns.clear()
        fx.persist(retry)
        self.assertEqual(self.db.query_unknown_sightings(username='admin')['rows'], [original])
        with self.db.connect() as conn:
            self.assertTrue(conn.execute('SELECT evidence_key FROM security_events').fetchone()[0])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

        # Another face location in that frame is a distinct acquired face.
        other = replace(retry, assessments=(replace(retry.assessments[0],
            presence_id='person:1:1000', track_id=1000, face_box=(350, 20, 400, 90)),))
        fx.persist(other)
        self.assertEqual(self.db.query_unknown_sightings(username='admin')['count'], 2)
        # Same crop pixels on a later acquired frame/new passage remain eligible.
        later = fx.analyze(5)
        later = replace(later, assessments=(replace(later.assessments[0],
            presence_id='person:1:1001', track_id=1001),))
        fx.persist(later)
        self.assertEqual(self.db.query_unknown_sightings(username='admin')['count'], 3)

    def test_optional_key_migration_preserves_legacy_duplicates_and_is_repeatable(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE security_events(id INTEGER PRIMARY KEY, presence_id TEXT, '
                     'event_code TEXT, observed_at TEXT, snapshot BLOB)')
        for identity in (1, 2):
            conn.execute('INSERT INTO security_events VALUES(?,?,?,?,?)',
                         (identity, 'same', 'unknown_person', '2026-01-01', b'original'))
        migrate_security_events(conn)
        migrate_security_events(conn)
        self.assertEqual(conn.execute('SELECT id,snapshot,evidence_key FROM security_events').fetchall(),
                         [(1, b'original', None), (2, b'original', None)])
        conn.execute("UPDATE security_events SET evidence_key='exact-frame' WHERE id=1")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE security_events SET evidence_key='exact-frame' WHERE id=2")

    def test_secondary_retry_still_obeys_cancel_and_staff_boundaries(self):
        first = self.db.log_security_event('a', event_key='a', evidence_key='frame')
        self.assertIsNone(self.db.log_security_event('b', event_key='b', evidence_key='frame',
                                                    valid_if=lambda: False))
        self.db.student_session_ref = object()
        with self.assertRaises(PermissionError):
            self.db.log_security_event('b', event_key='b', evidence_key='frame')
        self.db.student_session_ref = None
        self.assertEqual(self.db.log_security_event('b', event_key='b', evidence_key='frame'), first)
