"""Opt-in profile projection; never reconstructs missing biometric enrollment."""
from __future__ import annotations

import json
from core.incremental_sync import enqueue, _payload, SUPPORTED


def prepare(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS incremental_profile_cache (
      student_id TEXT PRIMARY KEY, version INTEGER NOT NULL, profile TEXT NOT NULL)''')


def save_profile(database, student_id, patch, *, actor_token, device_id, expected_version):
    """Commit the provisional business change and outbox together, without I/O."""
    _payload(SUPPORTED,patch)
    with database.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor=database._staff_actor(conn,actor_token)
        if actor is None or actor['role'] not in ('admin','superadmin'):
            raise PermissionError('An authenticated administrator is required')
        student=conn.execute('SELECT is_archived FROM students WHERE student_id=?',(student_id,)).fetchone()
        if student is None or student['is_archived']:raise ValueError('Student is unavailable')
        # Explicitly adopted mapping/version is required; enqueue must not create
        # an authority mapping just because a local integer or name matches.
        mapping=conn.execute("SELECT version FROM incremental_entity_versions WHERE entity_type='student' AND entity_id=?",(student_id,)).fetchone()
        if mapping is None or mapping[0]!=expected_version:raise ValueError('Shared profile baseline requires reconciliation')
        if conn.execute("SELECT 1 FROM incremental_outbox WHERE entity_type='student' AND entity_id=? AND state<>'accepted' LIMIT 1",(student_id,)).fetchone():
            raise ValueError('Wait for the pending profile edit or resolve its conflict')
        operation_id=enqueue(conn,device_id=device_id,entity_type='student',entity_id=student_id,
            expected_version=expected_version,operation_type=SUPPORTED,payload=patch)
        assignments=','.join(k+'=?' for k in sorted(patch))  # validated exact allowlist above
        conn.execute('UPDATE students SET '+assignments+' WHERE student_id=?',(*[patch[k] for k in sorted(patch)],student_id))
    return operation_id


def apply_profile(conn, operation_id, payload):
    if set(payload)!={'entity_type','entity_id','version','name','email'} or payload['entity_type']!='student':
        raise ValueError('Unsupported profile journal event')
    sid,version=payload['entity_id'],payload['version']
    if not isinstance(sid,str) or not sid or type(version) is not int or version<1:raise ValueError('Invalid profile identity/version')
    _payload(SUPPORTED,{k:payload[k] for k in ('name','email')})
    prepare(conn)
    old=conn.execute('SELECT version,profile FROM incremental_profile_cache WHERE student_id=?',(sid,)).fetchone()
    encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    if old and (version<old['version'] or (version==old['version'] and encoded!=old['profile'])):
        raise ValueError('Changed or stale profile journal version')
    conn.execute('INSERT INTO incremental_profile_cache VALUES(?,?,?) ON CONFLICT(student_id) DO UPDATE SET version=excluded.version,profile=excluded.profile',(sid,version,encoded))
    # Missing local records remain explicitly metadata-only. Registration and
    # recognition gallery provenance are never synthesized from a portal record.
    pending=conn.execute("SELECT 1 FROM incremental_outbox WHERE entity_type='student' AND entity_id=? AND state<>'accepted' LIMIT 1",(sid,)).fetchone()
    if not pending:
        conn.execute('UPDATE students SET name=?,email=? WHERE student_id=? AND is_archived=0',(payload['name'],payload['email'],sid))
    conn.execute("INSERT INTO incremental_entity_versions VALUES('student',?,?) ON CONFLICT(entity_type,entity_id) DO UPDATE SET version=MAX(version,excluded.version)",(sid,version))
