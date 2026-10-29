"""Explicit current-monitor-session activity ownership and exact log reads."""
import json
import unittest
from dataclasses import replace

from tests import test_attendance as attendance_fixtures


class MonitorSessionActivityTests(unittest.TestCase):
    def setUp(self):
        attendance_fixtures.AttendanceTests.setUp(self)

    def fact(self, *args, **kwargs):
        return attendance_fixtures.AttendanceTests.fact(self, *args, **kwargs)

    def save(self, *args, **kwargs):
        return attendance_fixtures.AttendanceTests.save(self, *args, **kwargs)

    def test_session_query_excludes_legacy_and_other_sessions_without_date_guessing(self):
        current = 'monitor-current-uuid'
        other = 'monitor-previous-uuid'
        fact = self.fact(monitor_session_id=current)
        self.assertTrue(self.save(fact))
        self.assertTrue(self.save(self.fact(600, monitor_session_id=other)))
        self.db.log_security_event('unknown-current', event_key='u-current',
            source_id='gate-1', source_label='Gate 1',
            evidence_metadata=json.dumps({'monitor_generation': 1, 'camera_generation': 1,
                                          'frame_id': ['gate-1', 8]}),
            monitor_session_id=current)
        unknown_id = self.db.query_unknown_sightings(username='admin')['rows'][0]['id']
        self.db.log_security_event('unknown-prior', event_key='u-prior',
            source_id='gate-1', source_label='Gate 1', monitor_session_id=other)
        self.db.log_security_event('unknown-old', event_key='u-old',
            source_id='gate-1', source_label='Gate 1')
        self.db.log_violation(self.sid, 'Student', 'wrong_uniform', b'jpeg',
            detected_at=self.when, evidence_provenance={
                'captured_at': self.when.isoformat(), 'camera_session': 'gate-1',
                'frame_id': ['gate-1', 8], 'face_box': [10, 10, 20, 20],
                'source_id': 'gate-1', 'source_label': 'Gate 1',
                'presence_id': fact.presence_id, 'monitor_generation': 1,
                'camera_generation': 1}, monitor_session_id=current)

        # Repeat additive initialization with populated legacy and session rows.
        self.db.initialize(process_deadlines=False)

        rows = self.db.query_monitor_activity(username='admin',
                                               monitor_session_id=current, limit=10)
        self.assertEqual({row['event_type'] for row in rows},
                         {'attendance', 'unknown', 'violation'})
        self.assertEqual({row['monitor_session_id'] for row in rows}, {current})
        attendance = next(row for row in rows if row['event_type'] == 'attendance')
        self.assertEqual(attendance['id'], 1)
        self.assertEqual(self.db.query_attendance_event_by_id(
            username='admin', event_id=attendance['id'])['id'], attendance['id'])
        self.assertIsNone(self.db.query_attendance_event_by_id(username='admin', event_id=999999))
        exact_unknown = self.db.query_unknown_sighting_by_id(username='admin', event_id=unknown_id)
        self.assertEqual(exact_unknown['id'], unknown_id)
        self.assertIsNone(self.db.query_unknown_sighting_by_id(username='admin', event_id=999999))

    def test_exact_session_is_required_and_query_remains_staff_only(self):
        for value in ('', ' '):
            with self.assertRaises(ValueError):
                self.db.query_monitor_activity(username='admin', monitor_session_id=value)
        with self.assertRaises(PermissionError):
            self.db.query_monitor_activity(username='student', monitor_session_id='current')
        self.db.student_session_ref = object()
        with self.assertRaises(PermissionError):
            self.db.query_monitor_activity(username='admin', monitor_session_id='current')

        with self.assertRaises(PermissionError):
            self.db.query_unknown_sighting_by_id(username='admin', event_id=1)

    def test_restarted_frame_sequence_does_not_reuse_old_session_receipt(self):
        import hashlib
        legacy = self.fact()
        expected = hashlib.sha256(json.dumps([legacy.student_id, legacy.session_id, legacy.frame_id],
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(legacy.key, expected)
        first = replace(legacy, session_id='first-camera-session', monitor_session_id='first')
        later = replace(self.fact(600), frame_id=first.frame_id, session_id='second-camera-session', monitor_session_id='second')
        self.assertNotEqual(first.key, later.key)
        self.assertTrue(self.save(first))
        self.assertTrue(self.save(later))
        self.assertFalse(self.save(later))
        self.assertEqual(len(self.db.query_monitor_activity(username='admin', monitor_session_id='second')), 1)

    def test_live_producer_assigns_monitor_and_source_session_without_policy_changes(self):
        from unittest.mock import MagicMock, patch
        from tests.test_live_pipeline import PipelineFixture, detection
        sessions = []
        for _ in range(2):
            fx = PipelineFixture()
            fx.processor.attendance_writer = MagicMock()
            fx.recognizer.recognize_faces.return_value = [detection('S1')]
            result = fx.confirmed()
            with patch('core.live_pipeline.time.monotonic', side_effect=lambda: fx.now):
                fx.processor.submit_attendance(result)
            writer = fx.processor.attendance_writer
            self.assertTrue(writer.offer.called)
            fact = writer.offer.call_args.args[0]
            self.assertEqual(fact.monitor_session_id, fx.processor.security_session)
            self.assertEqual(json.loads(fact.session_id)[0], fx.processor.security_session)
            sessions.append(fact.session_id)
        self.assertNotEqual(*sessions)

    def test_historical_updates_and_foreign_sessions_never_join_current_session(self):
        old = self.db.log_security_event('old', event_key='old', observed_at='2026-10-27T00:00:00+00:00')
        remote = self.db.log_security_event('remote', event_key='remote', monitor_session_id='other-desktop')
        current = self.db.log_security_event('current', event_key='current', monitor_session_id='current')
        with self.db.connect() as conn:
            conn.execute("UPDATE security_events SET observed_at='2026-10-28T12:00:00+00:00', snapshot=? WHERE id IN (?,?)",
                         (b'updated historical evidence', old, remote))
        self.db.initialize(process_deadlines=False)
        self.db.initialize(process_deadlines=False)
        rows = self.db.query_monitor_activity(username='admin', monitor_session_id='current')
        self.assertEqual([row['id'] for row in rows], [current])
        with self.db.connect() as conn:
            self.assertIsNone(conn.execute('SELECT monitor_session_id FROM security_events WHERE id=?', (old,)).fetchone()[0])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM security_events').fetchone()[0], 3)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
