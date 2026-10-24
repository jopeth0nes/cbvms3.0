"""Prepare an offline, additive campus-to-portal import bundle."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from core.cloud_sync import frozen_database, student_snapshot, violation_identity

CONTROL = ('campus_sync_control', 'campus_sync_publications')
PERSISTENT = ('portal_students', 'portal_accounts', 'portal_actions', 'portal_preferences')
# Preserve existing encodings accepted by both campus and deployed portal auth.
# Import never creates a weaker hash or rewrites a student's password.
HASH_RE = re.compile(r'(?:[0-9a-f]{64}|pbkdf2_sha256\$v1\$(?:[1-9][0-9]{5}|1[0-9]{6}|2000000)\$[0-9a-f]{32}\$[0-9a-f]{64})')
ENCODE_SAFE = "!~*'()-._"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _table(conn, table):
    return [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]


def _guard_digest(conn):
    return _digest({t: _table(conn, t) for t in PERSISTENT})


def _remap(value, mapping):
    if isinstance(value, list):
        return [_remap(x, mapping) for x in value]
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == 'violation_id' and child in mapping:
                result[key] = mapping[child]
                result['campus_violation_id'] = child
            else:
                result[key] = _remap(child, mapping)
        return result
    return value


def prepare(campus_path, cloud_restore_path, output_dir, import_id):
    """Build sensitive local files from a campus backup and cloud restore only."""
    campus_path, cloud_restore_path, output_dir = map(Path, (campus_path, cloud_restore_path, output_dir))
    try:
        import_id = str(uuid.UUID(str(import_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid import identifier') from None
    if output_dir.exists():
        raise ValueError('Output directory must be new')
    if not campus_path.is_file() or not cloud_restore_path.is_file():
        raise ValueError('Input backup is unavailable')
    output_dir.mkdir(mode=0o700, parents=True)
    os.chmod(output_dir, 0o700)
    try:
        with closing(sqlite3.connect(cloud_restore_path.resolve().as_uri() + '?mode=ro', uri=True)) as cloud:
            cloud.row_factory = sqlite3.Row
            required = set(PERSISTENT + CONTROL)
            if not required.issubset({r[0] for r in cloud.execute("SELECT name FROM sqlite_master WHERE type='table'")}):
                raise ValueError('Cloud restore schema is incomplete')
            control = cloud.execute('SELECT * FROM campus_sync_control WHERE singleton=1').fetchone()
            if not control:
                raise ValueError('Cloud checkpoint is unavailable')
            cloud_rows = {t: _table(cloud, t) for t in PERSISTENT}
            original_publications = _table(cloud, 'campus_sync_publications')
            ids = {r['id'] for r in cloud_rows['portal_students']}
            usernames = {r['username'] for r in cloud_rows['portal_accounts']}
            emails = {r['email'].casefold() for r in cloud_rows['portal_students']}
            original_digest = _guard_digest(cloud)
            max_negative = min([0] + [int(v['id']) for s in cloud_rows['portal_students'] for v in json.loads(s['snapshot']).get('violations', []) if int(v['id']) < 0])
        from core.sync_audit import readonly
        with readonly(campus_path):
            digest_file = hashlib.sha256()
            with campus_path.open('rb') as source_stream:
                for block in iter(lambda: source_stream.read(1024 * 1024), b''):
                    digest_file.update(block)
            source_file_hash = digest_file.hexdigest()
        rows_out, mappings, evidence, account_rows = [], {}, [], []
        with frozen_database(type('ReadOnlySource', (), {'db_path': campus_path})()) as frozen:
            with frozen.connect() as conn:
                source_rows = [dict(r) for r in conn.execute('SELECT student_id,name,email,registration_pending FROM students ORDER BY student_id')]
                for integrity_row in conn.execute('SELECT snapshot,snapshot_sha256 FROM violations'):
                    stored_hash = integrity_row['snapshot_sha256']
                    blob = bytes(integrity_row['snapshot'] or b'')
                    if stored_hash and (not blob or hashlib.sha256(blob).hexdigest() != stored_hash):
                        raise ValueError('Original campus evidence checksum mismatch')
                logical_hash = hashlib.sha256()
                for statement in conn.iterdump():
                    logical_hash.update(statement.encode('utf-8'))
                    logical_hash.update(b'\n')
                source_fp = logical_hash.hexdigest()
                used = set()
                for row in source_rows:
                    sid = row['student_id']
                    portal_email = hashlib.sha256(sid.encode()).hexdigest() + '@cbvms.invalid'
                    snap = student_snapshot(frozen, {**row, 'email': portal_email})
                    account = conn.execute('SELECT username,password_hash,must_change_password,first_login_completed_at,credential_version FROM student_accounts WHERE student_id=?', (sid,)).fetchone()
                    if not account or not HASH_RE.fullmatch(account['password_hash'] or '') or not account['username'] or not isinstance(account['credential_version'], int) or account['credential_version'] < 0:
                        raise ValueError('Campus credential data is incomplete')
                    if sid in ids or account['username'] in usernames or portal_email.casefold() in emails:
                        raise ValueError('Campus identity conflicts with existing portal records')
                    if account['username'] in used:
                        raise ValueError('Campus usernames are not unique')
                    used.add(account['username'])
                    records = {v['id']: dict(v) for v in conn.execute('SELECT id,student_id,timestamp,violation_code FROM violations WHERE student_id=?', (sid,))}
                    violations = snap['snapshot'].get('violations', [])
                    mapping = {}
                    for violation in violations:
                        old = int(violation['id'])
                        if old <= 0 or old not in records:
                            raise ValueError('Campus violation identifiers are invalid')
                        max_negative -= 1
                        mapping[str(old)] = {'portal_violation_id': max_negative, 'original_identity': violation_identity(records[old])}
                        violation['campus_record_id'] = old
                        violation['campus_violation_id'] = old
                        violation['identity'] = violation_identity(records[old])
                    mappings[sid] = mapping
                    snap['snapshot'] = _remap(snap['snapshot'], {int(k): v['portal_violation_id'] for k, v in mapping.items()})
                    # Keep a stable explicit map for campus-side reconciliation.
                    for violation in snap['snapshot'].get('violations', []):
                        violation['id'] = mapping[str(violation['campus_record_id'])]['portal_violation_id']
                    rows_out.append(snap)
                    account_rows.append({'student_id': sid, 'username': account['username'], 'password_hash': account['password_hash'],
                        'must_change_password': int(bool(account['must_change_password'] or not account['first_login_completed_at'])),
                        'credential_version': account['credential_version']})
                # Evidence is deliberately extracted only when checksums are present and match.
                for record in rows_out:
                    sid = record['id']
                    for violation in record['snapshot'].get('violations', []):
                        if violation.get('has_snapshot'):
                            old = violation['campus_record_id']
                            row = conn.execute('SELECT snapshot,snapshot_sha256 FROM violations WHERE id=? AND student_id=?', (old, sid)).fetchone()
                            blob = bytes(row['snapshot'] or b'') if row else b''
                            digest = hashlib.sha256(blob).hexdigest()
                            if not blob:
                                raise ValueError('Original campus evidence is unavailable')
                            stored = row['snapshot_sha256'] if row and 'snapshot_sha256' in row.keys() else None
                            if stored and stored != digest:
                                raise ValueError('Original campus evidence checksum mismatch')
                            key = f"original-v2/{quote(sid, safe=ENCODE_SAFE)}/{violation['id']}/{digest}"
                            violation['evidence_sha256'] = digest
                            rel = f"objects/{digest}"
                            obj = output_dir / rel
                            obj.parent.mkdir(mode=0o700, exist_ok=True)
                            obj.write_bytes(blob)
                            os.chmod(obj, 0o600)
                            evidence.append({'key': key, 'sha256': digest, 'path': rel, 'student_id': sid,
                                'owner_id': sid, 'parent_id': str(violation['id']), 'kind': 'original',
                                'checksum_origin': 'stored' if stored else 'computed'})
                for record in rows_out:
                    owned_case_ids = {int(v['id']) for v in record['snapshot'].get('violations', [])}
                    def validate_case_refs(value):
                        if isinstance(value, dict):
                            for key, child in value.items():
                                if key == 'violation_id' and child is not None and (not isinstance(child, int) or child not in owned_case_ids):
                                    raise ValueError('Campus case relationship is invalid')
                                validate_case_refs(child)
                        elif isinstance(value, list):
                            for child in value:
                                validate_case_refs(child)
                    validate_case_refs(record['snapshot'])
                exported_ids = {r['student_id'] for r in source_rows}
                all_evidence = conn.execute('SELECT * FROM evidence_files ORDER BY id').fetchall()
                joined = conn.execute('''SELECT e.*,a.id AS parent_appeal,a.student_id AS appeal_owner,
                    v.student_id AS violation_owner,a.violation_id FROM evidence_files e
                    JOIN appeals a ON a.id=e.appeal_id JOIN violations v ON v.id=a.violation_id''').fetchall()
                if len(joined) != len(all_evidence):
                    raise ValueError('Supporting evidence ownership or parent validation failed')
                per_student_appeals = {r['id']: {int(a['id']): a for a in r['snapshot'].get('appeals', [])} for r in rows_out}
                for item in joined:
                    sid = item['appeal_owner']
                    if sid != item['student_id'] or sid != item['violation_owner'] or sid not in exported_ids or int(item['appeal_id']) != int(item['parent_appeal']):
                        raise ValueError('Supporting evidence ownership or parent validation failed')
                    blob = bytes(item['file_data'] or b'')
                    if not blob:
                        raise ValueError('Supporting evidence bytes are unavailable')
                    digest = hashlib.sha256(blob).hexdigest()
                    stored = item['file_sha256'] if 'file_sha256' in item.keys() else None
                    if stored and stored != digest:
                        raise ValueError('Supporting evidence checksum mismatch')
                    aid, fid = int(item['parent_appeal']), int(item['id'])
                    storage_key = f"campus-import/{import_id}/supporting/{quote(sid, safe=ENCODE_SAFE)}/{aid}/{fid}/{digest}"
                    rel = f"objects/{digest}"
                    obj = output_dir / rel
                    obj.parent.mkdir(mode=0o700, exist_ok=True)
                    obj.write_bytes(blob)
                    os.chmod(obj, 0o600)
                    appeal = per_student_appeals[sid].get(aid)
                    if appeal is None:
                        raise ValueError('Supporting evidence appeal is absent from snapshot')
                    appeal.setdefault('supporting_evidence', []).append({'id': fid, 'filename': item['filename'],
                        'sha256': digest, 'storage_key': storage_key,
                        'checksum_origin': 'stored' if stored else 'computed'})
                    evidence.append({'key': storage_key, 'sha256': digest, 'path': rel, 'student_id': sid,
                        'owner_id': sid, 'parent_id': str(aid), 'kind': 'supporting'})
                prepared_at = datetime.now(timezone.utc).isoformat()
        bundle = {'version': 1, 'import_id': import_id, 'source_file_sha256': source_file_hash,
            'source_fingerprint': source_fp, 'source_checkpoint': source_fp, 'prepared_at': prepared_at,
            'original_publications': original_publications,
            'expected_control': {k: control[k] for k in control.keys()},
            'original_cloud_fingerprint': original_digest, 'original_cloud_rows': cloud_rows,
            'students': rows_out, 'accounts': account_rows, 'mapping': mappings,
            'evidence': evidence}
        payload = {k: v for k, v in bundle.items() if k != 'payload_sha256'}
        bundle['payload_sha256'] = _digest(payload)
        path = output_dir / 'bundle.json'
        path.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':')), encoding='utf-8')
        os.chmod(path, 0o600)
        return {'student_count': len(rows_out), 'evidence_count': len(evidence), 'bundle': str(path)}
    except Exception:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


def build_batch(bundle):
    """Return parameterized INSERT statements for one atomic D1 batch."""
    bundle_root = None
    if isinstance(bundle, (str, Path)):
        bundle_path = Path(bundle)
        bundle_root = bundle_path.resolve().parent
        bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
    if bundle.get('payload_sha256') != _digest({k: v for k, v in bundle.items() if k != 'payload_sha256'}):
        raise ValueError('Bundle integrity check failed')
    try:
        if str(uuid.UUID(bundle['import_id'])) != bundle['import_id']:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Bundle identity is invalid') from None
    students = bundle.get('students')
    accounts = bundle.get('accounts')
    if not isinstance(students, list) or not isinstance(accounts, list) or len(students) != len(accounts):
        raise ValueError('Bundle identity rows are invalid')
    student_ids, usernames, emails = set(), set(), set()
    forbidden = {'encoding', 'photo', 'profile_photo', 'password_hash', 'password', 'face_encoding', 'face_embedding', 'embedding', 'embeddings', 'biometric', 'capture_id', 'capture_hashes', 'provenance', 'session_token', 'file_data'}
    def validate_public(value):
        if isinstance(value, dict):
            if forbidden.intersection(value):
                raise ValueError('Bundle snapshot contains private local fields')
            for child in value.values():
                validate_public(child)
        elif isinstance(value, list):
            for child in value:
                validate_public(child)
    for student, account in zip(students, accounts):
        sid, username, email = student.get('id'), account.get('username'), student.get('email')
        if not all(isinstance(v, str) and v for v in (sid, username, email)) or sid != account.get('student_id'):
            raise ValueError('Bundle identity rows are invalid')
        if sid in student_ids or username in usernames or email.casefold() in emails:
            raise ValueError('Bundle identities are not unique')
        if not HASH_RE.fullmatch(account.get('password_hash', '')) or not isinstance(account.get('credential_version'), int) or account['credential_version'] < 0:
            raise ValueError('Bundle credentials are invalid')
        validate_public(student.get('snapshot'))
        student_ids.add(sid); usernames.add(username); emails.add(email.casefold())
    for sid, mapping in bundle.get('mapping', {}).items():
        if sid not in student_ids or not isinstance(mapping, dict):
            raise ValueError('Bundle violation mapping is invalid')
        for old, target in mapping.items():
            if not old.isdigit() or int(old) <= 0 or not isinstance(target, dict) or not isinstance(target.get('portal_violation_id'), int) or target['portal_violation_id'] >= 0 or not re.fullmatch(r'[0-9a-f]{64}', target.get('original_identity', '')):
                raise ValueError('Bundle violation mapping is invalid')
    mapped_ids = [target['portal_violation_id'] for m in bundle.get('mapping', {}).values() for target in m.values()]
    if len(mapped_ids) != len(set(mapped_ids)):
        raise ValueError('Bundle violation mapping is invalid')
    for student in students:
        sid = student['id']
        mapping = bundle.get('mapping', {}).get(sid, {})
        by_negative = {v['portal_violation_id']: (int(old), v['original_identity']) for old, v in mapping.items()}
        roots = student.get('snapshot', {}).get('violations', [])
        if {v.get('id') for v in roots} != set(by_negative):
            raise ValueError('Bundle violations do not match their identity map')
        for violation in roots:
            original, identity = by_negative[violation['id']]
            if violation.get('campus_record_id') != original or violation.get('campus_violation_id') != original or violation.get('identity') != identity:
                raise ValueError('Bundle violation mapping is invalid')
        def validate_refs(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == 'violation_id' and child is not None and child not in by_negative:
                        raise ValueError('Bundle case relationship is invalid')
                    validate_refs(child)
            elif isinstance(value, list):
                for child in value:
                    validate_refs(child)
        validate_refs(student.get('snapshot'))
    evidence_paths, evidence_keys = {}, set()
    for item in bundle.get('evidence', []):
        rel = Path(item.get('path', ''))
        if rel.is_absolute() or '..' in rel.parts or rel.parts != ('objects', item.get('sha256')) or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', '')) or not item.get('key') or item.get('student_id') not in student_ids or item.get('owner_id') != item.get('student_id') or item.get('kind') not in {'original', 'supporting'}:
            raise ValueError('Bundle evidence descriptor is invalid')
        if (rel.as_posix() in evidence_paths and evidence_paths[rel.as_posix()] != item['sha256']) or item['key'] in evidence_keys:
            raise ValueError('Bundle evidence descriptor is invalid')
        evidence_paths[rel.as_posix()] = item['sha256']; evidence_keys.add(item['key'])
        if bundle_root is not None:
            path = (bundle_root / rel).resolve()
            hasher = hashlib.sha256()
            if bundle_root not in path.parents or not path.is_file():
                raise ValueError('Bundle evidence checksum mismatch')
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    hasher.update(block)
            if hasher.hexdigest() != item['sha256']:
                raise ValueError('Bundle evidence checksum mismatch')
    cp = bundle['expected_control']
    if set(bundle['original_cloud_rows']) != set(PERSISTENT):
        raise ValueError('Bundle cloud guard tables are invalid')
    control_columns = {'singleton', 'source_id', 'writer_id', 'protocol', 'generation',
                       'run_id', 'checked_at', 'minimum_snapshot_version', 'published_student_count'}
    if set(cp) != control_columns:
        raise ValueError('Bundle control schema requires review')
    # CHECK forces a constraint error when any checkpoint or original row differs.
    predicates = [f'c."{column}" IS ?' for column in cp]
    guard_params = [cp[column] for column in cp]
    for table, rows in bundle['original_cloud_rows'].items():
        predicates.append(f'(SELECT count(*) FROM {table})=?')
        guard_params.append(len(rows))
    sql = "INSERT INTO campus_portal_imports(import_id,source_checkpoint,payload_sha256,imported_at,student_count,evidence_count,mapping_json) VALUES(CASE WHEN EXISTS(SELECT 1 FROM campus_sync_control c WHERE " + ' AND '.join(predicates) + ") THEN ? ELSE NULL END,?,?,?,?,?,?)"
    params = guard_params + [bundle['import_id'], bundle['source_checkpoint'], bundle['payload_sha256'], bundle['prepared_at'], len(bundle['students']), len(bundle['evidence']), json.dumps(bundle['mapping'], sort_keys=True, separators=(',', ':'))]
    out = [{'sql': sql, 'params': params}]
    receipt_cols = 'import_id,source_checkpoint,payload_sha256,imported_at,student_count,evidence_count,mapping_json'
    receipt_values = ','.join('?' for _ in range(7))
    receipt = [bundle['import_id'], bundle['source_checkpoint'],
               bundle['payload_sha256'], bundle['prepared_at'], len(bundle['students']), len(bundle['evidence']),
               json.dumps(bundle['mapping'], sort_keys=True, separators=(',', ':'))]
    for table, rows in bundle['original_cloud_rows'].items():
        out.append({'sql': f'INSERT INTO campus_portal_imports({receipt_cols}) SELECT {receipt_values} WHERE (SELECT count(*) FROM {table})<>?', 'params': receipt + [len(rows)]})
        for row in rows:
            columns = tuple(row)
            where = ' AND '.join(f'"{column}" IS ?' for column in columns)
            out.append({'sql': f'INSERT INTO campus_portal_imports({receipt_cols}) SELECT {receipt_values} WHERE NOT EXISTS (SELECT 1 FROM "{table}" WHERE {where})',
                        'params': receipt + [row[column] for column in columns]})
    publications = bundle.get('original_publications', [])
    out.append({'sql': f'INSERT INTO campus_portal_imports({receipt_cols}) SELECT {receipt_values} WHERE (SELECT count(*) FROM campus_sync_publications)<>?', 'params': receipt + [len(publications)]})
    for row in publications:
        columns = tuple(row)
        where = ' AND '.join(f'"{column}" IS ?' for column in columns)
        out.append({'sql': f'INSERT INTO campus_portal_imports({receipt_cols}) SELECT {receipt_values} WHERE NOT EXISTS (SELECT 1 FROM campus_sync_publications WHERE {where})', 'params': receipt + [row[column] for column in columns]})
    for row in students:
        out.append({'sql': 'INSERT INTO portal_students(id,email,name,snapshot,synced_at,active) VALUES(?,?,?,?,?,?)', 'params': [row['id'], row['email'], row['name'], json.dumps(row['snapshot'], ensure_ascii=False, separators=(',', ':')), bundle['prepared_at'], int(row['active'])]})
    for row in bundle['accounts']:
        out.append({'sql': 'INSERT INTO portal_accounts(student_id,username,password_hash,must_change_password,credential_version) VALUES(?,?,?,?,?)', 'params': [row['student_id'], row['username'], row['password_hash'], row['must_change_password'], row['credential_version']]})
    return out
