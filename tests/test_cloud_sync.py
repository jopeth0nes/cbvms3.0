import base64
import json
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch

from core.cloud_sync import apply_action, student_snapshot
from auth.passwords import hash_password, verify_password
from core.discipline import utc_now, format_db_datetime
from database.db_manager import CBVMSDatabase
from tests.evidence_fixture import picture_evidence


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('core.email_sender.dispatch_discipline_notice')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CBVMSDatabase(Path(self.temp.name) / 'test.db')
        self.db.initialize()
        for sid in ('S1', 'S2'):
            self.db.insert_student(sid, sid, 'BSIT', '3A', b'biometric-secret', b'photo-secret',
                account_username=sid, account_password='password', email=sid + '@example.com')

    def action(self, kind, payload, aid='cloud-action-1'):
        return {'id': aid, 'student_id': 'S1', 'kind': kind, 'payload': payload,
                'created_at': utc_now().isoformat()}

    def test_export_excludes_biometrics_credentials_and_other_students(self):
        self.db.log_violation('S2', 'S2', 'wrong_uniform')
        row = self.db.get_student_by_student_id('S1')
        data = student_snapshot(self.db, row)
        raw = json.dumps(data)
        self.assertNotIn('biometric-secret', raw)
        self.assertNotIn('password_hash', raw)
        self.assertNotIn('encoding', data['snapshot']['profile'])
        self.assertEqual(data['snapshot']['violations'], [])

    def test_report_replay_is_exactly_once(self):
        action = self.action('reports', {'title': 'Test report', 'description': 'Details', 'category': 'System Bug'})
        first = apply_action(self.db, action)
        self.assertEqual(apply_action(self.db, action), first)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM system_reports').fetchone()[0], 1)

    def test_cloud_password_change_imports_hash_and_revokes_sessions_exactly_once(self):
        account = self.db.verify_student_account('S1', 'password')
        session = self.db.create_student_session(account)
        with self.db.connect() as conn:
            old = conn.execute('SELECT password_hash,credential_version FROM student_accounts WHERE student_id=?', ('S1',)).fetchone()
        encoded = hash_password('My personal cloud passphrase')
        action = self.action('password', {'password_hash': encoded, 'expected_hash': old['password_hash']})
        self.assertEqual(apply_action(self.db, action)['status'], 'applied')
        self.assertEqual(apply_action(self.db, action)['status'], 'applied')
        with self.db.connect() as conn:
            updated = conn.execute('SELECT * FROM student_accounts WHERE student_id=?', ('S1',)).fetchone()
            self.assertTrue(verify_password('My personal cloud passphrase', updated['password_hash']))
            self.assertEqual(updated['credential_version'], old['credential_version'] + 1)
            self.assertFalse(updated['must_change_password'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM student_sessions').fetchone()[0], 0)

    def test_cloud_password_change_cannot_overwrite_a_newer_desktop_reset(self):
        action = self.action('password', {'password_hash': hash_password('A new personal password'), 'expected_hash': 'stale hash'})
        self.assertEqual(apply_action(self.db, action)['status'], 'rejected')
        self.assertIsNotNone(self.db.verify_student_account('S1', 'password'))

    def test_appeal_replay_does_not_duplicate_evidence(self):
        vid = self.db.log_violation('S1', 'S1', 'wrong_uniform')
        name, _, blob = picture_evidence()
        action = self.action('appeals', {'violation_id': vid, 'reason': 'Please review this original detection picture.', 'filename': name, 'file': base64.b64encode(blob).decode()})
        self.assertEqual(apply_action(self.db, action)['status'], 'applied')
        self.assertEqual(apply_action(self.db, action)['status'], 'applied')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM appeals').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM evidence_files').fetchone()[0], 1)

    def test_cannot_import_another_students_appeal(self):
        vid = self.db.log_violation('S2', 'S2', 'wrong_uniform')
        name, _, blob = picture_evidence()
        action = self.action('appeals', {'violation_id': vid, 'reason': 'Please review this original detection picture.', 'filename': name, 'file': base64.b64encode(blob).decode()})
        self.assertEqual(apply_action(self.db, action)['status'], 'rejected')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM appeals').fetchone()[0], 0)

    def test_on_time_cloud_submission_survives_later_deadline_closure(self):
        vid = self.db.log_violation('S1', 'S1', 'wrong_uniform')
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute('UPDATE violations SET appeal_deadline=?,appeal_window_closed_at=? WHERE id=?',
                (format_db_datetime(now - timedelta(hours=1)), format_db_datetime(now - timedelta(minutes=30)), vid))
            conn.commit()
        name, _, blob = picture_evidence()
        action = self.action('appeals', {'violation_id': vid, 'reason': 'Please review this original detection picture.', 'filename': name, 'file': base64.b64encode(blob).decode()})
        action['created_at'] = (now - timedelta(hours=2)).isoformat()
        self.assertEqual(apply_action(self.db, action)['status'], 'applied')

    def test_late_cloud_submission_is_rejected(self):
        vid = self.db.log_violation('S1', 'S1', 'wrong_uniform')
        with self.db.connect() as conn:
            conn.execute('UPDATE violations SET appeal_deadline=? WHERE id=?',
                (format_db_datetime(utc_now() - timedelta(hours=1)), vid))
            conn.commit()
        name, _, blob = picture_evidence()
        action = self.action('appeals', {'violation_id': vid, 'reason': 'Please review this original detection picture.', 'filename': name, 'file': base64.b64encode(blob).decode()})
        self.assertEqual(apply_action(self.db, action)['status'], 'rejected')
