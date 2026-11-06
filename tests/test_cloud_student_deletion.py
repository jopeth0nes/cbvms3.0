"""Atomic cloud deletion and recovery on disposable synthetic SQLite data."""
import sqlite3
import unittest
from core.cloud_student_deletion import delete


class Transport:
    def __init__(self, conn):self.conn=conn
    def query(self, sql, params):
        with self.conn:
            return [dict(r) for r in self.conn.execute(sql,params).fetchall()]


class CloudDeletionTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.row_factory=sqlite3.Row
        self.transport=Transport(self.conn)
        self.conn.execute('CREATE TABLE portal_students(id TEXT PRIMARY KEY)')
        self.conn.execute('CREATE TABLE portal_accounts(student_id TEXT PRIMARY KEY,username TEXT,password_hash TEXT,credential_version INTEGER)')
        for name in ('portal_student_lifecycle','portal_student_revocations','portal_lifecycle_operations',
            'portal_recovery_emails','portal_sessions','portal_preferences','portal_actions',
            'campus_registration_owners','portal_academic_owner_updates'):
            self.conn.execute(f'CREATE TABLE {name}(student_id TEXT)')
        self.conn.execute('CREATE TABLE portal_recovery_challenges(id TEXT,student_id TEXT)')
        self.conn.execute('CREATE TABLE portal_recovery_assistance(id TEXT,student_id TEXT)')
        self.conn.execute('CREATE TABLE portal_recovery_mail(id TEXT)')
        self.conn.execute('CREATE TABLE portal_recovery_contact_proofs(challenge_id TEXT)')
        self.conn.execute('CREATE TABLE portal_recovery_assisted_challenges(challenge_id TEXT,approval_id TEXT)')
        for sid in ('S1','S2'):
            self.conn.execute('INSERT INTO portal_students VALUES(?)',(sid,))
            self.conn.execute('INSERT INTO portal_accounts VALUES(?,?,?,1)',(sid,sid,'synthetic-hash'))
            self.conn.execute('INSERT INTO portal_sessions VALUES(?)',(sid,))
            self.conn.execute('INSERT INTO portal_actions VALUES(?)',(sid,))
        self.conn.commit()

    def remove(self, expected_hash='synthetic-hash'):
        delete(self.transport,'synthetic-operation','S1','S1',expected_hash,1,'superadmin','Confirmed synthetic deletion')

    def test_target_only_and_retry_is_idempotent(self):
        self.remove();self.remove()
        self.assertEqual([r[0] for r in self.conn.execute('SELECT id FROM portal_students')],['S2'])
        self.assertEqual([r[0] for r in self.conn.execute('SELECT student_id FROM portal_actions')],['S2'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM campus_account_deletions').fetchone()[0],1)

    def test_failure_rolls_back_receipt_and_all_deletes(self):
        self.conn.execute("CREATE TRIGGER failure BEFORE DELETE ON portal_accounts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.remove()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM portal_sessions').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM campus_account_deletions').fetchone()[0],0)

    def test_old_receipt_cannot_delete_a_re_registered_identity(self):
        self.remove()
        self.conn.execute("INSERT INTO portal_students VALUES('S1')")
        self.conn.execute("INSERT INTO portal_accounts VALUES('S1','S1','new-synthetic-hash',1)")
        self.conn.commit()
        with self.assertRaises(ValueError):self.remove()
        self.assertEqual(self.conn.execute("SELECT password_hash FROM portal_accounts WHERE student_id='S1'").fetchone()[0],'new-synthetic-hash')

    def test_changed_credentials_or_protected_history_stop_deletion(self):
        with self.assertRaises(ValueError):self.remove('wrong-hash')
        self.conn.execute("INSERT INTO portal_student_lifecycle VALUES('S1')");self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.remove()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM portal_accounts').fetchone()[0],2)
