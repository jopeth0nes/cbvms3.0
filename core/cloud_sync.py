"""Sync student-safe portal snapshots and import cloud submissions transactionally."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit

from core.appeal_evidence import validate_evidence
from core.discipline import parse_db_datetime, utc_now, format_db_datetime
from core.portal_state import page_snapshot
from database.db_manager import CBVMSDatabase
from web_portal import public

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'data' / 'cloud-sync.json'
PROFILE_FIELDS = ('student_id', 'name', 'email', 'college_department', 'course',
                  'report_year_level', 'report_section', 'student_status')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward sync credentials to another origin.


class CloudClient:
    def __init__(self, config):
        self.origin = config['origin'].rstrip('/')
        parsed = urlsplit(self.origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('Cloud sync requires an exact HTTPS Site origin.')
        self.headers = {'Authorization': 'Bearer ' + config['sync_key'],
                        'OAI-Sites-Authorization': 'Bearer ' + config['service_token'],
                        'Content-Type': 'application/json'}
        self.opener = build_opener(NoRedirect)

    def call(self, path, data=None):
        if path not in {'student', 'account', 'evidence', 'roster', 'actions', 'ack'}:
            raise ValueError('Unknown sync operation')
        req = Request(self.origin + '/api/sync/' + path,
                      data=None if data is None else json.dumps(data).encode(), headers=self.headers)
        with self.opener.open(req, timeout=45) as response:
            return json.load(response)


def student_snapshot(database, row):
    sid = row['student_id']
    dashboard = page_snapshot(database, sid, 'dashboard')
    profile = {key: dashboard.get('_student', {}).get(key, row.get(key)) for key in PROFILE_FIELDS}
    violations = database.get_visible_violations_for_student(sid, include_snapshot=False)
    result = {'id': sid, 'email': row['email'], 'name': row['name'],
        'active': not bool(row.get('registration_pending')),
        'snapshot': {'profile': profile, 'term': dashboard.get('_current_term'),
            'strikes': dashboard.get('_strike_summary'), 'suspension': dashboard.get('_active_suspension'),
            'counts': dashboard.get('_violation_counts'), 'violations': violations,
            'notifications': database.get_notifications_for_student(sid),
            'appeals': database.get_appeals_for_student(sid),
            'preferences': database.get_portal_preferences(sid)}}
    result['snapshot'] = public(result['snapshot'])
    return result


def apply_action(database, action):
    """One transaction commits both the mutation and receipt, making replay safe.

    Submission time comes only from the authenticated Site's server-owned queue.
    Student-facing local APIs retain their current-clock-only appeal behavior.
    """
    aid, sid, kind, payload = action['id'], action['student_id'], action['kind'], action['payload']
    submitted = parse_db_datetime(action['created_at'])
    if submitted is None or submitted > utc_now():
        raise ValueError('Invalid cloud submission time')
    with database.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS cloud_portal_receipts (id TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT NOT NULL)')
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT status,result FROM cloud_portal_receipts WHERE id=?', (aid,)).fetchone()
        if old:
            return dict(old)
        status, result = 'applied', 'Saved to campus records.'
        conn.execute('SAVEPOINT cloud_action')
        try:
            student = conn.execute('SELECT name,registration_pending FROM students WHERE student_id=?', (sid,)).fetchone()
            if not student or student['registration_pending']:
                raise ValueError('Student is no longer authorized. Contact the administrator.')
            if kind == 'password':
                encoded = payload.get('password_hash', '')
                if not re.fullmatch(r'pbkdf2_sha256\$v1\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}', encoded):
                    raise ValueError('Invalid password hash.')
                account = conn.execute('SELECT * FROM student_accounts WHERE student_id=?', (sid,)).fetchone()
                if not account or account['password_hash'] != payload.get('expected_hash'):
                    raise ValueError('Campus credentials changed. Use the latest campus password.')
                conn.execute('''UPDATE student_accounts SET password_hash=?,must_change_password=0,
                    first_login_completed_at=COALESCE(first_login_completed_at,?),password_changed_at=?,
                    credential_version=credential_version+1 WHERE id=?''',
                    (encoded, format_db_datetime(submitted), format_db_datetime(submitted), account['id']))
                conn.execute('DELETE FROM student_sessions WHERE account_id=?', (account['id'],))
            elif kind == 'appeals':
                reason = str(payload['reason']).strip()
                if not 20 <= len(reason) <= 1000:
                    raise ValueError('Appeal explanation must contain 20–1000 characters.')
                blob = base64.b64decode(payload['file'], validate=True)
                validate_evidence(payload['filename'], 'image', blob)
                row = conn.execute('''SELECT v.*,a.id AS appeal_id FROM violations v
                    LEFT JOIN appeals a ON a.violation_id=v.id WHERE v.id=?''', (int(payload['violation_id']),)).fetchone()
                if not row or row['student_id'] != sid:
                    raise ValueError('This violation does not belong to the student.')
                record = dict(row)
                # A deadline closed after the cloud submission must not invalidate an
                # on-time submission merely because the campus PC was powered off.
                closed = parse_db_datetime(record.get('appeal_window_closed_at'))
                if closed and closed > submitted:
                    record['appeal_window_closed_at'] = None
                if not database._appeal_eligibility_from_row(record, sid, submitted)['eligible']:
                    raise ValueError('Campus record changed or appeal is unavailable. Contact the administrator for review.')
                timestamp = format_db_datetime(submitted)
                cursor = conn.execute('INSERT INTO appeals (violation_id,student_id,reason,submitted_at) VALUES (?,?,?,?)',
                    (int(payload['violation_id']), sid, reason, timestamp))
                conn.execute('''INSERT INTO evidence_files
                    (appeal_id,student_id,filename,file_type,file_data,uploaded_at,file_sha256)
                    VALUES (?,?,?,?,?,?,?)''', (cursor.lastrowid, sid, payload['filename'], 'image', blob, timestamp, hashlib.sha256(blob).hexdigest()))
                result = f'Campus appeal #{cursor.lastrowid}'
            elif kind == 'reports':
                title, description = str(payload['title']).strip(), str(payload['description']).strip()
                if not title or len(title) > 200 or not description or len(description) > 10000 or payload['category'] not in {'System Bug', 'Account Issue', 'Violation Dispute', 'Other'}:
                    raise ValueError('Invalid system report.')
                conn.execute('''INSERT INTO system_reports
                    (reporter_id,reporter_name,category,title,description,submitted_at) VALUES (?,?,?,?,?,?)''',
                    (sid, student['name'], payload['category'], title, description, format_db_datetime(submitted)))
            elif kind == 'profile':
                name = str(payload['name']).strip()
                if not name or len(name) > 120:
                    raise ValueError('Invalid student name.')
                conn.execute('UPDATE students SET name=? WHERE student_id=?', (name, sid))
            elif kind == 'notifications/read':
                if payload.get('id'):
                    conn.execute('UPDATE student_notifications SET is_read=1 WHERE id=? AND student_id=?', (int(payload['id']), sid))
                else:
                    conn.execute('UPDATE student_notifications SET is_read=1 WHERE student_id=? AND created_at<=?', (sid, format_db_datetime(submitted)))
            else:
                raise ValueError('Unsupported cloud action.')
        except (ValueError, KeyError, TypeError) as exc:
            conn.execute('ROLLBACK TO cloud_action')
            status, result = 'rejected', str(exc)
        conn.execute('RELEASE cloud_action')
        conn.execute('INSERT INTO cloud_portal_receipts VALUES (?,?,?)', (aid, status, result))
        conn.commit()
        return {'status': status, 'result': result}


@contextmanager
def sync_lock(state_path):
    if state_path is None:
        yield
        return
    path = Path(state_path).with_suffix('.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        import os
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def sync_once(database, client, state_path=None):
    with sync_lock(state_path):
        return _sync_once(database, client, state_path)


def _sync_once(database, client, state_path=None):
    actions = client.call('actions')['actions']
    receipts = [(a['id'], apply_action(database, a)) for a in actions]
    with database.connect() as conn:
        rows = [dict(row) for row in conn.execute('SELECT student_id,name,email,registration_pending FROM students')]
    students = []
    for row in rows:
        # The cloud storage key is no longer a sign-in identity. Use a unique
        # internal alias so missing or shared contact emails cannot block login.
        row['email'] = hashlib.sha256(row['student_id'].encode()).hexdigest() + '@cbvms.invalid'
        students.append(row)
    state = json.loads(Path(state_path).read_text()) if state_path and Path(state_path).exists() else {}
    current = {}
    for row in students:
        data = student_snapshot(database, row)
        client.call('student', {'student': data})
        with database.connect() as conn:
            account = conn.execute('''SELECT username,password_hash,must_change_password,
                first_login_completed_at FROM student_accounts WHERE student_id=?''', (row['student_id'],)).fetchone()
        credentials = dict(account) if account else None
        if credentials:
            credentials['must_change_password'] = bool(credentials['must_change_password'] or not credentials.pop('first_login_completed_at'))
        client.call('account', {'student_id': row['student_id'], 'account': credentials})
        for violation in data['snapshot']['violations']:
            if not violation.get('has_snapshot'):
                continue
            evidence = database.get_student_original_evidence(violation['id'], row['student_id'])
            blob = evidence.get('snapshot')
            if not blob:
                continue
            key = f"{row['student_id']}:{violation['id']}"
            current[key] = hashlib.sha256(blob).hexdigest()
            if state.get(key) != current[key]:
                client.call('evidence', {'student_id': row['student_id'], 'violation_id': violation['id'], 'file': base64.b64encode(blob).decode()})
    client.call('roster', {'ids': [row['student_id'] for row in students]})
    # Publish updated records before clearing cloud pending indicators.
    for aid, receipt in receipts:
        client.call('ack', {'id': aid, **receipt})
    if state_path:
        path = Path(state_path)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(current), encoding='utf-8')
        temp.replace(path)
    return len(students)


def start_cloud_sync(database):
    if not CONFIG.exists():
        return None
    stop = threading.Event()
    def run():
        while not stop.is_set():
            try:
                client = CloudClient(json.loads(CONFIG.read_text(encoding='utf-8')))
                sync_once(CBVMSDatabase(database.db_path), client, ROOT / 'data' / 'cloud-sync-state.json')
            except Exception as exc:
                logging.warning('Cloud portal sync failed (%s); will retry.', type(exc).__name__)
            stop.wait(60)
    threading.Thread(target=run, daemon=True, name='cloud-portal-sync').start()
    return stop
