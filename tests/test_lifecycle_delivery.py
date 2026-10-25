"""End-to-end local superadmin outbox to isolated cloud SQL dispatcher."""
import json,sqlite3,unittest,uuid
from unittest.mock import patch
from auth.auth_manager import AuthManager
from tests.test_portal_import import PortalImportTests,MIGRATIONS
from tests.test_archive_cloud import Transport
from core.portal_import import build_batch,verify_imported_rows
from core.student_lifecycle_cloud import authority_id
from scripts.lifecycle_dispatcher import run
class DeliveryTests(unittest.TestCase):
 def setUp(self):
  f=PortalImportTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
  f._student_case();f._prepare();self.bundle=json.loads((f.output/'bundle.json').read_text())
  self.c=sqlite3.connect(f.cloud);self.c.row_factory=sqlite3.Row;self.addCleanup(self.c.close)
  for item in build_batch(self.bundle):self.c.execute(item['sql'],item['params'])
  self.c.commit();self.c.executescript((MIGRATIONS/'0007_student_lifecycle.sql').read_text())
  auth=AuthManager(f.db).authenticate('superadmin','superadmin123');self.token=auth['staff_session_token']
  with f.db.connect() as conn:self.actor=dict(conn.execute("SELECT id,username FROM users WHERE role='superadmin'").fetchone())
  self.authority=authority_id(self.bundle['source_checkpoint'],self.actor['id'],self.actor['username'])
  self.c.execute('INSERT INTO portal_lifecycle_authorities VALUES(?,?,?,?,?,1)',(self.authority,self.bundle['source_checkpoint'],self.actor['id'],self.actor['username'],'superadmin'));self.c.commit()
  self.pk=f.db.get_student_by_student_id('00123')['id']
  self.config={'local_db':str(f.campus),'import_bundle':str(f.output/'bundle.json')}
 def change(self,action,version):
  method=self.f.db.recycle_student if action=='recycle' else self.f.db.restore_student
  return method(self.pk,'00123',self.token,version,'Synthetic lifecycle reason',str(uuid.uuid4()))
 def deliver(self,transport=None):
  with patch('scripts.lifecycle_dispatcher.RefreshingProvider',return_value=transport or Transport(self.c)):
   return run(self.config)
 def test_offline_recycle_restore_restart_delivery_and_import_retry(self):
  a=self.change('recycle',0);b=self.change('restore',1)
  class Offline:
   def query(self,*args):raise TimeoutError('offline fixture')
  result=self.deliver(Offline());self.assertEqual(result['failed'],1);self.assertEqual(result['deferred'],1)
  self.assertEqual(self.f.db.get_student(self.pk)['is_archived'],0)
  result=self.deliver();self.assertEqual(result['verified'],2)
  self.assertEqual(self.deliver()['verified'],0)
  for op in (a,b):self.assertEqual(self.f.db.get_archive_operation(self.token, op['operation_id'])['state'],'verified')
  self.assertEqual(self.c.execute("SELECT active FROM portal_students WHERE id='00123'").fetchone()[0],1)
  def rows(t):return [dict(r) for r in self.c.execute('SELECT * FROM '+t)]
  self.assertTrue(verify_imported_rows(self.bundle,rows('campus_portal_imports'),rows('portal_students'),rows('portal_accounts'),rows('portal_student_revocations'),rows('portal_lifecycle_operations'),rows('portal_student_lifecycle')))
  self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(),[])
 def test_timeout_after_commit_and_role_downgrade(self):
  self.change('recycle',0);t=Transport(self.c);t.lose_response=True
  self.assertEqual(self.deliver(t)['verified'],1)
  self.change('restore',1)
  with self.f.db.connect() as conn:conn.execute("UPDATE users SET role='admin' WHERE id=?",(self.actor['id'],))
  self.assertEqual(self.deliver()['failed'],1)
  self.assertEqual(self.c.execute("SELECT active FROM portal_students WHERE id='00123'").fetchone()[0],0)
if __name__=='__main__':unittest.main()
