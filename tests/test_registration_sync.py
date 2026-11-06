import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import registration_sync as sync
from database.db_manager import CBVMSDatabase


class RegistrationSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CBVMSDatabase(Path(self.temp.name) / 'test.db')
        self.db.initialize(process_deadlines=False)
        self.pk = self.db.insert_student('S1','Synthetic','BSIT','3A',b'private-face',b'private-photo',
            account_username='synthetic',account_password='synthetic-password')
        self.config = {'enabled':True, 'origin':'https://portal.example', 'key':'x'*64,
                       'source_id':'00000000-0000-4000-8000-000000000001'}

    def test_registered_account_sent_once_without_biometrics_or_plaintext(self):
        with patch.object(sync, 'load_config', return_value=self.config), patch.object(sync, 'send', return_value='registered') as send:
            self.assertEqual(sync.run_once(self.db), {self.pk:'registered'})
            sync.run_once(self.db)
            send.assert_called_once()
            payload = send.call_args.args[1]
            self.assertEqual(payload['username'],'synthetic')
            self.assertNotIn('encoding', json.dumps(payload))
            self.assertNotIn('photo', json.dumps(payload))
            self.assertNotIn('synthetic-password', json.dumps(payload))
            self.assertTrue(payload['must_change_password'])

    def test_existing_website_password_is_never_overwritten(self):
        with patch.object(sync, 'load_config', return_value=self.config), patch.object(sync, 'send', return_value='existing_account') as send:
            sync.run_once(self.db)
            self.assertIn('existing website password', sync.email_note(self.db, 'S1'))
            send.assert_called_once()

    def test_network_failure_retries_and_recycle_state_is_forwarded(self):
        with patch.object(sync, 'load_config', return_value=self.config), patch.object(sync, 'send', side_effect=TimeoutError):
            self.assertEqual(sync.run_once(self.db), {self.pk:'pending'})
        with patch.object(sync, 'load_config', return_value=self.config), patch.object(sync, 'send', return_value='registered') as send:
            sync.run_once(self.db)
            with self.db.connect() as conn:
                conn.execute('UPDATE students SET is_archived=1,archive_version=1 WHERE id=?',(self.pk,))
            sync.run_once(self.db)
            self.assertFalse(send.call_args.args[1]['active'])
            self.assertEqual(send.call_args.args[1]['lifecycle_version'], 1)

    def test_disabled_and_student_session_do_not_send(self):
        with patch.object(sync, 'load_config', return_value=None), patch.object(sync, 'send') as send:
            sync.run_once(self.db)
            send.assert_not_called()

    def test_recycle_during_first_network_request_is_not_lost(self):
        def publish_then_recycle(config, payload):
            with self.db.connect() as conn:
                conn.execute('UPDATE students SET is_archived=1,archive_version=1 WHERE id=?',(self.pk,))
            return 'registered'
        with patch.object(sync, 'load_config', return_value=self.config), patch.object(sync, 'send', side_effect=publish_then_recycle):
            sync.run_once(self.db)
        with patch.object(sync, 'load_config', return_value=self.config), patch.object(sync, 'send', return_value='registered') as send:
            sync.run_once(self.db)
            self.assertFalse(send.call_args.args[1]['active'])
        self.db.student_session_ref = {'token':'synthetic'}
        with patch.object(sync, 'load_config', return_value=self.config), patch.object(sync, 'send') as send:
            sync.run_once(self.db)
            send.assert_not_called()
