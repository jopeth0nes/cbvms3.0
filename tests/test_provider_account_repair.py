import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from core.provider_account_repair import repair_batch


class ProviderAccountRepairTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.addCleanup(self.c.close)
        for p in sorted((Path(__file__).resolve().parents[1]/'sites/student-portal/drizzle').glob('*.sql')):
            self.c.executescript(p.read_text())
        for sid in ('S1','S2'):
            self.c.execute('INSERT INTO portal_students VALUES(?,?,?,?,?,1)',(sid,sid+'@test',sid,'{"violations":[]}','now'))
            self.c.execute('INSERT INTO portal_accounts(student_id,username,password_hash,credential_version,pending_action_id) VALUES(?,?,?,?,?)',(sid,sid,'a'*64,1,sid))
            self.c.execute('INSERT INTO portal_actions(id,student_id,kind,payload,created_at) VALUES(?,?,?,?,?)',(sid,sid,'password','{}','now'))
            self.c.execute('INSERT INTO portal_sessions VALUES(?,?,?,?,?)',(sid,sid,'hash','csrf',9999999999999))
        self.c.commit()
        self.args=dict(operation_id=str(uuid.uuid4()),account=dict(self.c.execute("SELECT * FROM portal_accounts WHERE student_id='S1'").fetchone()),
            student=dict(self.c.execute("SELECT * FROM portal_students WHERE id='S1'").fetchone()),password_hash='b'*64,version=2,
            snapshot='{"violations":[],"enrollment_status":"Enrolled in current term"}',actor='superadmin',reason='Owner approved temporary credential reset')

    def apply(self):
        with self.c:
            for s in repair_batch(**self.args):self.c.execute(s['sql'],s['params'])

    def test_reset_requires_setup_revokes_sessions_retains_event_and_other_account(self):
        self.apply()
        a=self.c.execute("SELECT * FROM portal_accounts WHERE student_id='S1'").fetchone()
        self.assertEqual((a['password_hash'],a['credential_version'],a['must_change_password'],a['pending_action_id']),('b'*64,2,1,None))
        self.assertEqual(self.c.execute("SELECT status FROM portal_actions WHERE id='S1'").fetchone()[0],'rejected')
        self.assertEqual(self.c.execute('SELECT student_id FROM portal_sessions').fetchall()[0][0],'S2')
        self.assertEqual(self.c.execute("SELECT password_hash FROM portal_accounts WHERE student_id='S2'").fetchone()[0],'a'*64)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM portal_actions WHERE kind='admin_credential_reset'").fetchone()[0],1)
        with self.assertRaises(sqlite3.IntegrityError):self.apply() # Recover receipt; never replay mutations.
        self.assertEqual(self.c.execute("SELECT credential_version FROM portal_accounts WHERE student_id='S1'").fetchone()[0],2)

    def test_concurrent_password_change_blocks_every_write(self):
        self.c.execute("UPDATE portal_accounts SET password_hash=? WHERE student_id='S1'",('c'*64,));self.c.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.apply()
        self.assertEqual(self.c.execute('SELECT count(*) FROM portal_sessions').fetchone()[0],2)
        self.assertEqual(self.c.execute("SELECT status FROM portal_actions WHERE id='S1'").fetchone()[0],'queued')
        self.assertEqual(self.c.execute("SELECT snapshot FROM portal_students WHERE id='S1'").fetchone()[0],self.args['student']['snapshot'])

    def test_inactive_account_blocks_reset(self):
        self.c.execute("UPDATE portal_students SET active=0 WHERE id='S1'");self.c.commit()
        with self.assertRaises(sqlite3.IntegrityError):self.apply()
        self.assertEqual(self.c.execute("SELECT password_hash FROM portal_accounts WHERE student_id='S1'").fetchone()[0],'a'*64)

    def test_unrelated_snapshot_edit_is_rejected(self):
        self.args['snapshot']='{"violations":[{"id":99}]}'
        with self.assertRaises(ValueError):self.apply()

    def test_older_revision_is_rejected(self):
        self.args['version']=1
        with self.assertRaises(ValueError):self.apply()
