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

class ProtocolTests(CloudSyncTests):
    def register(self):
        with self.db.connect() as conn:
            conn.execute("INSERT INTO campus_sync_identity (singleton,source_id,writer_id) VALUES (1,'source','writer')")

    def test_replay_with_changed_payload_is_not_acknowledged(self):
        action = self.action('reports', {'title': 'Test', 'description': 'Details', 'category': 'Other'})
        apply_action(self.db, action)
        action['payload']['title'] = 'Changed'
        with self.assertRaises(ValueError):
            apply_action(self.db, action)

    def test_profile_base_value_conflict(self):
        action = self.action('profile', {'name': 'Cloud name', 'expected_name': 'Old campus name'})
        self.assertEqual(apply_action(self.db, action)['status'], 'rejected')
        self.assertEqual(self.db.get_student_by_student_id('S1')['name'], 'S1')

    def test_audit_is_readonly_even_with_due_deadlines(self):
        from core.sync_audit import audit, readonly
        before = Path(self.db.db_path).read_bytes()
        with patch.object(CBVMSDatabase, 'initialize', side_effect=AssertionError), patch.object(CBVMSDatabase, 'process_expired_deadlines', side_effect=AssertionError):
            self.assertEqual(audit(self.db.db_path)['local_students'], 2)
        self.assertEqual(before, Path(self.db.db_path).read_bytes())
        with readonly(self.db.db_path) as conn:
            with self.assertRaises(Exception):
                conn.execute('DELETE FROM students')

    def test_backup_restores_committed_wal(self):
        import sqlite3
        from core.sync_audit import backup, readonly
        connection = sqlite3.connect(self.db.db_path)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute("UPDATE students SET name='Committed WAL' WHERE student_id='S1'")
        connection.commit()
        target = Path(self.temp.name) / 'backup.db'
        self.assertEqual(backup(self.db.db_path, target)['restore_check'], 'passed')
        with readonly(target) as conn:
            self.assertEqual(conn.execute("SELECT name FROM students WHERE student_id='S1'").fetchone()[0], 'Committed WAL')

    def test_consistent_export_snapshot(self):
        from core.cloud_sync import frozen_database
        with frozen_database(self.db) as frozen:
            with self.db.connect() as conn:
                conn.execute("UPDATE students SET name='Concurrent change' WHERE student_id='S1'")
            self.assertEqual(frozen.get_student_by_student_id('S1')['name'], 'S1')

    def test_registered_offline_source_does_not_finalize_deadlines(self):
        vid = self.db.log_violation('S1', 'S1', 'wrong_uniform')
        with self.db.connect() as conn:
            conn.execute('UPDATE violations SET appeal_deadline=? WHERE id=?', (format_db_datetime(utc_now()-timedelta(days=1)), vid))
        self.register()
        self.db.process_expired_deadlines()
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes').fetchone()[0], 0)

    def test_timely_offline_appeal_corrects_actual_strike_and_automatic_suspension(self):
        vids = [self.db.log_violation('S1', 'S1', 'wrong_uniform') for _ in range(3)]
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute('UPDATE violations SET appeal_deadline=?', (format_db_datetime(now-timedelta(hours=1)),))
        self.db.process_expired_deadlines(now=now)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes WHERE is_active=1').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM student_suspensions WHERE lifted_at IS NULL').fetchone()[0], 1)
        name, _, blob = picture_evidence()
        action = self.action('appeals', {'violation_id': vids[-1], 'reason': 'Please review my timely submitted appeal.', 'filename': name, 'file': base64.b64encode(blob).decode()})
        action['created_at'] = (now-timedelta(hours=2)).isoformat()
        self.assertEqual(apply_action(self.db, action)['status'], 'applied')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes WHERE is_active=1').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM student_suspensions WHERE lifted_at IS NULL').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM discipline_reconciliations').fetchone()[0], 1)
            self.assertTrue(conn.execute("SELECT 1 FROM student_notifications WHERE title LIKE 'Timely appeal%'").fetchone())
        self.assertEqual(self.db.get_appeal_eligibility(vids[-1], 'S1')['reason'], 'already_submitted')

    def test_manual_suspension_remains_after_timely_cloud_appeal(self):
        vid = self.db.log_violation('S1', 'S1', 'wrong_uniform')
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute('UPDATE violations SET appeal_deadline=? WHERE id=?', (format_db_datetime(now-timedelta(hours=1)), vid))
            conn.execute("INSERT INTO student_suspensions (student_id,reason,starts_at,imposed_by,imposed_at) VALUES ('S1','Office decision',?,'office',?)", (format_db_datetime(now), format_db_datetime(now)))
        self.db.process_expired_deadlines(now=now)
        name, _, blob = picture_evidence()
        action = self.action('appeals', {'violation_id': vid, 'reason': 'Please review my timely submitted appeal.', 'filename': name, 'file': base64.b64encode(blob).decode()})
        action['created_at'] = (now-timedelta(hours=2)).isoformat()
        self.assertEqual(apply_action(self.db, action)['status'], 'applied')
        with self.db.connect() as conn:
            self.assertIsNone(conn.execute("SELECT lifted_at FROM student_suspensions WHERE imposed_by='office'").fetchone()[0])

    def test_same_hash_with_new_reset_revision_rejects_password_command(self):
        with self.db.connect() as conn:
            row = conn.execute("SELECT password_hash,credential_version FROM student_accounts WHERE student_id='S1'").fetchone()
        action = self.action('password', {'password_hash': hash_password('Personal cloud password'), 'expected_hash': row['password_hash'], 'expected_version': row['credential_version']-1})
        self.assertEqual(apply_action(self.db, action)['status'], 'rejected')

    def test_audit_cloud_comparison_never_calls_mutation_endpoint(self):
        from core.sync_audit import audit
        cloud = FakeCloud()
        report = audit(self.db.db_path, cloud)
        self.assertEqual(report['local_only'], 0)
        self.assertEqual(report['cloud_only'], 0)
        self.assertEqual(cloud.writes, [])

    def test_sync_lock_cannot_be_bypassed_with_different_cache(self):
        from core.cloud_sync import sync_lock, sync_once
        with sync_lock(Path(self.db.db_path).with_suffix('.cloud-sync-state')):
            with self.assertRaises(OSError):
                sync_once(self.db, FakeCloud(), Path(self.temp.name)/'other.json')

    def test_wrong_source_empty_roster_and_protocol_have_zero_remote_writes(self):
        from core.cloud_sync import sync_once
        self.register()
        for field, value in [('source_id', 'wrong'), ('protocol', 1), ('ids', [str(i) for i in range(100)])]:
            cloud = FakeCloud()
            cloud.meta[field] = value
            with self.assertRaises(ValueError):
                sync_once(self.db, cloud)
            self.assertEqual(cloud.writes, [])

    def test_backlog_bad_action_and_ack_crash_recovery(self):
        from core.cloud_sync import sync_once
        self.register()
        cloud = FakeCloud()
        cloud.actions = [dict(self.action('reports', {'title': 'Test', 'description': 'Details', 'category': 'Other'}, aid=f'action-{i}'), sequence=i+1) for i in range(30)]
        cloud.actions.insert(0, {'id': 'missing-evidence', 'student_id': 'S1', 'kind': 'appeals', 'sequence': 0, 'error': 'Missing evidence'})
        cloud.fail_after_publish = True
        with self.assertRaises(ConnectionError):
            sync_once(self.db, cloud)
        sync_once(self.db, cloud)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM system_reports').fetchone()[0], 30)
            self.assertEqual(conn.execute('SELECT generation FROM campus_sync_identity').fetchone()[0], 2)

    def test_missing_remote_evidence_repaired_even_with_corrupt_cache(self):
        from core.cloud_sync import sync_once
        self.register()
        name, _, blob = picture_evidence()
        self.db.log_violation('S1', 'S1', 'wrong_uniform', snapshot_jpeg=blob)
        cloud = FakeCloud()
        cache = Path(self.temp.name)/'cache.json'
        cache.write_text('{broken')
        sync_once(self.db, cloud, cache)
        initial = len([p for p in cloud.writes if p == 'evidence'])
        self.assertGreater(initial, 0)
        sync_once(self.db, cloud, cache)
        self.assertEqual(len([p for p in cloud.writes if p == 'evidence']), initial*2)


class FakeCloud:
    def __init__(self):
        self.meta = {'protocol': 2, 'source_id': 'source', 'writer_id': 'writer', 'generation': 0,
                     'run_id': None, 'ids': ['S1', 'S2'], 'queue': [], 'queued_ids': [], 'checked_at': utc_now().isoformat()}
        self.actions, self.writes = [], []
        self.fail_after_publish = False

    def call(self, path, data=None):
        if path == 'audit':
            return dict(self.meta)
        if path.startswith('actions?'):
            after = int(path.split('=')[1])
            actions = [a for a in self.actions if a['sequence'] > after or (after == 0 and a['sequence'] == 0)]
            return {'actions': actions[:25], 'has_more': len(actions)>25, 'pending': len(self.actions), 'oldest_pending': None}
        self.writes.append(path)
        if path == 'evidence':
            return {'sha256': data['sha256']}
        if path == 'publish':
            self.meta.update(generation=data['generation']+1, run_id=data['run_id'])
            receipts = {r['id'] for r in data['receipts']}
            self.actions = [a for a in self.actions if a['id'] not in receipts]
            if self.fail_after_publish:
                self.fail_after_publish = False
                raise ConnectionError('Disconnected after commit')
            return {'generation': self.meta['generation'], 'run_id': data['run_id']}
        raise AssertionError(path)
