"""Explicit provider-admin repair; never exposed through a student HTTP route.

The caller must have owner approval to replace credentials, submit the statements
as one atomic batch, and verify the durable receipt before updating local state.
"""
import hashlib
import json
import uuid
from core.portal_import import HASH_RE


def repair_batch(*, operation_id, account, student, password_hash, version, snapshot, actor, reason):
    operation_id = str(uuid.UUID(operation_id))
    sid = account['student_id']
    if (not actor or not reason.strip() or not HASH_RE.fullmatch(password_hash)
            or type(version) is not int or version <= account['credential_version']
            or student['id'] != sid or student['active'] != 1):
        raise ValueError('Invalid reviewed account repair')
    # Preserve all nonacademic paths. This operation does not import case history.
    before, after = json.loads(student['snapshot']), json.loads(snapshot)
    fields = {'academic_terms', 'term', 'enrollment_status', 'term_history'}
    if {k:v for k,v in before.items() if k not in fields} != {k:v for k,v in after.items() if k not in fields}:
        raise ValueError('Unrelated snapshot changes are forbidden')
    payload = json.dumps(dict(actor=actor, reason=reason, credential_version=version,
        credential_fingerprint=hashlib.sha256(password_hash.encode()).hexdigest(),
        superseded_action=account.get('pending_action_id')), sort_keys=True)
    # Fresh receipt is the compare-and-swap fence. Replays fail on its primary key;
    # callers recover by reading the existing receipt instead of replaying writes.
    statements = [{'sql': """INSERT INTO portal_actions(id,student_id,kind,payload,created_at,status,result)
        VALUES(?,?,'admin_credential_reset',CASE WHEN EXISTS(
        SELECT 1 FROM portal_accounts a JOIN portal_students s ON s.id=a.student_id
        WHERE a.student_id=? AND a.username=? AND a.password_hash=? AND a.credential_version=?
        AND a.pending_action_id IS ? AND s.active=1 AND s.snapshot=?
        AND NOT EXISTS(SELECT 1 FROM portal_student_lifecycle l WHERE l.student_id=a.student_id)
        ) THEN ? ELSE NULL END,strftime('%Y-%m-%dT%H:%M:%fZ','now'),'applied',?)""",
        'params': [operation_id,sid,sid,account['username'],account['password_hash'],
                   account['credential_version'],account.get('pending_action_id'),student['snapshot'],payload,reason]}]
    fence = "EXISTS(SELECT 1 FROM portal_actions WHERE id=? AND student_id=? AND kind='admin_credential_reset' AND payload=?)"
    guard = [operation_id,sid,payload]
    def add(sql, params):
        statements.append({'sql':sql+' AND '+fence, 'params':params+guard})
    add('''UPDATE portal_accounts SET password_hash=?,credential_version=?,must_change_password=1,
        pending_base_hash=NULL,pending_action_id=NULL WHERE student_id=?''',[password_hash,version,sid])
    add("UPDATE portal_actions SET status='rejected',result=? WHERE id=? AND student_id=? AND status='queued'",
        ['Superseded by owner-approved credential reset.',account.get('pending_action_id'),sid])
    add('DELETE FROM portal_sessions WHERE student_id=?',[sid])
    add('UPDATE portal_students SET snapshot=? WHERE id=?',[snapshot,sid])
    return statements
