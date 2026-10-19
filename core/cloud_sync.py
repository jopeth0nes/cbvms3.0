"""Sync student-safe portal snapshots and import cloud submissions transactionally."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import re
import sqlite3
import uuid
import tempfile
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
                        'Content-Type': 'application/json',
                        'X-CBVMS-Protocol': '2',
                        'X-CBVMS-Source': config.get('source_id', ''),
                        'X-CBVMS-Writer': config.get('writer_id', '')}
        self.opener = build_opener(NoRedirect)

    def call(self, path, data=None):
        if path.split('?')[0] not in {'audit', 'actions', 'evidence', 'publish'}:
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
    if not all(isinstance(value, str) and 0 < len(value) <= 200 for value in (aid, sid, kind)) or not isinstance(payload, dict):
        raise ValueError('Invalid command envelope')
    submitted = parse_db_datetime(action['created_at'])
    if submitted is None or submitted > utc_now():
        raise ValueError('Invalid cloud submission time')
    with database.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS cloud_portal_receipts (id TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT NOT NULL)')
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        from database.cloud_sync_migration import migrate
        migrate(conn)
        fingerprint = action_digest(action)
        old = conn.execute('SELECT status,result,digest FROM cloud_portal_receipts WHERE id=?', (aid,)).fetchone()
        if old:
            if old['digest'] != fingerprint:
                raise ValueError('Action identity conflict; administrator review required')
            return {'status': old['status'], 'result': old['result']}
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
                if not account or account['password_hash'] != payload.get('expected_hash') or (payload.get('expected_version') is not None and account['credential_version'] != payload['expected_version']):
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
                if payload.get('evidence_sha256') and hashlib.sha256(blob).hexdigest() != payload['evidence_sha256']:
                    raise ValueError('Appeal evidence checksum mismatch')
                row = conn.execute('''SELECT v.*,a.id AS appeal_id FROM violations v
                    LEFT JOIN appeals a ON a.violation_id=v.id WHERE v.id=?''', (int(payload['violation_id']),)).fetchone()
                if not row or row['student_id'] != sid:
                    raise ValueError('This violation does not belong to the student.')
                record = dict(row)
                if payload.get('violation_identity') and payload['violation_identity'] != violation_identity(record):
                    raise ValueError('Violation identity conflict.')
                # A deadline closed after the cloud submission must not invalidate an
                # on-time submission merely because the campus PC was powered off.
                closed = parse_db_datetime(record.get('appeal_window_closed_at'))
                if closed and closed > submitted:
                    record['appeal_window_closed_at'] = None
                if not database._appeal_eligibility_from_row(record, sid, submitted)['eligible']:
                    raise ValueError('Campus record changed or appeal is unavailable. Contact the administrator for review.')
                database._reconcile_cloud_appeal_conn(conn, dict(row), submitted)
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
                if payload.get('expected_name') != student['name']:
                    raise ValueError('Campus profile changed; review required.')
                conn.execute('UPDATE students SET name=? WHERE student_id=?', (name, sid))
            elif kind == 'notifications/read':
                if payload.get('id'):
                    if not conn.execute('SELECT 1 FROM student_notifications WHERE id=? AND student_id=?', (int(payload['id']), sid)).fetchone():
                        raise ValueError('Notification does not belong to the student')
                    conn.execute('UPDATE student_notifications SET is_read=1 WHERE id=? AND student_id=?', (int(payload['id']), sid))
                else:
                    conn.execute('UPDATE student_notifications SET is_read=1 WHERE student_id=? AND created_at<=?', (sid, format_db_datetime(submitted)))
            else:
                raise ValueError('Unsupported cloud action.')
        except (ValueError, KeyError, TypeError) as exc:
            conn.execute('ROLLBACK TO cloud_action')
            status, result = 'rejected', 'Campus review required: invalid or conflicting submission.'
        conn.execute('RELEASE cloud_action')
        conn.execute('INSERT INTO cloud_portal_receipts (id,status,result,digest) VALUES (?,?,?,?)', (aid, status, result, fingerprint))
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


def action_digest(action):
    # Evidence transport bytes are excluded; the immutable queue payload includes
    # its server-owned checksum. Legacy actions remain bound to their full payload.
    payload = dict(action['payload'])
    if payload.get('evidence_sha256'):
        payload.pop('file', None)
    return hashlib.sha256(json.dumps({k: action[k] for k in ('id', 'student_id', 'kind', 'created_at')} |
        {'payload': payload}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def violation_identity(row):
    return hashlib.sha256(json.dumps([row.get(k) for k in
        ('id', 'student_id', 'timestamp', 'violation_code')], separators=(',', ':')).encode()).hexdigest()


class FrozenDatabase(CBVMSDatabase):
    """Private backup snapshot: legacy getters may initialize derived tables here."""
    def process_expired_deadlines(self, **kwargs):
        return {}


@contextmanager
def frozen_database(database):
    with tempfile.TemporaryDirectory(prefix='cbvms-export-') as directory:
        path = Path(directory) / 'snapshot.db'
        from core.sync_audit import readonly
        with readonly(database.db_path) as source, sqlite3.connect(path) as dest:
            source.backup(dest)
        yield FrozenDatabase(path)


def sync_once(database, client, state_path=None):
    # Lock by database, not optional/cache path. Works on macOS and Windows.
    from core.sync_status import write_status
    try:
        with sync_lock(Path(database.db_path).with_suffix('.cloud-sync-state')):
            write_status(database.db_path, state='syncing', message='Validating campus source and cloud protocol.')
            return _sync_once(database, client, state_path)
    except Exception as exc:
        from urllib.error import URLError
        write_status(database.db_path, state='offline' if isinstance(exc, URLError) else 'paused' if isinstance(exc, ValueError) else 'failed',
                     message='Sync stopped. Run audit; check source, schema, connectivity and unresolved actions. Campus and cloud data are preserved.')
        raise


def _sync_once(database, client, state_path=None):
    from core.sync_audit import identity
    with database.connect() as conn:
        source = identity(conn)
    if not source or source['protocol'] != 2:
        raise ValueError('Sync paused: register the authoritative campus source first.')
    remote = client.call('audit')
    if remote['protocol'] != 2 or remote['source_id'] != source['source_id'] or remote['writer_id'] != source['writer_id']:
        raise ValueError('Sync paused: source or protocol mismatch.')
    # Recover only a committed publication owned by this durable pending run.
    if remote['generation'] != source['generation']:
        if remote['generation'] == source['generation'] + 1 and remote['run_id'] == source['pending_run']:
            with database.connect() as conn:
                conn.execute('UPDATE campus_sync_identity SET generation=?,pending_run=NULL WHERE singleton=1', (remote['generation'],))
            source['generation'] = remote['generation']
        else:
            raise ValueError('Sync paused: stale database copy or competing writer.')
    with database.connect() as conn:
        count = conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]
    if not count or count < len(remote['ids']) * .8:
        raise ValueError('Sync paused: empty or sharply reduced roster requires reconciliation.')
    with database.connect() as conn:
        records = {r['id']: violation_identity(dict(r)) for r in conn.execute('SELECT id,student_id,timestamp,violation_code FROM violations')}
    if any(r['id'] in records and records[r['id']] != r['identity'] for r in remote.get('records', [])):
        raise ValueError('Sync paused: local integer-ID collision; reconcile database copies.')
    receipts, cursor, blocked, drained = [], 0, set(), False
    for _ in range(10):  # bounded 250 commands; stable integer queue order
        page = client.call('actions?after=' + str(cursor))
        for action in page['actions']:
            cursor = max(cursor, action['sequence'])
            sid = action.get('student_id')
            ordering_key = (sid, action.get('kind', 'unknown'))
            if ordering_key in blocked:
                continue
            if action.get('error'):
                blocked.add(ordering_key)
                continue
            try:
                receipt = apply_action(database, action)
                receipts.append({'id': action['id'], **receipt})
                if receipt['status'] != 'applied':
                    blocked.add(ordering_key)
            except (ValueError, KeyError, TypeError):
                blocked.add(ordering_key)
        if not page['has_more']:
            drained = True
            break
    unresolved_appeals = any(q['kind'] == 'appeals' and q['status'] == 'rejected' and q['count'] for q in remote.get('queue', []))
    if drained and not blocked and not unresolved_appeals:
        with database.connect() as conn:
            conn.execute('UPDATE campus_sync_identity SET verified_through=? WHERE singleton=1', (remote['checked_at'],))
        database.process_expired_deadlines()
    from core.sync_status import write_status
    write_status(database.db_path, pending=page['pending'], oldest_pending=page.get('oldest_pending'), evidence_verified=0)
    evidence_count = 0
    students = []
    with frozen_database(database) as snapshot:
        with snapshot.connect() as conn:
            rows = [dict(r) for r in conn.execute('SELECT student_id,name,email,registration_pending FROM students')]
        for row in rows:
            row['email'] = hashlib.sha256(row['student_id'].encode()).hexdigest() + '@cbvms.invalid'
            data = student_snapshot(snapshot, row)
            with snapshot.connect() as conn:
                account = conn.execute('SELECT username,password_hash,must_change_password,first_login_completed_at,credential_version FROM student_accounts WHERE student_id=?', (row['student_id'],)).fetchone()
                records = {r['id']: dict(r) for r in conn.execute('SELECT id,student_id,timestamp,violation_code FROM violations WHERE student_id=?', (row['student_id'],))}
            if not account:
                raise ValueError('Sync paused: missing campus credentials; cloud accounts preserved.')
            data['account'] = dict(account)
            data['account']['must_change_password'] = bool(account['must_change_password'] or not data['account'].pop('first_login_completed_at'))
            for violation in data['snapshot']['violations']:
                violation['identity'] = violation_identity(records[violation['id']])
                if violation.get('has_snapshot'):
                    evidence = snapshot.get_student_original_evidence(violation['id'], row['student_id'])
                    blob = evidence.get('snapshot')
                    if not blob:
                        raise ValueError('Original evidence unavailable; publication paused.')
                    digest = hashlib.sha256(blob).hexdigest()
                    verified = client.call('evidence', {'student_id': row['student_id'], 'violation_id': violation['id'],
                        'generation': source['generation'], 'sha256': digest, 'file': base64.b64encode(blob).decode()})
                    if verified.get('sha256') != digest:
                        raise ValueError('Evidence verification failed')
                    violation['evidence_sha256'] = digest
                    evidence_count += 1
                    write_status(database.db_path, evidence_verified=evidence_count)
            students.append(data)
    run_id = str(uuid.uuid4())
    with database.connect() as conn:
        conn.execute('UPDATE campus_sync_identity SET pending_run=? WHERE singleton=1', (run_id,))
    result = client.call('publish', {'generation': source['generation'], 'run_id': run_id, 'students': students, 'receipts': receipts})
    if result.get('generation') != source['generation'] + 1 or result.get('run_id') != run_id:
        raise ValueError('Publication not verified')
    with database.connect() as conn:
        conn.execute('UPDATE campus_sync_identity SET generation=?,pending_run=NULL WHERE singleton=1', (result['generation'],))
    final = client.call('audit')
    pending_count = sum(q['count'] for q in final['queue'] if q['status'] == 'queued')
    oldest = min((q['oldest'] for q in final['queue'] if q['status'] == 'queued'), default=None)
    unresolved = sum(q['count'] for q in final['queue'] if q['status'] == 'rejected')
    write_status(database.db_path, state='conflict' if blocked or unresolved else 'pending' if pending_count else 'synced',
                 last_success=final['checked_at'], pending=pending_count, oldest_pending=oldest,
                 evidence_verified=evidence_count, conflicts=unresolved, message='Publication verified; submissions require campus review.' if blocked or unresolved else 'Publication and evidence verified.', generation=result['generation'])
    return len(students)


def start_cloud_sync(database):
    if not CONFIG.exists():
        return None
    stop = threading.Event()
    def run():
        while not stop.is_set():
            try:
                config = json.loads(CONFIG.read_text(encoding='utf-8'))
                if config.get('enabled') is not True:
                    from core.sync_status import write_status
                    write_status(database.db_path, state='paused', message='Uploads disabled. Review audit and source registration.')
                else:
                    sync_once(CBVMSDatabase(database.db_path), CloudClient(config), ROOT / 'data' / 'cloud-sync-state.json')
            except Exception as exc:
                logging.warning('Cloud portal sync failed (%s); review Sync settings.', type(exc).__name__)
            stop.wait(60)
    threading.Thread(target=run, daemon=True, name='cloud-portal-sync').start()
    return stop
