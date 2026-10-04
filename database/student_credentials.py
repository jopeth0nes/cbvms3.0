"""Shared student authentication, restricted sessions, and atomic credential changes."""
import hashlib
import secrets
import time

from auth.passwords import hash_password, verify_password, validate_new_password

SETUP_TTL = 10 * 60
SESSION_TTL = 8 * 3600


class SessionExpired(ValueError):
    pass


def migrate_credentials(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(student_accounts)')}
    # Old versions kept no durable, trustworthy completion history. Only adding
    # the column assigns the one-time requirement; startup never resets flags.
    for name, declaration in {
        'must_change_password': 'INTEGER NOT NULL DEFAULT 1',
        'first_login_completed_at': 'TEXT',
        'password_changed_at': 'TEXT',
        'credential_version': 'INTEGER NOT NULL DEFAULT 1',
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE student_accounts ADD COLUMN {name} {declaration}')
    conn.execute('''CREATE TABLE IF NOT EXISTS student_sessions (
        token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
        credential_version INTEGER NOT NULL, restricted INTEGER NOT NULL,
        expires REAL NOT NULL, csrf TEXT NOT NULL,
        FOREIGN KEY(account_id) REFERENCES student_accounts(id) ON DELETE CASCADE)''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_student_sessions_account ON student_sessions(account_id)')


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class StudentCredentials:
    def verify_student_account(self, username, password):
        """Credential check only: does not complete setup or authorize portal access."""
        with self.connect() as conn:
            row = conn.execute('''SELECT sa.*, s.name AS display_name FROM student_accounts sa
                LEFT JOIN students s ON s.student_id=sa.student_id WHERE sa.username=?''',
                ((username or '').strip(),)).fetchone()
        if not row or not verify_password(password, row['password_hash']):
            return None
        result = dict(row)
        if not row['password_hash'].startswith('pbkdf2_sha256$v1$'):
            upgraded = hash_password(password)
            with self.connect() as conn:
                cursor = conn.execute('UPDATE student_accounts SET password_hash=? WHERE id=? AND password_hash=?',
                                      (upgraded, row['id'], row['password_hash']))
                if cursor.rowcount != 1:
                    return None  # A concurrent reset wins over this credential check.
        result.pop('password_hash')
        result['display_name'] = result['display_name'] or result['student_id']
        result['must_change_password'] = bool(result['must_change_password'] or not result['first_login_completed_at'])
        return result

    def create_student_session(self, account):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM student_accounts WHERE id=? AND credential_version=?',
                               (account['id'], account['credential_version'])).fetchone()
            if not row:
                raise SessionExpired('Credentials changed. Please sign in again.')
            token = self._insert_session(conn, row)
        return self.get_student_session(token)

    @staticmethod
    def _insert_session(conn, account):
        restricted = bool(account['must_change_password'] or not account['first_login_completed_at'])
        token = secrets.token_urlsafe(32)
        now = time.time()
        conn.execute('DELETE FROM student_sessions WHERE expires<=?', (now,))
        conn.execute('INSERT INTO student_sessions VALUES (?,?,?,?,?,?)',
                     (token_digest(token), account['id'], account['credential_version'], int(restricted),
                      now + (SETUP_TTL if restricted else SESSION_TTL), secrets.token_urlsafe(32)))
        return token

    @staticmethod
    def _session_row(conn, token):
        row = conn.execute('''SELECT sa.*, ss.restricted, ss.expires, ss.csrf, s.name AS display_name
            FROM student_sessions ss JOIN student_accounts sa ON sa.id=ss.account_id
            LEFT JOIN students s ON s.student_id=sa.student_id
            WHERE ss.token_hash=? AND ss.expires>? AND ss.credential_version=sa.credential_version''',
            (token_digest(token), time.time())).fetchone()
        if not row:
            raise SessionExpired('Your session expired or credentials changed. Please sign in again.')
        return row

    def get_student_session(self, token, *, require_full=False):
        with self.connect() as conn:
            row = self._session_row(conn, token)
        restricted = bool(row['restricted'] or row['must_change_password'] or not row['first_login_completed_at'])
        if require_full and restricted:
            raise SessionExpired('Set your new password before entering the student portal.')
        return dict(session_token=token, role='student', username=row['username'], student_id=row['student_id'],
                    display_name=row['display_name'] or row['student_id'], name=row['display_name'] or row['student_id'],
                    must_change_password=restricted, credential_version=row['credential_version'],
                    expires=row['expires'], csrf=row['csrf'])

    def revoke_student_session(self, token):
        with self.connect() as conn:
            conn.execute('DELETE FROM student_sessions WHERE token_hash=?', (token_digest(token),))

    def change_student_password(self, token, password, confirmation, *, current_password=None, cancel=None):
        # Read identity exclusively from the authenticated token. No form ID is accepted.
        with self.connect() as conn:
            row = self._session_row(conn, token)
        validate_new_password(password, confirmation, username=row['username'], student_id=row['student_id'])
        restricted = row['restricted'] or row['must_change_password'] or not row['first_login_completed_at']
        if not restricted and not verify_password(current_password, row['password_hash']):
            raise ValueError('Current password is incorrect.')
        if verify_password(password, row['password_hash']):
            raise ValueError('New password must be different from your current or temporary password.')
        encoded = hash_password(password)  # Expensive work outside the write transaction.
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            latest = self._session_row(conn, token)
            if latest['credential_version'] != row['credential_version']:
                raise SessionExpired('Credentials changed. Please sign in again.')
            if cancel and cancel.is_set():
                raise ValueError('Password change cancelled. Please sign in again.')
            conn.execute('''UPDATE student_accounts SET password_hash=?, must_change_password=0,
                first_login_completed_at=COALESCE(first_login_completed_at,datetime('now')),
                password_changed_at=datetime('now'), credential_version=credential_version+1 WHERE id=?''',
                (encoded, row['id']))
            conn.execute('DELETE FROM student_sessions WHERE account_id=?', (row['id'],))
            updated = conn.execute('SELECT * FROM student_accounts WHERE id=?', (row['id'],)).fetchone()
            new_token = self._insert_session(conn, updated)
        # Only return success after the context manager has committed.
        return self.get_student_session(new_token, require_full=True)
