"""Provider-owner deletion of one reconciled identity, with an atomic cloud receipt."""
import json

TABLE = '''CREATE TABLE IF NOT EXISTS campus_account_deletions (
 operation_id TEXT PRIMARY KEY,student_id TEXT NOT NULL,username TEXT NOT NULL,
 actor TEXT NOT NULL,reason TEXT NOT NULL,deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'''
TRIGGER = '''CREATE TRIGGER IF NOT EXISTS apply_campus_account_deletion
AFTER INSERT ON campus_account_deletions BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=NEW.student_id)
 OR EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=NEW.student_id)
 OR EXISTS(SELECT 1 FROM portal_lifecycle_operations WHERE student_id=NEW.student_id)
 THEN RAISE(ABORT,'Protected lifecycle history requires separate reconciliation') END;
 DELETE FROM portal_recovery_mail WHERE id IN (SELECT id FROM portal_recovery_challenges WHERE student_id=NEW.student_id);
 DELETE FROM portal_recovery_contact_proofs WHERE challenge_id IN (SELECT id FROM portal_recovery_challenges WHERE student_id=NEW.student_id);
 DELETE FROM portal_recovery_assisted_challenges WHERE challenge_id IN (SELECT id FROM portal_recovery_challenges WHERE student_id=NEW.student_id)
 OR approval_id IN (SELECT id FROM portal_recovery_assistance WHERE student_id=NEW.student_id);
 DELETE FROM portal_recovery_challenges WHERE student_id=NEW.student_id;
 DELETE FROM portal_recovery_assistance WHERE student_id=NEW.student_id;
 DELETE FROM portal_recovery_emails WHERE student_id=NEW.student_id;
 DELETE FROM portal_sessions WHERE student_id=NEW.student_id;
 DELETE FROM portal_preferences WHERE student_id=NEW.student_id;
 DELETE FROM portal_actions WHERE student_id=NEW.student_id;
 DELETE FROM campus_registration_owners WHERE student_id=NEW.student_id;
 DELETE FROM portal_academic_owner_updates WHERE student_id=NEW.student_id;
 DELETE FROM portal_accounts WHERE student_id=NEW.student_id;
 DELETE FROM portal_students WHERE id=NEW.student_id;
END'''


def recover(provider, operation_id, student_id, username):
    receipt = provider.query('SELECT student_id,username FROM campus_account_deletions WHERE operation_id=?', [operation_id])
    if not receipt:
        return False
    if len(receipt)!=1 or receipt[0] != dict(student_id=student_id,username=username):
        raise ValueError('Cloud deletion receipt does not match the selected identity.')
    for table,key in (('portal_students','id'),('portal_accounts','student_id'),('portal_sessions','student_id'),('portal_actions','student_id')):
        if provider.query(f'SELECT 1 FROM {table} WHERE {key}=?',[student_id]):
            raise ValueError('Cloud identity changed after deletion; local deletion stopped.')
    return True


def delete(provider, operation_id, student_id, username, expected_hash, expected_version, actor, reason):
    # A server-side trigger makes receipt creation and all deletes one statement.
    provider.query(TABLE,[])
    provider.query(TRIGGER,[])
    if recover(provider,operation_id,student_id,username):
        return
    provider.query('''INSERT INTO campus_account_deletions(operation_id,student_id,username,actor,reason)
        SELECT ?,?,?,?,? WHERE EXISTS(SELECT 1 FROM portal_accounts WHERE student_id=?
        AND username=? AND password_hash=? AND credential_version=?)''',
        [operation_id,student_id,username,actor,reason,student_id,username,expected_hash,expected_version])
    if not recover(provider,operation_id,student_id,username):
        raise ValueError('Cloud credentials changed; no deletion was confirmed.')
