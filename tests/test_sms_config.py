import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core import sms_config


class SmsConfigTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_encrypted_token_round_trip_and_environment_override(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(sms_config,'TOKEN_PATH',Path(temporary)/'test.dpapi'), patch.dict(os.environ,{},clear=True):
            token = 'synthetic-token-for-disposable-test-only'
            sms_config.save_token(token)
            self.assertNotIn(token.encode(),sms_config.TOKEN_PATH.read_bytes())
            self.assertEqual(sms_config.get_token(),token)
            with patch.dict(os.environ,IPROG_API_TOKEN='environment-test-token'):
                self.assertEqual(sms_config.get_token(),'environment-test-token')
            sms_config.TOKEN_PATH.write_bytes(b'invalid-encrypted-file')
            self.assertEqual(sms_config.get_token(),'')
