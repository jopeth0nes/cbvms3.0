"""Read-only reconciliation. Never construct/initialize the application database."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

PROTOCOL = 2

@contextmanager
def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def identity(conn):
    try:
        row = conn.execute('SELECT * FROM campus_sync_identity WHERE singleton=1').fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def audit(path, client=None):
    with readonly(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        schema = '\n'.join(r[0] or '' for r in conn.execute('SELECT sql FROM sqlite_master ORDER BY name'))
        ids = {r[0] for r in conn.execute('SELECT student_id FROM students')}
        receipts = [dict(r) for r in conn.execute('SELECT id,status FROM cloud_portal_receipts')] if 'cloud_portal_receipts' in tables else []
        source = identity(conn)
        fingerprint = hashlib.sha256()
        for statement in conn.iterdump():
            fingerprint.update(statement.encode())
        evidence = {'missing': 0, 'mismatched': 0}
        for row in conn.execute('SELECT snapshot,snapshot_sha256 FROM violations'):
            if row['snapshot_sha256'] and not row['snapshot']:
                evidence['missing'] += 1
            elif row['snapshot'] and row['snapshot_sha256'] and hashlib.sha256(row['snapshot']).hexdigest() != row['snapshot_sha256']:
                evidence['mismatched'] += 1
        report = {'mode': 'read-only', 'protocol': PROTOCOL, 'source_registered': bool(source),
                  'local_students': len(ids), 'local_receipts': len(receipts),
                  'local_schema_sha256': hashlib.sha256(schema.encode()).hexdigest(),
                  'local_checkpoint': fingerprint.hexdigest(), 'local_evidence': evidence,
                  'conflicting_credentials': 'unavailable', 'conflicting_profiles': 'unavailable',
                  'missing_cloud_evidence': 'unavailable', 'mismatched_cloud_evidence': 'unavailable',
                  'local_user_version': conn.execute('PRAGMA user_version').fetchone()[0],
                  'proposed_deactivations': 0, 'production_apply_allowed': False,
                  'cloud': 'unavailable', 'unresolved': ['Campus authority and backups require review.']}
        if client:
            try:
                cloud = client.call('audit')
            except Exception:
                report['unresolved'].append('Cloud read unavailable or incompatible; no production plan can be approved.')
                return report
            cloud_ids = set(cloud['ids'])
            report.update(cloud='available', local_only=len(ids-cloud_ids), cloud_only=len(cloud_ids-ids),
                          proposed_additions=len(ids-cloud_ids), proposed_updates=len(ids & cloud_ids),
                          queue=cloud['queue'], cloud_protocol=cloud['protocol'],
                          awaiting_ack=sum(r['id'] in cloud['queued_ids'] for r in receipts),
                          compatible=bool(source and source['source_id'] == cloud['source_id'] and cloud['protocol'] == PROTOCOL))
            accounts = {r['student_id']: dict(r) for r in conn.execute('SELECT student_id,password_hash,credential_version FROM student_accounts')}
            names = {r['student_id']: hashlib.sha256(r['name'].encode()).hexdigest() for r in conn.execute('SELECT student_id,name FROM students')}
            report.update(cloud_generation=cloud['generation'],
                conflicting_credentials=sum(a['id'] in accounts and hashlib.sha256(accounts[a['id']]['password_hash'].encode()).hexdigest() != a['fingerprint'] for a in cloud.get('credentials', [])),
                pending_password_changes=sum(a['pending'] for a in cloud.get('credentials', [])),
                conflicting_profiles=sum(p['id'] in names and names[p['id']] != p['name'] for p in cloud.get('profiles', [])),
                missing_cloud_evidence=cloud.get('evidence_missing'), mismatched_cloud_evidence=cloud.get('evidence_mismatched'))
            report['unresolved'].append('Cloud-only students are preserved; overlapping snapshots require review.')
        return report


def backup(path, target):
    """SQLite backup API includes committed WAL. Restore-check without initializing."""
    target = Path(target)
    if target.exists():
        raise ValueError('Backup destination must not exist')
    with readonly(path) as source:
        with sqlite3.connect(target) as dest:
            source.backup(dest)
    with readonly(target) as restored:
        if restored.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup restore verification failed')
        if restored.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Backup contains foreign key violations')
    return {'backup': str(target), 'restore_check': 'passed'}
