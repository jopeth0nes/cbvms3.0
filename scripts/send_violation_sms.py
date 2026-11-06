"""Explicit staff-authenticated first SMS attempt for an already-published violation."""
import argparse
import getpass
from pathlib import Path
import sys
import warnings
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database.db_manager import CBVMSDatabase
from database import sms_notifications
from core.sms_sender import configured


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--violation-id',type=int,required=True)
    args = parser.parse_args()
    if not configured():
        raise ValueError('Configure IPROG first with scripts/configure_iprog_sms.py.')
    db = CBVMSDatabase()
    username = input('Staff username: ').strip()
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        password = getpass.getpass('Staff password (hidden): ')
    token = db.create_staff_session(username,password)
    try:
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            actor = db._staff_actor(conn,token)
            if not actor or actor['role'] not in ('admin','superadmin'):
                raise PermissionError('A current staff account is required.')
            row = conn.execute("SELECT * FROM violations WHERE id=? AND appeal_opened_at IS NOT NULL AND status<>'dismissed'",(args.violation_id,)).fetchone()
            if not row:
                raise ValueError('Published, non-dismissed violation not found.')
            sms_notifications.queue(conn,db,row,schedule=False)
        sms_notifications.drain(db,violation_id=args.violation_id)
        with db.connect() as conn:
            row = conn.execute('SELECT status FROM violation_sms WHERE violation_id=?',(args.violation_id,)).fetchone()
        print('SMS status: '+(row[0] if row else 'not queued; check the registered number and active student status'))
        print('Accepted means IPROG queued the message, not confirmed handset delivery. Previous attempts are never repeated.')
    finally:
        db.revoke_staff_session(token)


if __name__ == '__main__':
    try:
        main()
    except (Exception,KeyboardInterrupt) as error:
        print('SMS attempt stopped. Check setup, staff credentials and violation ID. No credentials were displayed.',file=sys.stderr)
        sys.exit(1)
