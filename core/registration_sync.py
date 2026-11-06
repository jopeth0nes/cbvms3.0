"""Create-only website registration worker. Never replaces cloud accounts."""
import json
import logging
from pathlib import Path
import sqlite3
import threading
import urllib.request
from urllib.parse import urlsplit
from contextlib import closing

CONFIG = Path(__file__).resolve().parents[1] / 'data' / 'registration-sync.json'
FIELDS = ('student_id','name','email','college_department','course','report_year_level','report_section','student_status')


def load_config():
    try:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        origin = urlsplit(config['origin'])
        if (config.get('enabled') is not True or origin.scheme != 'https' or not origin.hostname
                or origin.username or origin.password or origin.path or origin.query or origin.fragment
                or not isinstance(config.get('key'), str) or len(config['key']) < 32):
            return None
        import uuid
        uuid.UUID(config['source_id'])
        return config
    except (OSError, ValueError, KeyError, TypeError):
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send(config, payload):
    request = urllib.request.Request(config['origin'] + '/api/campus/registrations',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + config['key'],
        'Content-Type': 'application/json', 'User-Agent': 'CBVMS-Campus-Registration/1'}, method='POST')
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        result = json.loads(response.read(16384))
    status = result.get('status')
    if status not in ('registered', 'existing_account', 'not_published'):
        raise ValueError('Unexpected registration response')
    return status


def run_once(database):
    config = load_config()
    if config is None or database.student_session_ref is not None:
        return {}
    with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('''CREATE TABLE IF NOT EXISTS website_registrations (
            student_pk INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
            account_id INTEGER NOT NULL, status TEXT NOT NULL, lifecycle_version INTEGER NOT NULL,
            checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        rows = conn.execute('''SELECT s.*,a.id AS account_id,a.username,a.password_hash,
            a.credential_version,a.must_change_password,a.first_login_completed_at,a.is_disabled
            FROM students s JOIN student_accounts a ON a.student_id=s.student_id
            LEFT JOIN website_registrations w ON w.student_pk=s.id AND w.account_id=a.id
            WHERE (w.student_pk IS NULL AND s.is_archived=0 AND a.is_disabled=0)
            OR (w.status='registered' AND w.lifecycle_version<>s.archive_version)
            ORDER BY s.id LIMIT 20''').fetchall()
    results = {}
    for row in rows:
        payload = dict(profile={k: row[k] or '' for k in FIELDS}, username=row['username'],
            password_hash=row['password_hash'], credential_version=row['credential_version'],
            must_change_password=bool(row['must_change_password'] or not row['first_login_completed_at']),
            source_id=config['source_id'], lifecycle_version=row['archive_version'], active=not bool(row['is_archived'] or row['is_disabled']))
        try:
            status = send(config, payload)
        except Exception:
            logging.getLogger(__name__).warning('One website registration is pending; continuing other registrations')
            results[row['id']] = 'pending'
            continue
        with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            with conn:
                # A deleted/replaced account must not receive an old acknowledgement.
                conn.execute('''INSERT INTO website_registrations(student_pk,account_id,status,lifecycle_version)
                    SELECT s.id,a.id,?,? FROM students s JOIN student_accounts a ON a.student_id=s.student_id
                    WHERE s.id=? AND a.id=?
                    ON CONFLICT(student_pk) DO UPDATE SET account_id=excluded.account_id,
                    status=excluded.status,lifecycle_version=excluded.lifecycle_version,checked_at=CURRENT_TIMESTAMP
                    WHERE website_registrations.account_id<>excluded.account_id
                    OR website_registrations.lifecycle_version<=excluded.lifecycle_version''',
                    (status,row['archive_version'],row['id'],row['account_id']))
        results[row['id']] = status
    return results


_lock = threading.Lock()
_running = set()


def email_note(database, student_id):
    """Called from the existing email worker, never the Tk callback."""
    if load_config() is None:
        return 'Website registration is not connected. These credentials are for the campus application.'
    try:
        run_once(database)
        with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
            row = conn.execute('''SELECT w.status FROM website_registrations w JOIN students s ON s.id=w.student_pk
                WHERE s.student_id=?''', (student_id,)).fetchone()
        if row and row[0] == 'registered':
            return 'Your account is available at ' + load_config()['origin'] + '. Change your temporary password at first login.'
        if row and row[0] == 'existing_account':
            return ('You already have a website account. Keep using your existing website password; '
                    'the new campus temporary password does not replace it.')
    except Exception:
        pass
    return 'Website registration is pending. Keep the campus application open; it retries automatically.'


def statuses(database):
    if database.student_session_ref is not None or load_config() is None:
        return None
    try:
        with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
            return dict(conn.execute('SELECT student_pk,status FROM website_registrations'))
    except sqlite3.OperationalError:
        return {}


def start(database):
    if database.student_session_ref is not None or load_config() is None:
        return
    key = str(database.db_path)
    with _lock:
        if key in _running:
            return
        _running.add(key)
    def work():
        while True:
            try:
                run_once(database)
                from core.registration_records import run_once as publish_records
                config = load_config()
                if config:
                    publish_records(database, config)
            except Exception:
                logging.getLogger(__name__).warning('Website registration pending; will retry automatically')
            threading.Event().wait(5)
    threading.Thread(target=work, name='website-registration', daemon=True).start()
