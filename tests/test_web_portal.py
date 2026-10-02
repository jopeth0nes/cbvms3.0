import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from database.db_manager import CBVMSDatabase
from tests.evidence_fixture import picture_evidence
from web_portal import PortalServer


class WebPortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'web.db')
        self.db.initialize()
        for sid in ('S1', 'S2'):
            self.db.insert_student(sid, sid, 'BSIT', '3A', b'biometric', b'photo',
                account_username=sid, account_password='password', email=f'{sid}@example.com')
        self.server = PortalServer(('127.0.0.1', 0), self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = None
        self.csrf = ''
        self.addCleanup(self.stop)
        self.dispatch = patch('core.email_sender.dispatch_discipline_notice').start()
        self.addCleanup(patch.stopall)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, csrf=True):
        conn = http.client.HTTPConnection(*self.server.server_address)
        headers = {}
        if self.cookie:
            headers['Cookie'] = self.cookie
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if csrf:
                headers['X-CSRF-Token'] = self.csrf
        conn.request('POST' if data is not None else 'GET', path,
                     json.dumps(data) if data is not None else None, headers)
        result = conn.getresponse()
        status = result.status
        if result.getheader('Set-Cookie'):
            self.cookie = result.getheader('Set-Cookie').split(';')[0]
        body = result.read()
        content = json.loads(body) if result.getheader('Content-Type') == 'application/json' else body
        conn.close()
        return status, content

    def login(self):
        status, session = self.request('/api/login', {'username': 'S1', 'password': 'password'})
        self.assertEqual(status, 200)
        self.csrf = session['csrf']

    def test_authentication_csrf_logout_and_preferences(self):
        self.assertEqual(self.request('/api/page')[0], 401)
        self.assertEqual(self.request('/api/login', {'username': 'S1', 'password': 'bad'})[0], 401)
        self.login()
        self.assertEqual(self.request('/api/preferences', {'dark_mode': True}, csrf=False)[0], 403)
        self.assertEqual(self.request('/api/preferences', {'dark_mode': True})[0], 200)
        self.assertEqual(self.db.get_portal_preferences('S1'), {'dark_mode': True})
        self.assertEqual(self.db.get_portal_preferences('S2'), {})
        self.assertEqual(self.request('/api/preferences', {'dark_mode': 'true'})[0], 400)
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.request('/api/me')[0], 401)

    def test_records_and_evidence_are_scoped_to_logged_in_student(self):
        own = self.db.log_violation('S1', 'S1', 'wrong_uniform', snapshot_jpeg=picture_evidence()[2])
        other = self.db.log_violation('S2', 'S2', 'wrong_uniform', snapshot_jpeg=picture_evidence()[2])
        self.login()
        status, page = self.request('/api/page?page=violations&student_id=S2')
        self.assertEqual(status, 200)
        self.assertEqual([r['id'] for r in page['_violations']], [own])
        self.assertNotIn('snapshot', page['_violations'][0])
        self.assertEqual(self.request(f'/api/evidence?id={other}')[0], 400)
        self.assertEqual(self.request(f'/api/evidence?id={own}')[1], picture_evidence()[2])
        self.assertEqual(self.request('/api/notifications/read', {})[0], 200)
        self.assertEqual(self.db.get_unread_notification_count('S1'), 0)
        self.assertGreater(self.db.get_unread_notification_count('S2'), 0)

    def test_browser_appeals_share_admin_strikes_and_suspension(self):
        self.login()
        for strike in range(1, 4):
            vid = self.db.log_violation('S1', 'S1', 'wrong_uniform', snapshot_jpeg=picture_evidence()[2])
            status, appeal = self.request('/api/appeals', {
                'violation_id': vid, 'reason': 'Please review this picture.',
                'filename': 'picture.png', 'file': base64.b64encode(picture_evidence()[2]).decode()})
            self.assertEqual(status, 201, appeal)
            self.assertTrue(self.db.update_appeal_decision(appeal['id'], 'rejected', 'Reviewed', decided_by='admin'))
        self.dispatch.assert_called_once()
        self.assertIn('Suspension Notice', self.dispatch.call_args.args[3])
        status, page = self.request('/api/page?page=dashboard')
        self.assertEqual(status, 200)
        self.assertEqual(page['_strike_summary'][0]['active_count'], 3)
        self.assertIsNotNone(page['_active_suspension'])

    def test_profile_reports_and_invalid_upload(self):
        self.login()
        self.assertEqual(self.request('/api/profile', {'name': 'Updated name'})[0], 200)
        self.assertEqual(self.db.get_student_by_student_id('S1')['name'], 'Updated name')
        self.assertEqual(self.db.get_student_by_student_id('S2')['name'], 'S2')
        self.assertEqual(self.request('/api/reports', {'title': 'Issue', 'description': 'Details', 'category': 'System Bug'})[0], 201)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT reporter_id FROM system_reports').fetchone()[0], 'S1')
        self.assertEqual(self.request('/api/appeals', {'violation_id': 1, 'reason': 'Review', 'filename': 'a.png', 'file': 'invalid'})[0], 400)

    def test_static_website_is_available(self):
        for path in ('/', '/app.js', '/style.css'):
            status, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(content)
