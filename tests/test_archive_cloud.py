import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from core.archive_cloud import revoke, FIELDS

ROOT = Path(__file__).resolve().parents[1]

class Transport:
    def __init__(self, conn): self.conn, self.lose_response = conn, False
    def query(self, sql, params):
        with self.conn:
            rows = [dict(r) for r in self.conn.execute(sql, params)]
        if self.lose_response and sql.startswith('INSERT'):
            self.lose_response = False
            raise TimeoutError('synthetic lost acknowledgment')
        return rows

class ArchiveCloudTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:'); self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        for p in sorted((ROOT/'sites/student-portal/drizzle').glob('*.sql')):
            if p.name > '0006_student_revocations.sql': continue  # legacy prepared protocol fixture
            self.c.executescript(p.read_text())
        self.t = Transport(self.c)
        self.c.execute("INSERT INTO campus_portal_imports VALUES ('import','source','payload','now',1,0,?)", (json.dumps({'S1':{'1':{'portal_violation_id':-1,'original_identity':'case'}}}),))
        for sid in ['S1', 'WINDOWS']:
            self.c.execute('INSERT INTO portal_students(id,email,name,snapshot,synced_at,active) VALUES(?,?,?,?,?,1)', (sid,sid+'@test.invalid',sid,'{"violations":[{"id":-1}]}','now'))
            self.c.execute('INSERT INTO portal_accounts(student_id,username,password_hash,credential_version) VALUES(?,?,?,1)',(sid,sid,'preserved-hash'))
            self.c.execute('INSERT INTO portal_sessions VALUES(?,?,?,?,?)',(sid,sid,'preserved-hash','csrf',9999999999999))
            self.c.execute('INSERT INTO portal_actions(id,student_id,kind,payload,created_at) VALUES(?,?,?,?,?)',(sid,sid,'password','{}','now'))
        self.c.commit()
        self.cmd=dict(zip(FIELDS,(str(uuid.uuid4()),'S1','import','source','payload','S1',1,0,1)))
    def tearDown(self): self.c.close()
    def test_timeout_recovery_and_idempotence(self):
        self.t.lose_response=True
        self.assertTrue(revoke(self.t,self.cmd)); self.assertTrue(revoke(self.t,self.cmd))
        self.assertEqual(self.c.execute('SELECT count(*) FROM portal_student_revocations').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT student_id FROM portal_sessions').fetchone()[0],'WINDOWS')
        self.assertEqual(self.c.execute("SELECT active FROM portal_students WHERE id='S1'").fetchone()[0],0)
        self.assertEqual(self.c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_stale_publication_replace_and_pending_action_cannot_reactivate(self):
        revoke(self.t,self.cmd)
        self.c.execute("UPDATE portal_students SET active=1,snapshot='{}' WHERE id='S1'")
        self.c.execute("INSERT OR REPLACE INTO portal_students(id,email,name,snapshot,synced_at) VALUES('S1','S1@test.invalid','changed','{}','later')")
        self.c.execute("UPDATE portal_accounts SET password_hash='new',pending_action_id=NULL WHERE student_id='S1'")
        self.c.execute("UPDATE portal_actions SET status='applied' WHERE student_id='S1'")
        self.assertEqual(self.c.execute("SELECT active,snapshot FROM portal_students WHERE id='S1'").fetchone()[:],(0,'{"violations":[{"id":-1}]}'))
        self.assertEqual(self.c.execute("SELECT password_hash FROM portal_accounts WHERE student_id='S1'").fetchone()[0],'preserved-hash')
        self.assertEqual(self.c.execute("SELECT status FROM portal_actions WHERE student_id='S1'").fetchone()[0],'queued')
        with self.assertRaises(sqlite3.IntegrityError): self.c.execute("INSERT INTO portal_sessions VALUES('new','S1','hash','csrf',9999999999999)")
    def test_wrong_source_identity_version_and_operation_conflicts(self):
        for field,value in [('source_checkpoint','wrong'),('student_id','WINDOWS'),('expected_username','WINDOWS'),('expected_credential_version',2)]:
            with self.subTest(field=field),self.assertRaises(sqlite3.IntegrityError): revoke(self.t,{**self.cmd,field:value})
        self.assertEqual(self.c.execute("SELECT active FROM portal_students WHERE id='S1'").fetchone()[0],1)
        revoke(self.t,self.cmd)
        with self.assertRaises(ValueError): revoke(self.t,{**self.cmd,'operation_id':str(uuid.uuid4())})
    def test_offline_outbox_recovers_after_restart(self):
        import tempfile
        from scripts.revoke_imported_student import LocalOutbox
        from core.archive_cloud import process_operation
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local.db'
            with sqlite3.connect(path) as conn:
                conn.executescript("CREATE TABLE students(student_id TEXT,is_archived INTEGER); CREATE TABLE student_archive_operations(operation_id TEXT,student_id TEXT,state TEXT,error TEXT,updated_at TEXT);")
                conn.execute("INSERT INTO students VALUES('S1',1)")
                conn.execute("INSERT INTO student_archive_operations VALUES(?,'S1','pending',NULL,'now')", (self.cmd['operation_id'],))
            class Offline:
                def query(self, *args): raise TimeoutError('offline')
            with self.assertRaises(TimeoutError):
                process_operation(LocalOutbox(path), self.cmd['operation_id'], Offline(), self.cmd)
            self.assertEqual(LocalOutbox(path).get_archive_operation(self.cmd['operation_id'])['state'], 'failed')
            # New objects simulate a restarted administrative reconciler.
            process_operation(LocalOutbox(path), self.cmd['operation_id'], Transport(self.c), dict(self.cmd))
            self.assertEqual(LocalOutbox(path).get_archive_operation(self.cmd['operation_id'])['state'], 'verified')
            LocalOutbox(path).set_archive_cloud_result(self.cmd['operation_id'], 'failed', 'stale error')
            self.assertEqual(LocalOutbox(path).get_archive_operation(self.cmd['operation_id'])['state'], 'verified')

    def test_remote_commit_recovers_when_local_ack_write_fails(self):
        from core.archive_cloud import process_operation
        class Local:
            state = 'pending'
            fail = True
            def get_archive_operation(inner, operation_id):
                return {'operation_id': operation_id, 'student_id': 'S1'}
            def set_archive_cloud_result(inner, operation_id, state, error=''):
                if inner.fail:
                    inner.fail = False
                    raise sqlite3.OperationalError('synthetic local lock')
                inner.state = state
        local = Local()
        with self.assertRaises(sqlite3.OperationalError):
            process_operation(local, self.cmd['operation_id'], self.t, self.cmd)
        self.assertEqual(local.state, 'pending')
        process_operation(local, self.cmd['operation_id'], Transport(self.c), self.cmd)
        self.assertEqual(local.state, 'verified')
        self.assertEqual(self.c.execute('SELECT count(*) FROM portal_student_revocations').fetchone()[0], 1)

    def test_receipts_and_history_are_immutable(self):
        revoke(self.t,self.cmd)
        for sql in ["INSERT OR REPLACE INTO campus_portal_imports SELECT * FROM campus_portal_imports", "INSERT OR REPLACE INTO portal_student_revocations SELECT * FROM portal_student_revocations", "INSERT INTO portal_actions(id,student_id,kind,payload,created_at) VALUES('race','S1','appeals','{}','now')", 'DELETE FROM campus_portal_imports','DELETE FROM portal_student_revocations',"DELETE FROM portal_students WHERE id='S1'", "DELETE FROM portal_accounts WHERE student_id='S1'", "DELETE FROM portal_actions WHERE student_id='S1'"]:
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError): self.c.execute(sql)

if __name__ == '__main__': unittest.main()
