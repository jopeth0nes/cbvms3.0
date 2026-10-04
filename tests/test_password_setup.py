import hashlib
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from auth.auth_manager import AuthManager
from auth.passwords import hash_password, verify_password
from database.db_manager import CBVMSDatabase
from database.student_credentials import SessionExpired, migrate_credentials


class PasswordSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'accounts.db', timeout=.2)
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('S1', 'Student', 'BSIT', '1A', b'', b'',
                               account_password='R4nd0mTmpXZ')
        self.auth = AuthManager(self.db)

    def pending(self):
        return self.auth.authenticate('S1', 'R4nd0mTmpXZ')

    def change(self, session, password='My new personal passphrase'):
        return self.db.change_student_password(session['session_token'], password, password)

    def state(self):
        with self.db.connect() as conn:
            return dict(conn.execute('SELECT * FROM student_accounts WHERE student_id="S1"').fetchone())

    def test_enrollment_and_self_registration_require_setup(self):
        self.db.insert_student('S2', 'Self registered', 'BSIT', '1A', b'', b'',
                               account_username='self-register', account_password='my initial personal')
        self.db.insert_student_account('S3', 'legacy-path', 'some initial password')
        for username, password in [('S1', 'R4nd0mTmpXZ'), ('self-register', 'my initial personal'),
                                   ('legacy-path', 'some initial password')]:
            session = self.auth.authenticate(username, password)
            self.assertTrue(session['must_change_password'])
            with self.assertRaises(SessionExpired):
                self.db.get_student_session(session['session_token'], require_full=True)
        self.assertIsNone(self.state()['first_login_completed_at'])

    def test_success_persists_and_old_password_and_sessions_fail(self):
        pending = self.pending()
        other = self.pending()
        full = self.change(pending)
        self.assertFalse(full['must_change_password'])
        state = self.state()
        self.assertEqual(state['must_change_password'], 0)
        self.assertIsNotNone(state['first_login_completed_at'])
        self.assertIsNotNone(state['password_changed_at'])
        self.assertEqual(state['credential_version'], 2)
        for session in (pending, other):
            with self.assertRaises(SessionExpired):
                self.db.get_student_session(session['session_token'])
        self.assertIsNone(self.auth.authenticate('S1', 'R4nd0mTmpXZ'))
        self.db.initialize(process_deadlines=False)
        self.assertFalse(self.auth.authenticate('S1', 'My new personal passphrase')['must_change_password'])
        self.assertEqual(state, self.state())

    def test_validation_does_not_change_state(self):
        pending = self.pending()
        before = self.state()
        for password, confirmation in [('short', 'short'), ('abcdefgh', 'different'),
                                       ('R4nd0mTmpXZ', 'R4nd0mTmpXZ'), ('student123', 'student123'),
                                       ('superadmin123', 'superadmin123'), (None, None)]:
            with self.subTest(password=password), self.assertRaises(ValueError):
                self.db.change_student_password(pending['session_token'], password, confirmation)
        self.assertEqual(before, self.state())
        long = '  A long 🐈 passphrase ' * 30 + '  '
        self.change(pending, long)
        self.assertIsNotNone(self.auth.authenticate('S1', long))
        self.assertIsNone(self.auth.authenticate('S1', long.strip()))

    def test_admin_reset_invalidates_setup_and_full_sessions(self):
        pending = self.pending()
        self.assertTrue(self.db.reset_student_password('S1', 'Reset temporary'))
        with self.assertRaises(SessionExpired):
            self.change(pending)
        reset = self.auth.authenticate('S1', 'Reset temporary')
        full = self.change(reset)
        self.db.reset_student_password('S1', 'Second reset temporary')
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(full['session_token'], require_full=True)
        self.assertTrue(self.auth.authenticate('S1', 'Second reset temporary')['must_change_password'])

    def test_reset_during_hashing_wins(self):
        pending = self.pending()
        original = hash_password
        def concurrent_reset(password):
            self.db.reset_student_password('S1', 'Administrator newer reset')
            return original(password)
        with patch('database.student_credentials.hash_password', side_effect=concurrent_reset):
            with self.assertRaises(SessionExpired):
                self.change(pending)
        self.assertIsNotNone(self.auth.authenticate('S1', 'Administrator newer reset'))

    def test_duplicate_and_concurrent_changes_commit_once(self):
        pending = self.pending()
        barrier = threading.Barrier(2)
        def change():
            barrier.wait()
            try:
                return self.change(pending)
            except SessionExpired:
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: change(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.state()['credential_version'], 2)

    def test_database_failure_rolls_back_then_retry(self):
        pending = self.pending()
        before = self.state()
        with self.db.connect() as conn:
            conn.execute("CREATE TRIGGER fail_password BEFORE UPDATE ON student_accounts BEGIN SELECT RAISE(ABORT, 'test save failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.change(pending)
        self.assertEqual(before, self.state())
        self.assertTrue(self.db.get_student_session(pending['session_token'])['must_change_password'])
        with self.db.connect() as conn:
            conn.execute('DROP TRIGGER fail_password')
        self.assertFalse(self.change(pending)['must_change_password'])

    def test_cancel_and_expired_session_cannot_save(self):
        pending = self.pending()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ValueError):
            self.db.change_student_password(pending['session_token'], 'another password', 'another password', cancel=cancel)
        with self.db.connect() as conn:
            conn.execute('UPDATE student_sessions SET expires=0')
        with self.assertRaises(SessionExpired):
            self.change(pending)
        self.assertEqual(self.state()['must_change_password'], 1)

    def test_settings_change_does_not_require_setup_again(self):
        full = self.change(self.pending())
        with self.assertRaisesRegex(ValueError, 'Current password'):
            self.db.change_student_password(full['session_token'], 'New settings passphrase', 'New settings passphrase', current_password='bad')
        updated = self.db.change_student_password(full['session_token'], 'New settings passphrase',
                     'New settings passphrase', current_password='My new personal passphrase')
        self.assertFalse(updated['must_change_password'])
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(full['session_token'])
        self.assertFalse(self.auth.authenticate('S1', 'New settings passphrase')['must_change_password'])

    def test_legacy_migration_preserves_credentials_and_is_repeatable(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        old_hash = hashlib.sha256(b'legacy personal password').hexdigest()
        conn.execute('CREATE TABLE student_accounts(id INTEGER PRIMARY KEY, student_id TEXT, username TEXT, password_hash TEXT)')
        conn.execute('INSERT INTO student_accounts VALUES (7,"ID","user",?)', (old_hash,))
        migrate_credentials(conn)
        self.assertEqual(conn.execute('SELECT id,student_id,username,password_hash,must_change_password FROM student_accounts').fetchone(),
                         (7, 'ID', 'user', old_hash, 1))
        conn.execute("UPDATE student_accounts SET must_change_password=0,first_login_completed_at='2026-01-01',password_changed_at='2026-01-01'")
        migrate_credentials(conn)
        self.assertEqual(conn.execute('SELECT must_change_password,first_login_completed_at FROM student_accounts').fetchone(), (0, '2026-01-01'))
        with self.db.connect() as c:
            c.execute('UPDATE student_accounts SET password_hash=? WHERE student_id="S1"', (old_hash,))
        self.assertTrue(self.auth.authenticate('S1', 'legacy personal password')['must_change_password'])
        self.assertTrue(self.state()['password_hash'].startswith('pbkdf2_sha256$v1$'))

    def test_staff_login_and_legacy_hash_upgrade(self):
        legacy = hashlib.sha256(b'admin123').hexdigest()
        with self.db.connect() as conn:
            conn.execute('UPDATE users SET password_hash=? WHERE username="admin"', (legacy,))
        self.assertEqual(self.auth.authenticate('admin', 'admin123')['role'], 'admin')
        self.assertEqual(self.auth.authenticate('superadmin', 'superadmin123')['role'], 'superadmin')
        self.assertIsNone(self.auth.authenticate('admin', 'wrong'))
        self.assertNotEqual(hash_password('same'), hash_password('same'))
        self.assertTrue(verify_password('same', hash_password('same')))

    def test_upsert_reset_preserves_username_and_restricts(self):
        self.change(self.pending())
        self.assertEqual(self.db.upsert_student_account('S1', 'replacement-name', 'New issued password'), (True, 'S1'))
        self.assertTrue(self.auth.authenticate('S1', 'New issued password')['must_change_password'])

    def test_session_insert_failure_rolls_back_credential_and_revocations(self):
        pending = self.pending()
        before = self.state()
        with self.db.connect() as conn:
            conn.execute("CREATE TRIGGER fail_session BEFORE INSERT ON student_sessions BEGIN SELECT RAISE(ABORT, 'rotation failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.change(pending)
        self.assertEqual(self.state(), before)
        self.assertTrue(self.db.get_student_session(pending['session_token'])['must_change_password'])

    def test_desktop_database_connections_recheck_authorization(self):
        pending = self.pending()
        guarded = CBVMSDatabase(self.db.db_path)
        guarded.student_session_ref = {'token': pending['session_token']}
        with self.assertRaises(SessionExpired):
            guarded.get_notifications_for_student('S1')
        full = self.change(pending)
        guarded.student_session_ref['token'] = full['session_token']
        self.assertIsInstance(guarded.get_notifications_for_student('S1'), list)
        self.db.reset_student_password('S1', 'Administrator reset')
        with self.assertRaises(SessionExpired):
            guarded.set_portal_preferences('S1', {'dark_mode': True})
