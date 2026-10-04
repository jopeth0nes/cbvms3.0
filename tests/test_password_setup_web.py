"""Real HTTP tests against disposable shared account state."""
import sqlite3
from auth.auth_manager import AuthManager
from database.student_credentials import SessionExpired
from tests import test_web_portal


class PasswordSetupWebTests(test_web_portal.WebPortalTests):
    def restricted_login(self):
        status, session = self.request('/api/login', {'username': 'S1', 'password': 'password'})
        self.assertEqual(status, 200, session)
        self.assertTrue(session['must_change_password'])
        self.assertNotIn('session_token', session)
        self.csrf = session['csrf']
        return session

    def setup(self):
        status, session = self.request('/api/setup/password',
            {'password': 'Browser personal passphrase', 'confirmation': 'Browser personal passphrase', 'student_id': 'S2'})
        self.assertEqual(status, 200, session)
        self.csrf = session['csrf']
        return session

    def test_restricted_refresh_and_all_portal_routes_blocked(self):
        self.restricted_login()
        self.assertTrue(self.request('/api/me')[1]['must_change_password'])
        self.assertNotIn('preferences', self.request('/api/me')[1])
        for path in ('/api/page?page=dashboard', '/api/page?page=profile', '/api/evidence?id=1', '/api/unknown'):
            self.assertEqual(self.request(path)[0], 403, path)
        for path in ('/api/profile', '/api/password', '/api/reports', '/api/appeals',
                     '/api/notifications/read', '/api/preferences'):
            self.assertEqual(self.request(path, {'student_id': 'S2'})[0], 403, path)
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.request('/api/me')[0], 401)

    def test_csrf_validation_rotation_and_identity(self):
        self.restricted_login()
        old_cookie, old_csrf = self.cookie, self.csrf
        valid = {'password': 'Browser personal passphrase', 'confirmation': 'Browser personal passphrase'}
        self.assertEqual(self.request('/api/setup/password', valid, csrf=False)[0], 403)
        for invalid in ({'password': 'short', 'confirmation': 'short'},
                        {'password': 'long enough', 'confirmation': 'mismatch'},
                        {'password': 'password', 'confirmation': 'password'},
                        {'password': 'student123', 'confirmation': 'student123'}):
            self.assertEqual(self.request('/api/setup/password', invalid)[0], 400)
        session = self.setup()
        self.assertFalse(session['must_change_password'])
        self.assertNotEqual(self.cookie, old_cookie)
        self.assertNotEqual(self.csrf, old_csrf)
        new_cookie, self.cookie = self.cookie, old_cookie
        self.assertEqual(self.request('/api/me')[0], 401)
        self.cookie = new_cookie
        self.assertEqual(self.request('/api/page')[0], 200)
        self.assertEqual(self.request('/api/setup/password', valid)[0], 409)
        self.assertIsNotNone(self.db.verify_student_account('S2', 'password'))
        self.assertIsNone(self.db.verify_student_account('S1', 'password'))
        desktop = AuthManager(self.db).authenticate('S1', 'Browser personal passphrase')
        self.assertFalse(desktop['must_change_password'])

    def test_desktop_change_invalidates_browser_pending_and_reset_invalidates_full(self):
        self.restricted_login()
        desktop = AuthManager(self.db).authenticate('S1', 'password')
        full = self.db.change_student_password(desktop['session_token'], 'Desktop personal passphrase', 'Desktop personal passphrase')
        self.assertEqual(self.request('/api/me')[0], 401)
        status, session = self.request('/api/login', {'username': 'S1', 'password': 'Desktop personal passphrase'})
        self.assertEqual(status, 200)
        self.assertFalse(session['must_change_password'])
        self.csrf = session['csrf']
        self.db.reset_student_password('S1', 'Admin reset password')
        self.assertEqual(self.request('/api/page')[0], 401)
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(full['session_token'])
        status, session = self.request('/api/login', {'username': 'S1', 'password': 'Admin reset password'})
        self.assertEqual(status, 200)
        self.assertTrue(session['must_change_password'])

    def test_expired_pending_and_reset_during_setup(self):
        self.restricted_login()
        with self.db.connect() as conn:
            conn.execute('UPDATE student_sessions SET expires=0')
        valid = {'password': 'Browser personal passphrase', 'confirmation': 'Browser personal passphrase'}
        self.assertEqual(self.request('/api/setup/password', valid)[0], 401)
        self.restricted_login()
        self.db.reset_student_password('S1', 'A newer reset password')
        self.assertEqual(self.request('/api/setup/password', valid)[0], 401)
        self.assertIsNotNone(self.db.verify_student_account('S1', 'A newer reset password'))

    def test_database_failure_then_retry_and_settings_rotation(self):
        self.restricted_login()
        with self.db.connect() as conn:
            conn.execute("CREATE TRIGGER fail_save BEFORE UPDATE ON student_accounts BEGIN SELECT RAISE(ABORT, 'save failure'); END")
        valid = {'password': 'Browser personal passphrase', 'confirmation': 'Browser personal passphrase'}
        self.assertEqual(self.request('/api/setup/password', valid)[0], 500)
        self.assertTrue(self.request('/api/me')[1]['must_change_password'])
        with self.db.connect() as conn:
            conn.execute('DROP TRIGGER fail_save')
        self.setup()
        old_cookie = self.cookie
        status, session = self.request('/api/password', {'username': 'S2', 'student_id': 'S2',
            'current_password': 'Browser personal passphrase', 'password': 'Settings personal passphrase',
            'confirmation': 'Settings personal passphrase'})
        self.assertEqual(status, 200, session)
        self.assertFalse(session['must_change_password'])
        self.csrf = session['csrf']
        self.assertNotEqual(self.cookie, old_cookie)
        self.assertIsNotNone(self.db.verify_student_account('S2', 'password'))
        self.assertIsNotNone(self.db.verify_student_account('S1', 'Settings personal passphrase'))
        self.assertEqual(self.request('/api/page')[0], 200)
