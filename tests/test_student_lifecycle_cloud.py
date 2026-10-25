import json,sqlite3,unittest,uuid
from pathlib import Path
from core.student_lifecycle_cloud import deliver,FIELDS
from tests.test_archive_cloud import Transport
ROOT=Path(__file__).resolve().parents[1]
class LifecycleCloudTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row;self.c.execute('PRAGMA foreign_keys=ON')
  for p in sorted((ROOT/'sites/student-portal/drizzle').glob('*.sql')):self.c.executescript(p.read_text())
  self.t=Transport(self.c)
  self.c.execute("INSERT INTO campus_portal_imports VALUES('import','source','payload','now',1,0,?)",(json.dumps({'S1':{'10':{'portal_violation_id':-1}}}),))
  self.c.execute("INSERT INTO portal_lifecycle_authorities VALUES('authority','source',1,'superadmin','superadmin',1)")
  for sid in ('S1','WINDOWS'):
   self.c.execute('INSERT INTO portal_students(id,email,name,snapshot,synced_at,active) VALUES(?,?,?,?,?,1)',(sid,sid+'@test',sid,'{"violations":[{"id":-1}]}','now'))
   self.c.execute('INSERT INTO portal_accounts(student_id,username,password_hash) VALUES(?,?,?)',(sid,sid,'unchanged'))
   self.c.execute('INSERT INTO portal_sessions VALUES(?,?,?,?,?)',(sid,sid,'unchanged','csrf',9999999999999))
   self.c.execute('INSERT INTO portal_actions(id,student_id,kind,payload,created_at) VALUES(?,?,?,?,?)',(sid,sid,'password','{}','now'))
  self.c.commit()
 def tearDown(self):self.c.close()
 def cmd(self,version,action):return dict(zip(FIELDS,(str(uuid.uuid4()),'S1','import','source','payload','S1',version-1,version,action,'authority','Synthetic reason')))
 def active(self):return self.c.execute("SELECT active FROM portal_students WHERE id='S1'").fetchone()[0]
 def test_recycle_restore_ordering_and_receipts(self):
  a,b,c=self.cmd(1,'recycle'),self.cmd(2,'restore'),self.cmd(3,'recycle')
  with self.assertRaises(sqlite3.IntegrityError):deliver(self.t,b)
  deliver(self.t,a);self.assertEqual(self.active(),0)
  deliver(self.t,b);self.assertEqual(self.active(),1)
  deliver(self.t,a);self.assertEqual(self.active(),1)
  deliver(self.t,c);deliver(self.t,b);self.assertEqual(self.active(),0)
  self.assertEqual(self.c.execute("SELECT count(*) FROM portal_sessions WHERE student_id='S1'").fetchone()[0],0)
  self.assertEqual(self.c.execute("SELECT count(*) FROM portal_sessions WHERE student_id='WINDOWS'").fetchone()[0],1)
  self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(),[])
 def test_independent_blocks_before_and_during_recycle_survive_restore(self):
  self.c.execute("UPDATE portal_students SET active=0 WHERE id='S1'");self.c.commit()
  deliver(self.t,self.cmd(1,'recycle'));deliver(self.t,self.cmd(2,'restore'));self.assertEqual(self.active(),0)
  self.c.execute("UPDATE portal_students SET active=1 WHERE id='S1'");self.assertEqual(self.active(),0)
 def test_later_independent_block_is_not_cleared(self):
  deliver(self.t,self.cmd(1,'recycle'))
  self.c.execute("UPDATE portal_students SET active=0 WHERE id='S1'");self.c.commit()
  deliver(self.t,self.cmd(2,'restore'));self.assertEqual(self.active(),0)
 def test_timeout_idempotency_and_payload_conflict(self):
  a=self.cmd(1,'recycle');self.t.lose_response=True;deliver(self.t,a);deliver(self.t,a)
  with self.assertRaises(ValueError):deliver(self.t,{**a,'reason':'changed'})
  self.assertEqual(self.c.execute('SELECT count(*) FROM portal_lifecycle_operations').fetchone()[0],1)
 def test_authority_and_ownership_enforced(self):
  for change in ({'authority_id':'ordinary-admin'},{'student_id':'WINDOWS'},{'source_checkpoint':'wrong'}):
   with self.assertRaises(sqlite3.IntegrityError):deliver(self.t,{**self.cmd(1,'recycle'),**change})
  self.c.execute("UPDATE portal_lifecycle_authorities SET enabled=0");self.c.commit()
  with self.assertRaises(sqlite3.IntegrityError):deliver(self.t,self.cmd(1,'recycle'))
  self.assertEqual(self.active(),1)
 def test_publications_replace_and_pending_actions_are_preserved(self):
  deliver(self.t,self.cmd(1,'recycle'))
  self.c.execute("UPDATE portal_students SET active=1,snapshot='{}' WHERE id='S1'")
  self.c.execute("UPDATE portal_accounts SET password_hash='reset' WHERE student_id='S1'")
  self.c.execute("UPDATE portal_actions SET status='applied' WHERE student_id='S1'")
  self.assertEqual(self.active(),0)
  self.assertEqual(self.c.execute("SELECT password_hash FROM portal_accounts WHERE student_id='S1'").fetchone()[0],'unchanged')
  self.assertEqual(self.c.execute("SELECT status FROM portal_actions WHERE student_id='S1'").fetchone()[0],'queued')
  for sql in ['INSERT OR REPLACE INTO portal_lifecycle_operations SELECT * FROM portal_lifecycle_operations','DELETE FROM portal_lifecycle_operations','DELETE FROM campus_portal_imports']:
   with self.assertRaises(sqlite3.IntegrityError):self.c.execute(sql)
  self.c.commit();deliver(self.t,self.cmd(2,'restore'))
  self.assertEqual(self.c.execute("SELECT snapshot FROM portal_students WHERE id='S1'").fetchone()[0],'{"violations":[{"id":-1}]}')
if __name__=='__main__':unittest.main()
