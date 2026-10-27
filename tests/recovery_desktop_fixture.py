"""Consume a synthetic Worker recovery event against an offline desktop fixture."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.cloud_sync import apply_action
from database.db_manager import CBVMSDatabase
from auth.passwords import verify_password


def main():
    action = json.load(sys.stdin)
    action['payload'] = json.loads(action['payload']) if isinstance(action['payload'], str) else action['payload']
    with tempfile.TemporaryDirectory() as directory:
        db = CBVMSDatabase(Path(directory) / 'offline.db')
        db.initialize()
        db.insert_student('S1', 'Synthetic student', 'BSIT', '3A', b'', b'',
                          account_username='S1', account_password='Initial personal password')
        with db.connect() as conn:
            conn.execute('UPDATE student_accounts SET password_hash=?,credential_version=?,must_change_password=0,first_login_completed_at=datetime(\'now\') WHERE student_id=?',
                         (action['payload']['expected_hash'], action['payload']['expected_version'], 'S1'))
            account = dict(conn.execute("SELECT * FROM student_accounts WHERE student_id='S1'").fetchone())
        session = db.create_student_session(account)
        # Offline devices cannot learn about a cloud reset until this delivery.
        assert db.get_student_session(session['session_token'])
        assert apply_action(db, action)['status'] == 'applied'
        assert apply_action(db, action)['status'] == 'applied'
        with db.connect() as conn:
            updated = conn.execute("SELECT * FROM student_accounts WHERE student_id='S1'").fetchone()
            assert updated['password_hash'] == action['payload']['password_hash']
            assert updated['credential_version'] == action['payload']['expected_version'] + 1
            assert not updated['must_change_password']
            assert conn.execute('SELECT COUNT(*) FROM student_sessions').fetchone()[0] == 0
            assert conn.execute('SELECT COUNT(*) FROM cloud_portal_receipts').fetchone()[0] == 1
            assert verify_password('Recovered personal passphrase', updated['password_hash'])
        print('Offline desktop: actual Worker event reconciled once; local sessions revoked.')


if __name__ == '__main__':
    main()
