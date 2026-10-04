"""Create an authenticated full session in disposable test databases only."""
from auth.auth_manager import AuthManager


def portal_session(db, sid):
    with db.connect() as conn:
        row = conn.execute('SELECT username FROM student_accounts WHERE student_id=?', (sid,)).fetchone()
    if not row:
        db.insert_student_account(sid, sid, 'fixture temporary')
        username = sid
    else:
        username = row[0]
        db.reset_student_password(sid, 'fixture temporary')
    pending = AuthManager(db).authenticate(username, 'fixture temporary')
    return db.change_student_password(pending['session_token'], 'fixture personal passphrase',
                                      'fixture personal passphrase')['session_token']
