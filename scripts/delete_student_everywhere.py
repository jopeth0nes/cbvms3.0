"""Explicit provider-owner reconciliation for a recycled student. Default: read-only.

Requires independently restored cloud/campus backups in --backup-dir. It never
uses the limited registration key to claim an existing website account.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database.db_manager import CBVMSDatabase
from database.student_deletion import _purge_student_conn
from core.cloud_student_deletion import delete, recover
from scripts.revoke_imported_student import Provider
from scripts.audit_student_cloud_login import normalized

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--student-id',required=True)
    parser.add_argument('--backup-dir',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    for filename in ('campus.db','cloud-restore.db'):
        with sqlite3.connect((args.backup_dir/filename).resolve().as_uri()+'?mode=ro',uri=True) as check:
            if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or check.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Backup restoration verification failed.')
    db=CBVMSDatabase()
    with db.connect() as conn:
        student=conn.execute('SELECT * FROM students WHERE student_id=?',(args.student_id,)).fetchone()
        account=conn.execute('SELECT id,username FROM student_accounts WHERE student_id=?',(args.student_id,)).fetchone()
        operation=conn.execute("SELECT operation_id FROM student_archive_operations WHERE student_id=? AND operation_type='recycle' ORDER BY lifecycle_version DESC LIMIT 1",(args.student_id,)).fetchone()
    if not student or not student['is_archived'] or not account or not operation:
        raise ValueError('Select a recycled student with an intact account and authenticated lifecycle operation.')
    proof=db.get_lifecycle_operation_for_provider(operation['operation_id'])
    if not proof or proof['lifecycle_version']!=student['archive_version']:
        raise ValueError('Current authenticated superadmin recycle proof is required.')
    provider=Provider('88e619a8bc929cbec00901fdadd3edc1','640d2258-ca44-45b3-b83a-5e1f0caec663',ROOT/'.runtime/cloud-config/.wrangler/config/default.toml')
    remote=provider.query('SELECT p.name,p.snapshot,a.username,a.password_hash,a.credential_version FROM portal_students p JOIN portal_accounts a ON a.student_id=p.id WHERE p.id=?',[args.student_id])
    deletion_id=str(uuid.uuid5(uuid.UUID(operation['operation_id']),'confirmed-permanent-deletion'))
    reason='Owner confirmed permanent deletion of desktop and website account and related records.'
    if remote:
        remote=remote[0]
        if remote['username']!=account['username'] or normalized(remote['name'])!=normalized(student['name']):
            raise ValueError('Local/cloud identity conflict; deletion stopped.')
        snapshot=json.loads(remote['snapshot'])
        if snapshot.get('violations') or snapshot.get('appeals'):
            raise ValueError('Cloud evidence/history needs a separate evidence-aware deletion plan.')
        for table in ('portal_student_lifecycle','portal_student_revocations','portal_lifecycle_operations'):
            if provider.query(f'SELECT 1 FROM {table} WHERE student_id=?',[args.student_id]):
                raise ValueError('Protected cloud lifecycle history requires separate reconciliation.')
    if not args.apply:
        print('Preflight passed: one recycled local student and matching website account. Backups verified. No data deleted.')
        return
    if remote:
        delete(provider,deletion_id,args.student_id,account['username'],remote['password_hash'],remote['credential_version'],proof['actor'],reason)
    elif not recover(provider,deletion_id,args.student_id,account['username']):
        raise ValueError('Cloud absence alone is not a verified deletion receipt.')
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        fresh=conn.execute('SELECT is_archived,archive_version FROM students WHERE id=? AND student_id=?',(student['id'],args.student_id)).fetchone()
        current=conn.execute('SELECT id,username FROM student_accounts WHERE student_id=?',(args.student_id,)).fetchone()
        if not fresh or not fresh['is_archived'] or fresh['archive_version']!=student['archive_version'] or dict(current or {})!=dict(account):
            raise ValueError('Local student changed; cloud receipt retained for recovery.')
        actor=conn.execute('SELECT id,username,role FROM users WHERE id=?',(proof['actor_id'],)).fetchone()
        if not actor or actor['role']!='superadmin':
            raise PermissionError('Original superadmin authority is no longer valid.')
        _purge_student_conn(conn,student['id'],args.student_id,student['archive_version'],actor,reason)
    print('Selected student deleted from desktop and website. Other students were not targeted. Deletion audit retained.')


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('Deletion stopped: '+type(error).__name__+'. Backups and any cloud receipt retained; no secrets displayed.',file=sys.stderr)
        sys.exit(1)
