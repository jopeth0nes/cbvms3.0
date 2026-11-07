"""Isolation tests use fake camera definitions and disposable application files."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.launch_uniform_observation import prepare_workspace, child_environment


class ObservationLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.source = base/'source'; self.source.mkdir()
        self.private = base/'private'; self.private.mkdir()
        for name in ('core','models','data'):
            (self.source/name).mkdir()
        for name in ('main.py','web_portal.py'):
            (self.source/name).write_text('# disposable fixture\n')
        (self.source/'models/model.pt').write_bytes(b'fixture model')
        (self.source/'data/cbvms.db').write_bytes(b'private roster sentinel')
        (self.source/'data/cloud-sync.json').write_text('{"enabled":true}')
        (self.source/'data/recovery-relay.json').write_text('{}')
        (self.source/'data/ip_cameras.json').write_text(json.dumps({'cameras':[
            {'id':'gate','label':'Front Gate','url':'rtsp://fixture.invalid/one'},
            {'id':'else','label':'Elsewhere','url':'rtsp://fixture.invalid/two'}]}))
        (self.source/'data/camera_preference.json').write_text('{"id":"usb_0"}')
        self.config = base/'config.json'
        self.config.write_text(json.dumps({'schema':1,'mode':'observation_only',
            'candidate_bundle':'candidate','candidate_sha256':'a'*64,
            'recognition_confidence':.8,'logo_verification':{'enabled':False}}))

    def prepare(self, primary='front gate'):
        return prepare_workspace(self.private,self.config,primary,source=self.source)

    def test_private_database_and_exact_camera_selection_without_source_mutation(self):
        before = {str(p):p.read_bytes() for p in (self.source/'data').iterdir()}
        config, primary = self.prepare()
        self.assertEqual(primary,{'id':'ip_gate','type':'rj45','label':'Front Gate'})
        self.assertFalse((self.private/'data/cbvms.db').exists())
        self.assertFalse((self.private/'data/cloud-sync.json').exists())
        self.assertFalse((self.private/'data/recovery-relay.json').exists())
        self.assertEqual(json.loads((self.private/'data/ip_cameras.json').read_text())['cameras'],
            [{'id':'gate','label':'Front Gate','url':'rtsp://fixture.invalid/one'}])
        self.assertEqual(json.loads((self.private/'data/camera_view_preference.json').read_text())['sources'],[])
        self.assertEqual(json.loads(config.read_text())['candidate_bundle'],str((self.config.parent/'candidate').resolve()))
        self.assertEqual(before,{str(p):p.read_bytes() for p in (self.source/'data').iterdir()})
        self.assertEqual(self.private.stat().st_mode & 0o777,0o700)
        self.assertEqual((self.private/'data/ip_cameras.json').stat().st_mode & 0o777,0o600)

    def test_models_are_independent_copies_and_camera_configs_not_shared(self):
        self.prepare('MacBook Camera')
        self.assertFalse((self.private/'data/ip_cameras.json').exists())
        copied=self.private/'models/model.pt'; copied.write_bytes(b'private mutation')
        self.assertEqual((self.source/'models/model.pt').read_bytes(),b'fixture model')
        self.assertFalse(copied.is_symlink())

    def test_reject_workspace_inside_source_or_nonempty_destination(self):
        with self.assertRaises(ValueError):
            prepare_workspace(self.source/'child',self.config,'front gate',source=self.source)
        (self.private/'sentinel').write_text('preserve')
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual((self.private/'sentinel').read_text(),'preserve')

    def test_reject_ambiguous_camera_and_symlinked_assets(self):
        with self.assertRaises(ValueError):self.prepare('missing camera')
        # Use a fresh destination after the failed preparation.
        self.private=self.private.parent/'other';self.private.mkdir()
        (self.source/'models/link').symlink_to(self.source/'data/cbvms.db')
        with self.assertRaisesRegex(ValueError,'Symlink'):self.prepare()

    def test_reject_enabled_untrained_verifier(self):
        config=json.loads(self.config.read_text());config['logo_verification']={'enabled':True}
        self.config.write_text(json.dumps(config))
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(list(self.private.iterdir()),[])

    def test_environment_cannot_reenable_production_runtime_or_import_path(self):
        with patch.dict(os.environ,{'CBVMS_ASSESSMENT_OBSERVATION_ONLY':'0',
                'CBVMS_UNIFORM_OBSERVATION_CONFIG':'/production/config',
                'CBVMS_OTHER_SECRET':'fixture','PYTHONPATH':'/production/import'},clear=True):
            env=child_environment(self.private,self.config,'cpu')
        self.assertEqual(env['CBVMS_ASSESSMENT_OBSERVATION_ONLY'],'1')
        self.assertEqual(env['CBVMS_UNIFORM_OBSERVATION_CONFIG'],str(self.config))
        self.assertNotIn('CBVMS_OTHER_SECRET',env)
        self.assertNotIn('PYTHONPATH',env)
        self.assertTrue(env['YOLO_CONFIG_DIR'].startswith(str(self.private)))


if __name__ == '__main__':unittest.main()
