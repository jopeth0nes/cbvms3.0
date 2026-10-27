import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.campus_client import CampusClient, CampusCycle
from core.incremental_sync import migrate, apply_page
from database.shared_profiles import save_profile, apply_profile
from database.db_manager import CBVMSDatabase


class Response(io.BytesIO):
    status=200


class ClientTests(unittest.TestCase):
    def test_https_and_redirect_boundary(self):
        for origin in ['http://portal.test','https://user:password@portal.test','https://portal.test/api','https://portal.test?x=1']:
            with self.assertRaises(ValueError):CampusClient(origin,lambda:{})
        from core.campus_client import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,None,{},'https://other.test'))

    def test_transport_binds_envelope_and_limits_response(self):
        seen=[]
        class Opener:
            def open(self,request,timeout):
                seen.append(request)
                return Response(json.dumps({'after_cursor':0,'next_cursor':0,'events':[]}).encode())
        client=CampusClient('https://portal.test',lambda:{'device_token':'d'*40,'actor_session':'a'*40},opener=Opener())
        self.assertEqual(client.changes(0)['events'],[])
        self.assertEqual(seen[0].get_header('Authorization'),'Bearer '+'d'*40)
        self.assertEqual(seen[0].get_header('X-cbvms-actor-session'),'a'*40)
        class Large:
            def open(self,*args,**kwargs):return Response(b'x'*65537)
        client.opener=Large()
        with self.assertRaises(ValueError):client.changes(0)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'campus.db'
        self.db=CBVMSDatabase(self.path);self.db.initialize()
        self.db.insert_student('S1','Original','BSIT','3A',b'',b'')
        self.token=self.db.create_staff_session('admin','admin123')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE'); migrate(c)
            c.execute("INSERT INTO incremental_device(singleton,device_id) VALUES(1,'A')")
            c.execute("INSERT INTO incremental_entity_versions VALUES('student','S1',0)")

    def tearDown(self):self.tmp.cleanup()

    def save(self,**kwargs):
        return save_profile(self.db,'S1',{'name':'Provisional'},actor_token=self.token,device_id='A',expected_version=0,**kwargs)

    def test_profile_and_outbox_commit_and_rollback(self):
        with patch('database.shared_profiles.enqueue',side_effect=ValueError('queue failed')):
            with self.assertRaises(ValueError):self.save()
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT name FROM students WHERE student_id='S1'").fetchone()[0],'Original')
        oid=self.save()
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT operation_id FROM incremental_outbox').fetchone()[0],oid)
            self.assertEqual(c.execute("SELECT name FROM students WHERE student_id='S1'").fetchone()[0],'Provisional')
        with self.assertRaises(ValueError):self.save()

    def test_local_auth_and_recycled_denied(self):
        self.db.revoke_staff_session(self.token)
        with self.assertRaises(PermissionError):self.save()
        self.token=self.db.create_staff_session('admin','admin123')
        with self.db.connect() as c:c.execute("UPDATE students SET is_archived=1 WHERE student_id='S1'")
        with self.assertRaises(ValueError):self.save()

    def test_pending_values_preserved_and_missing_gallery_not_created(self):
        self.save()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            events=[{'cursor':1,'operation_id':'remote-1','payload':{'entity_type':'student','entity_id':'S1','version':1,'name':'Remote','email':'remote@test.invalid'}},
                    {'cursor':3,'operation_id':'remote-2','payload':{'entity_type':'student','entity_id':'cloud-only','version':1,'name':'Cloud','email':'cloud@test.invalid'}}]
            apply_page(c,after_cursor=0,next_cursor=3,events=events,apply_callback=apply_profile)
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT name FROM students WHERE student_id='S1'").fetchone()[0],'Provisional')
            self.assertEqual(c.execute('SELECT count(*) FROM incremental_profile_cache').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT count(*) FROM students').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM incremental_outbox').fetchone()[0],1)

    def test_cycle_upload_receipt_then_inbound_and_later_idle_cycle(self):
        oid=self.save();calls=[]
        class Client:
            def __call__(self,batch):
                calls.append(batch)
                return [{**{k:r[k] for k in ('operation_id','device_id','entity_type','entity_id','operation_type','expected_version','payload_digest')},'status':'accepted','version':1} for r in batch]
            def changes(self,cursor):
                return {'after_cursor':cursor,'next_cursor':1,'events':[] if cursor else [{'cursor':1,'operation_id':oid,'payload':{'entity_type':'student','entity_id':'S1','version':1,'name':'Provisional','email':''}}]}
        cycle=CampusCycle(self.path,Client(),apply_profile)
        self.assertTrue(cycle.run_once());self.assertTrue(cycle.run_once());self.assertEqual(len(calls),1)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM incremental_outbox').fetchone()[0],'accepted')
            self.assertEqual(c.execute('SELECT cursor FROM incremental_cursor').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM incremental_inbox').fetchone()[0],1)
