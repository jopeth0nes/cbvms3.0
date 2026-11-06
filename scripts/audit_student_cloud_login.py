"""Compare one campus/cloud identity read-only, without printing credentials."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.revoke_imported_student import Provider


def normalized(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value or '')
                            if not unicodedata.combining(c)).casefold().split())


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--student-id', required=True)
    args = parser.parse_args()
    provider = Provider('88e619a8bc929cbec00901fdadd3edc1',
                        '640d2258-ca44-45b3-b83a-5e1f0caec663',
                        ROOT / '.runtime/cloud-config/.wrangler/config/default.toml')
    conn = sqlite3.connect((ROOT / 'data/cbvms.db').as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        local = conn.execute('''SELECT s.name,s.is_archived,s.registration_pending,a.*
            FROM students s JOIN student_accounts a ON a.student_id=s.student_id
            WHERE s.student_id=?''', (args.student_id,)).fetchone()
        local_count = conn.execute('SELECT count(*) FROM students').fetchone()[0]
        remote = provider.query('''SELECT p.name,p.active,a.* FROM portal_students p
            JOIN portal_accounts a ON a.student_id=p.id WHERE p.id=?''', [args.student_id])
        if not local or len(remote) != 1:
            print(json.dumps(dict(local_account_exists=bool(local), cloud_matches=len(remote))))
            return
        remote = remote[0]
        lifecycle = provider.query('SELECT recycled,access_blocked FROM portal_student_lifecycle WHERE student_id=?', [args.student_id])
        report = dict(names_match=normalized(local['name']) == normalized(remote['name']),
            usernames_match=local['username'] == remote['username'],
            password_hashes_match=local['password_hash'] == remote['password_hash'],
            campus_matches_pending_cloud_base=local['password_hash'] == remote.get('pending_base_hash'),
            campus_credential_version=local['credential_version'],
            cloud_credential_version=remote['credential_version'],
            pending_cloud_password_change=bool(remote.get('pending_action_id')),
            campus_password_change_required=bool(local['must_change_password']),
            cloud_active=bool(remote['active']), cloud_lifecycle=lifecycle,
            local_students=local_count)
        print(json.dumps(report, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Read-only login comparison failed; no records were changed.', file=sys.stderr)
        sys.exit(1)
