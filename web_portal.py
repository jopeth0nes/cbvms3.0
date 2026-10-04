"""Run the connected student website: .venv/Scripts/python.exe web_portal.py."""
from __future__ import annotations

import argparse
import base64
import json
import logging
import secrets
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from core.portal_state import page_snapshot
from core.appeal_evidence import validate_evidence, MAX_EVIDENCE_BYTES
from database.db_manager import CBVMSDatabase
from database.student_credentials import SessionExpired

STATIC = Path(__file__).parent / 'web'
MAX_BODY = 15 * 1024 * 1024


def public(value):
    """Never serialize biometric, credential, or evidence bytes into page JSON."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()
                if k not in {'encoding', 'photo', 'profile_photo', 'portal_photo',
                             'snapshot', 'password_hash', 'file_data', 'session_token'}
                and not str(k).startswith('_profile') and k != '_timings'}
    if isinstance(value, (tuple, list)):
        return [public(v) for v in value]
    if isinstance(value, bytes):
        return None
    return value


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, database):
        super().__init__(address, PortalHandler)
        self.database = database
        self.attempts = {}
        self.lock = threading.Lock()


class PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logging.info('%s %s', self.client_address[0], fmt % args)

    def reply(self, status, value, *, content_type='application/json', cookie=None):
        payload = json.dumps(public(value)).encode() if content_type == 'application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(payload)

    def session(self):
        jar = cookies.SimpleCookie()
        jar.load(self.headers.get('Cookie', ''))
        token = jar['cbvms_session'].value if 'cbvms_session' in jar else ''
        try:
            return token, self.server.database.get_student_session(token)
        except SessionExpired:
            return token, None

    def session_reply(self, session):
        token = session['session_token']
        age = max(0, int(session['expires'] - time.time()))
        return self.reply(200, session, cookie=f'cbvms_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}')

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def handle_request(self, write):
        try:
            self.route(write)
        except SessionExpired as exc:
            self.reply(401, {'error': str(exc)})
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(400, {'error': str(exc)})
        except Exception:
            logging.exception('Portal request failed')
            self.reply(500, {'error': 'Request failed. Please try again.'})

    def route(self, write):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if not write and path in {'/', '/app.js', '/style.css'}:
            filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
            mime = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8',
                    '/style.css': 'text/css; charset=utf-8'}[path]
            return self.reply(200, (STATIC / filename).read_bytes(), content_type=mime)
        data = {}
        if write:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > MAX_BODY:
                return self.reply(413, {'error': 'Invalid request size.'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(415, {'error': 'JSON required.'})
            origin = self.headers.get('Origin')
            if origin and origin != f'http://{self.headers.get("Host")}':
                return self.reply(403, {'error': 'Request origin rejected.'})
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Invalid request')
        db = self.server.database
        token, session = self.session()
        if write and path == '/api/login':
            now, ip = time.time(), self.client_address[0]
            with self.server.lock:
                attempts = [t for t in self.server.attempts.get(ip, []) if now-t < 60]
                self.server.attempts[ip] = attempts + [now]
            if len(attempts) >= 10:
                return self.reply(429, {'error': 'Too many attempts. Try again in a minute.'})
            account = db.verify_student_account(str(data.get('username', '')), str(data.get('password', '')))
            if not account:
                return self.reply(401, {'error': 'Incorrect student username or password.'})
            if token:
                db.revoke_student_session(token)
            return self.session_reply(db.create_student_session(account))
        if not session:
            return self.reply(401, {'error': 'Please sign in.'})
        sid = session['student_id']
        if write and not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
            return self.reply(403, {'error': 'Please refresh and try again.'})
        if write and path == '/api/logout':
            db.revoke_student_session(token)
            return self.reply(200, {'ok': True}, cookie='cbvms_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        if not write and path == '/api/me':
            return self.reply(200, session if session['must_change_password'] else
                              dict(session, preferences=db.get_portal_preferences(sid)))
        if write and path == '/api/setup/password':
            if not session['must_change_password']:
                return self.reply(409, {'error': 'Password setup is already complete.'})
            return self.session_reply(db.change_student_password(token, data.get('password'), data.get('confirmation')))
        if session['must_change_password']:
            return self.reply(403, {'error': 'Set your new password before entering the student portal.',
                                    'must_change_password': True})
        # Recheck persisted authorization before every portal operation.
        db.get_student_session(token, require_full=True)
        db = CBVMSDatabase(db.db_path, timeout=2)
        db.student_session_ref = {'token': token}
        if not write and path == '/api/page':
            page = query.get('page', ['dashboard'])[0]
            if page not in {'dashboard', 'violations', 'notifications', 'appeals', 'profile', 'settings', 'report'}:
                raise ValueError('Unknown page')
            offset = max(0, min(int(query.get('offset', ['0'])[0]), 100000))
            # Profile snapshot prepares desktop imagery; metadata comes from dashboard instead.
            result = page_snapshot(db, sid, 'dashboard' if page == 'profile' else page, offset=offset)
            return self.reply(200, result)
        if not write and path == '/api/evidence':
            row = db.get_student_original_evidence(int(query['id'][0]), sid)
            if not row.get('snapshot'):
                return self.reply(404, {'error': 'No original picture available.'})
            return self.reply(200, row['snapshot'], content_type='image/jpeg')
        if write and path == '/api/preferences':
            return self.reply(200, db.set_portal_preferences(sid, data))
        if write and path == '/api/notifications/read':
            if data.get('id'):
                db.mark_notification_read(int(data['id']), student_id=sid)
            else:
                db.mark_all_notifications_read(sid)
            return self.reply(200, {'ok': True})
        if write and path == '/api/appeals':
            blob = base64.b64decode(data['file'], validate=True)
            if len(blob) > MAX_EVIDENCE_BYTES:
                raise ValueError('Evidence file is too large.')
            validate_evidence(data['filename'], 'image', blob)
            evidence = (data['filename'], 'image', blob)
            aid = db.insert_appeal(int(data['violation_id']), sid, data['reason'], evidence=evidence)
            if not aid:
                raise ValueError('Appeal unavailable. Refresh the record and check its deadline.')
            return self.reply(201, {'id': aid})
        if write and path == '/api/profile':
            name = str(data.get('name', '')).strip()
            if not name or len(name) > 120:
                raise ValueError('Enter a name up to 120 characters.')
            db.update_student_name(sid, name)
            session['name'] = name
            return self.reply(200, {'ok': True})
        if write and path == '/api/password':
            return self.session_reply(self.server.database.change_student_password(token, data.get('password'),
                                      data.get('confirmation'), current_password=data.get('current_password')))
        if write and path == '/api/reports':
            title, description = str(data.get('title', '')).strip(), str(data.get('description', '')).strip()
            if not title or not description or len(title) > 200 or len(description) > 10000:
                raise ValueError('Enter a title and description within the allowed length.')
            if data.get('category') not in {'System Bug', 'Account Issue', 'Violation Dispute', 'Other'}:
                raise ValueError('Choose a report category.')
            if not db.insert_system_report(reporter_id=sid, reporter_name=session['name'],
                    category=data['category'], title=title, description=description):
                raise ValueError('Report could not be saved.')
            return self.reply(201, {'ok': True})
        self.reply(404, {'error': 'Not found.'})


def main():
    parser = argparse.ArgumentParser(description='Connected CBVMS student website')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    db = CBVMSDatabase(args.database)
    db.initialize()
    server = PortalServer((args.host, args.port), db)
    print(f'Student portal: http://{args.host}:{args.port} | Database: {db.db_path}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
