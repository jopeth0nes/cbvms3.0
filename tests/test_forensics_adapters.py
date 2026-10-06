"""Offline policy tests; real C2PA fixture tests require the optional runtime."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

from core.forensics_c2pa import summarize_manifest, inspect_c2pa
from core.forensics_model import inspect_model, source_hash, SOURCE_SHA256
from core.forensics_classical import describe_pixels
from scripts.benchmark_evidence_forensics import metrics, make_fixtures

class AdapterPolicyTests(unittest.TestCase):
    def test_untrusted_invalid_and_ai_provenance_are_distinct(self):
        doc={'active_manifest':'m','manifests':{'m':{'assertions':[{'label':'c2pa.actions.v2','data':{'actions':[{'action':'c2pa.edited','digitalSourceType':'http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia','parameters':{'GPS':'private'}}]}}]}}}
        for state,status in [('Valid','untrusted'),('Trusted','valid'),('Invalid','invalid')]:
            r=summarize_manifest(doc,state)
            self.assertEqual(r['status'],status);self.assertTrue(r['ai_provenance_recorded'])
            self.assertEqual(r['assertions_verified'],state=='Trusted');self.assertNotIn('private',json.dumps(r))
        self.assertEqual(summarize_manifest({},None)['status'],'absent')
    def test_model_disabled_and_missing_configuration_do_not_load_ml(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(inspect_model(b'x','JPEG')['status'],'not_configured')
        with patch.dict(os.environ,{'CBVMS_FORENSICS_ENABLE_EXPERIMENTAL':'1','CBVMS_FORENSICS_LICENSE_ACCEPTED':'1'},clear=True):self.assertEqual(inspect_model(b'x','JPEG')['status'],'not_configured')
    def test_classical_measurements_have_no_threshold_and_do_not_modify_input(self):
        b=io.BytesIO();Image.new('RGB',(80,80),'gray').save(b,format='PNG');original=b.getvalue()
        d=describe_pixels(original)
        self.assertEqual(d['local_highpass_mad_4x4'],[[0.]*4]*4)
        self.assertNotIn('score',d);self.assertEqual(original,b.getvalue())
    def test_abstention_is_not_counted_as_true_negative(self):
        rows=[{'label':'benign','prediction':'abstain'},{'label':'manipulated','prediction':'abstain'}]
        r=metrics(rows);self.assertEqual(r['coverage'],0);self.assertIsNone(r['precision_decided'])
        self.assertIsNone(r['false_positive_rate_decided']);self.assertEqual(r['manipulated_without_alert_rate'],1)
    def test_synthetic_manifest_is_reproducible(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            left=json.loads(make_fixtures(a).read_text());right=json.loads(make_fixtures(b).read_text())
            self.assertEqual(left,right);self.assertEqual(len(left['items']),31)

@unittest.skipUnless(os.environ.get('CBVMS_C2PA_TEST_FIXTURES'),'requires official C2PA fixtures and optional runtime')
class RealC2PATests(unittest.TestCase):
    def setUp(self):
        import c2pa
        self.c2pa=c2pa;self.root=Path(os.environ['CBVMS_C2PA_TEST_FIXTURES'])
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.anchors=Path(self.tmp.name)/'test-only.pem'
        self.anchors.write_text(json.loads((self.root/'trust_config_test_settings.json').read_text())['trust']['trust_anchors'])
    def test_real_absent_untrusted_trusted_and_tampered(self):
        fixture=self.root/'fixtures';data=(fixture/'C.jpg').read_bytes()
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(inspect_c2pa((fixture/'A.jpg').read_bytes(),'JPEG')['status'],'absent')
            self.assertEqual(inspect_c2pa(data,'JPEG')['status'],'untrusted')
        with patch.dict(os.environ,{'CBVMS_FORENSICS_C2PA_TRUST_ANCHORS':str(self.anchors)}):
            self.assertEqual(inspect_c2pa(data,'JPEG')['status'],'valid')
            changed=bytearray(data);changed[-100]^=1
            result=inspect_c2pa(bytes(changed),'JPEG')
            self.assertEqual(result['status'],'invalid');self.assertIn('assertion.dataHash.mismatch',result['validation_codes'])
    def test_real_valid_edited_ai_assertions_remain_separate(self):
        c2pa=self.c2pa;f=self.root/'fixtures'
        info=c2pa.C2paSignerInfo(alg=b'es256',sign_cert=(f/'es256_certs.pem').read_bytes(),private_key=(f/'es256_private.key').read_bytes(),ta_url=None)
        manifest={'claim_generator_info':[{'name':'CBVMS synthetic test'}],'format':'image/jpeg','assertions':[{'label':'c2pa.actions','data':{'actions':[{'action':'c2pa.created','digitalSourceType':'http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia'},{'action':'c2pa.edited'}]}}]}
        src=io.BytesIO();Image.new('RGB',(64,64),'green').save(src,format='JPEG');src.seek(0);out=io.BytesIO()
        with c2pa.Context.from_dict({'verify':{'remote_manifest_fetch':False,'ocsp_fetch':False},'core':{'allowed_network_hosts':[]}}) as ctx:
            with c2pa.Signer.from_info(info) as signer:
                with c2pa.Builder(manifest,ctx) as builder:builder.sign(signer,'image/jpeg',src,out)
        with patch.dict(os.environ,{'CBVMS_FORENSICS_C2PA_TRUST_ANCHORS':str(self.anchors)}):result=inspect_c2pa(out.getvalue(),'JPEG')
        self.assertEqual(result['status'],'valid');self.assertTrue(result['ai_provenance_recorded']);self.assertIn('c2pa.edited',result['recorded_actions'])
