"""Automatic post-registration reports, with disposable student data only."""
import gc
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from database.db_manager import CBVMSDatabase
from core import registration_sync, registration_records

class RecordSyncTests(unittest.TestCase):
    def test_new_reports_follow_registration_without_other_students_or_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            db=CBVMSDatabase(Path(folder)/'test.db')
            db.initialize(process_deadlines=False)
            db.insert_student('S1','Synthetic','BSIT','3A',b'face',b'photo',account_username='one',account_password='synthetic-password')
            db.insert_student('S2','Unowned','BSIT','3A',b'',b'',account_username='two',account_password='synthetic-password')
            config={'source_id':'00000000-0000-4000-8000-000000000001'}
            with patch.object(registration_sync,'load_config',return_value=config),patch.object(registration_sync,'send',side_effect=['registered','existing_account']):
                registration_sync.run_once(db)
            sent=[]
            def post(config,payload):
                if payload['kind']=='appeal_actions':
                    return {'status':'actions','actions':[]}
                sent.append(payload)
                return {'status':'synced'}
            with patch.object(registration_records,'post',side_effect=post):
                self.assertEqual(registration_records.run_once(db,config),{'S1':'synced'})
                self.assertEqual(sent[-1]['snapshot']['violations'],[])
                vid=db.log_violation('S1','Synthetic','Wrong uniform',violation_code='wrong_uniform')
                registration_records.run_once(db,config)
                self.assertEqual(sent[-1]['snapshot']['violations'][0]['id'],vid)
                self.assertGreater(sent[-1]['sequence'],sent[0]['sequence'])
                count=len(sent)
                self.assertEqual(registration_records.run_once(db,config),{'S1':'unchanged'})
                self.assertEqual(len(sent),count)
                self.assertNotIn('password_hash',json.dumps(sent))
                self.assertNotIn('encoding',json.dumps(sent))
                self.assertNotIn('synthetic-password',json.dumps(sent))
                db.student_session_ref={'token':'synthetic'}
                self.assertEqual(registration_records.run_once(db,config),{})
            gc.collect()

    def test_appeal_is_imported_once_even_when_acknowledgement_is_lost(self):
        import base64
        import hashlib
        from core.cloud_sync import violation_identity, apply_action
        from core.discipline import utc_now
        from tests.evidence_fixture import picture_evidence
        with tempfile.TemporaryDirectory() as folder:
            db=CBVMSDatabase(Path(folder)/'test.db')
            db.initialize(process_deadlines=False)
            pk=db.insert_student('S1','Synthetic','BSIT','3A',b'',b'',account_username='one',account_password='synthetic-password')
            config={'source_id':'00000000-0000-4000-8000-000000000001'}
            with patch.object(registration_sync,'load_config',return_value=config),patch.object(registration_sync,'send',return_value='registered'):
                registration_sync.run_once(db)
            vid=db.log_violation('S1','Synthetic','Wrong uniform',violation_code='wrong_uniform')
            with db.connect() as conn:
                violation=dict(conn.execute('SELECT * FROM violations WHERE id=?',(vid,)).fetchone())
                owner=dict(conn.execute('SELECT s.id,s.archive_version,a.id AS account_id,a.username FROM students s JOIN student_accounts a ON a.student_id=s.student_id').fetchone())
            name,_,blob=picture_evidence()
            action={'id':'synthetic-appeal','student_id':'S1','kind':'appeals','created_at':utc_now().isoformat(),
                'payload':{'violation_id':vid,'violation_identity':violation_identity(violation),'reason':'Please review the original uniform detection picture.',
                    'filename':name,'file':base64.b64encode(blob).decode(),'evidence_sha256':hashlib.sha256(blob).hexdigest()}}
            acknowledged=[]
            def lost_ack(config,payload):
                if payload['kind']=='appeal_actions':return {'status':'actions','actions':[action]}
                raise TimeoutError('Lost acknowledgement')
            with patch.object(registration_records,'post',side_effect=lost_ack):
                registration_records.import_appeals(db,config,1)
            def recovered(config,payload):
                if payload['kind']=='appeal_actions':return {'status':'actions','actions':[] if acknowledged else [action]}
                acknowledged.append(payload)
                return {'status':'acknowledged'}
            with patch.object(registration_records,'post',side_effect=recovered):
                registration_records.import_appeals(db,config,2)
            with db.connect() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM appeals').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT file_data FROM evidence_files').fetchone()[0],blob)
                conn.execute('UPDATE students SET is_archived=1,archive_version=archive_version+1 WHERE id=?',(pk,))
            self.assertEqual(acknowledged[0]['status'],'applied')
            with self.assertRaises(ValueError):apply_action(db,action,expected_owner=owner)
            gc.collect()
