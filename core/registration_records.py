"""Background, per-account record publication; never replaces the cloud roster."""
import base64
from contextlib import closing
import hashlib
import json
import logging
import sqlite3
import urllib.request
import time

# Per-process cache: retries/restarts remain safe, with a periodic full heartbeat.
_published = {}


def snapshot_digest(snapshot):
    def stable(value):
        if isinstance(value, dict):
            return {k:stable(v) for k,v in value.items() if k!='appeal_remaining_seconds'}
        if isinstance(value, list):
            return [stable(v) for v in value]
        return value
    return hashlib.sha256(json.dumps(stable(snapshot),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def post(config, payload):
    from core.registration_sync import NoRedirect
    request = urllib.request.Request(config['origin'] + '/api/campus/records',
        data=json.dumps(payload, separators=(',', ':')).encode(), method='POST',
        headers={'Authorization':'Bearer '+config['key'], 'Content-Type':'application/json',
                 'User-Agent':'CBVMS-Campus-Registration/1'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(15*1024*1024+1)
        if len(raw)>15*1024*1024:
            raise ValueError('Record response too large')
        result = json.loads(raw)
    if result.get('status') not in ('synced','unchanged','evidence_verified','actions','acknowledged'):
        raise ValueError('Unexpected record publication response')
    return result


def import_appeals(database, config, sequence):
    from core.cloud_sync import apply_action
    with database.connect() as conn:
        rows = [dict(r) for r in conn.execute('''SELECT s.id,s.student_id,s.archive_version,a.id AS account_id,a.username
            FROM students s JOIN student_accounts a ON a.student_id=s.student_id
            JOIN website_registrations w ON w.student_pk=s.id AND w.account_id=a.id
            WHERE w.status='registered' AND w.lifecycle_version=s.archive_version
            AND s.is_archived=0 AND s.registration_pending=0 AND a.is_disabled=0''')]
    for owner in rows:
        envelope=dict(student_id=owner['student_id'],username=owner['username'],source_id=config['source_id'],
                      lifecycle_version=owner['archive_version'],sequence=sequence)
        try:
            # Bounded one-picture responses, preserving pending appeals on outages.
            for _ in range(10):
                actions=post(config,dict(envelope,kind='appeal_actions')).get('actions')
                if actions == []:
                    break
                if not isinstance(actions,list) or len(actions)!=1:
                    raise ValueError('Invalid appeal batch')
                action=actions[0]
                if action.get('student_id')!=owner['student_id'] or action.get('kind')!='appeals':
                    raise ValueError('Appeal owner mismatch')
                receipt=apply_action(database,action,expected_owner=owner)
                post(config,dict(envelope,kind='appeal_receipt',action_id=action['id'],status=receipt['status']))
        except Exception:
            logging.getLogger(__name__).warning('Website appeal import pending; automatic retry scheduled')


def run_once(database, config):
    if database.student_session_ref is not None:
        return {}
    from core.cloud_sync import frozen_database, student_snapshot, violation_identity
    # A durable counter is allocated before taking the read snapshot, so an older
    # concurrent worker can never replace a newer snapshot on the website.
    with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS website_record_sequence (singleton INTEGER PRIMARY KEY CHECK(singleton=1), sequence INTEGER NOT NULL)')
        conn.execute('''CREATE TABLE IF NOT EXISTS website_evidence_receipts (
            student_pk INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL, digest TEXT NOT NULL, violation_id INTEGER NOT NULL,
            PRIMARY KEY(student_pk,source_id,digest,violation_id))''')
        with conn:
            conn.execute('INSERT INTO website_record_sequence VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET sequence=sequence+1')
            sequence = conn.execute('SELECT sequence FROM website_record_sequence').fetchone()[0]
    results = {}
    import_appeals(database, config, sequence)
    with frozen_database(database) as frozen:
        with frozen.connect() as conn:
            rows = [dict(r) for r in conn.execute('''SELECT s.*,a.username FROM students s
                JOIN student_accounts a ON a.student_id=s.student_id
                JOIN website_registrations w ON w.student_pk=s.id AND w.account_id=a.id
                WHERE w.status='registered' AND w.lifecycle_version=s.archive_version
                AND s.is_archived=0 AND a.is_disabled=0 AND s.registration_pending=0''')]
        for row in rows:
            sid = row['student_id']
            envelope = dict(student_id=sid, username=row['username'], source_id=config['source_id'],
                            lifecycle_version=row['archive_version'], sequence=sequence)
            try:
                snapshot = student_snapshot(frozen, row)['snapshot']
                cache_key = (str(database.db_path),row['id'],row['username'],config['source_id'],row['archive_version'])
                fingerprint = snapshot_digest(snapshot)
                previous = _published.get(cache_key)
                if previous and previous[0] == fingerprint and time.monotonic()-previous[1]<60:
                    results[sid] = 'unchanged'
                    continue
                with frozen.connect() as conn:
                    records = {r['id']:dict(r) for r in conn.execute(
                        'SELECT id,student_id,timestamp,violation_code FROM violations WHERE student_id=?',(sid,))}
                for violation in snapshot['violations']:
                    violation['identity'] = violation_identity(records[violation['id']])
                    if violation.get('has_snapshot'):
                        blob = frozen.get_student_original_evidence(violation['id'], sid).get('snapshot')
                        if not blob:
                            raise ValueError('Original evidence unavailable')
                        digest = hashlib.sha256(blob).hexdigest()
                        receipt_key = (row['id'], config['source_id'], digest, violation['id'])
                        with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
                            cached = conn.execute('SELECT 1 FROM website_evidence_receipts WHERE student_pk=? AND source_id=? AND digest=? AND violation_id=?', receipt_key).fetchone()
                        if not cached:
                            result = post(config, dict(envelope, kind='evidence', violation_id=violation['id'],
                                sha256=digest, file=base64.b64encode(blob).decode('ascii')))
                            if result.get('sha256') != digest:
                                raise ValueError('Evidence verification failed')
                            with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
                                conn.execute('PRAGMA foreign_keys=ON')
                                with conn:
                                    conn.execute('INSERT OR IGNORE INTO website_evidence_receipts VALUES(?,?,?,?)', receipt_key)
                        violation['evidence_sha256'] = digest
                result = post(config, dict(envelope, kind='records', snapshot=snapshot))
                results[sid] = result['status']
                _published[cache_key] = (fingerprint,time.monotonic())
            except Exception:
                # Remote evidence may have been restored/removed; revalidate on retry.
                with closing(sqlite3.connect(database.db_path, timeout=database.timeout)) as conn:
                    with conn:
                        conn.execute('DELETE FROM website_evidence_receipts WHERE student_pk=?',(row['id'],))
                logging.getLogger(__name__).warning('Website records pending; automatic retry scheduled')
                results[sid] = 'pending'
    return results
