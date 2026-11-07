"""Scheduling contracts on synthetic pixels; no trained logo backend or timings."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np

from core.uniform_logo import LogoVerificationConfig, LogoRequestGate, original_logo_region
from core.uniform_observation import ObservationUniformSession
from core.uniform_model_registry import CLASSES, policy_rule
from tests import test_uniform_observation as fixtures


class LogoGateTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.arange(120*160*3, dtype=np.uint8).reshape(120,160,3)
        self.ticket = SimpleNamespace(usable=True, torso_box=(10,20,140,110), captured_at=10.,
                                     key=('camera','session','generation','track'), revision=1)
        self.options = dict(frame=self.frame, ticket=self.ticket, logo_box=[20,30,60,70],
            visibility='visible', torso_label=None, now=10.05, frame_ttl=1., current=lambda t,n:True)
        self.gate = LogoRequestGate(LogoVerificationConfig(True,50.), backend_available=True)

    def test_default_disabled_and_no_backend_do_not_schedule(self):
        self.assertIsNone(LogoRequestGate(LogoVerificationConfig(), backend_available=True).reserve(**self.options))
        self.assertIsNone(LogoRequestGate(LogoVerificationConfig(True)).reserve(**self.options))
        with self.assertRaisesRegex(ValueError,'Not trained'):
            LogoVerificationConfig(True).require_available()

    def test_only_uncertain_torso_and_visible_logo_are_considered(self):
        for visibility in ('obscured','too_small','back_view','uncertain','not_visible'):
            self.assertIsNone(self.gate.reserve(**{**self.options,'visibility':visibility}))
        for label in CLASSES:
            self.assertIsNone(self.gate.reserve(**{**self.options,'torso_label':label}))
        self.assertIsNotNone(self.gate.reserve(**self.options))

    def test_original_pixels_no_resize_flip_or_other_torso(self):
        crop=original_logo_region(self.frame,self.ticket,[20,30,60,70])
        np.testing.assert_array_equal(crop,self.frame[30:70,20:60])
        self.assertEqual(crop.shape,(40,40,3))
        for box in ([0,0,60,70],[20,30,141,70],[20,30,20,70],[20.,30,60,70]):
            with self.assertRaises(ValueError):original_logo_region(self.frame,self.ticket,box)
        self.ticket.usable=False
        with self.assertRaises(ValueError):self.gate.reserve(**self.options)

    def test_one_slot_and_no_overlap_after_invalidation(self):
        first=self.gate.reserve(**self.options)
        self.assertIsNone(self.gate.reserve(**self.options))
        self.gate.invalidate()
        self.assertIsNone(self.gate.reserve(**self.options))
        self.assertFalse(self.gate.finish(first,now=10.06,current=lambda t,n:True))
        second=self.gate.reserve(**self.options)
        self.assertIsNotNone(second)
        self.assertFalse(self.gate.finish(first,now=10.06,current=lambda t,n:True))
        self.assertTrue(self.gate.finish(second,now=10.07,current=lambda t,n:True))

    def test_deadline_and_scope_reject_late_or_reassigned_results(self):
        first=self.gate.reserve(**self.options)
        self.assertAlmostEqual(first.deadline,10.1)
        self.assertFalse(self.gate.finish(first,now=10.101,current=lambda t,n:True))
        for scope in ('camera','session','generation','track','model','policy'):
            with self.subTest(scope=scope):
                request=self.gate.reserve(**self.options)
                self.assertFalse(self.gate.finish(request,now=10.06,current=lambda t,n:False))
        self.assertIsNone(self.gate.reserve(**{**self.options,'now':11.1}))
        self.assertIsNone(self.gate.reserve(**{**self.options,'current':lambda t,n:False}))
        backwards=self.gate.reserve(**self.options)
        self.assertFalse(self.gate.finish(backwards,now=10.04,current=lambda t,n:True))

    def test_bad_config_rejected_without_backend_or_download(self):
        for value in ({'enabled':1},{'max_added_latency_ms':0},{'max_added_latency_ms':float('nan')},
                      {'max_added_latency_ms':1001},{'ocr':True},{'weights':'download'}):
            with self.assertRaises(ValueError):LogoVerificationConfig.parse(value)


class LogoObservationTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.ObservationTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def test_not_trained_cannot_be_enabled_and_changes_are_versioned(self):
        config=self.fx.config
        changed=replace(config,logo_verification={'enabled':False,'max_added_latency_ms':70})
        self.assertNotEqual(config.version,changed.version)
        session=ObservationUniformSession(changed)
        self.assertFalse(session.snapshot()['logo_verification']['executed'])
        self.assertEqual(session.snapshot()['logo_verification']['max_added_latency_ms'],70)
        fx=self.fx;fx.session=ObservationUniformSession(changed,clock=lambda:fx.now)
        fx.session.predictor=fx.predictor
        self.assertEqual(fx.assess(fx.task()).logo_verification['max_added_latency_ms'],70)
        with self.assertRaisesRegex(ValueError,'Not trained'):
            ObservationUniformSession(replace(config,logo_verification={'enabled':True}))

    def test_missing_logo_does_not_change_uncertainty_policy_or_cache(self):
        fx=self.fx
        fx.session=ObservationUniformSession(replace(fx.config,policy=policy_rule(.8)),clock=lambda:fx.now)
        fx.session.predictor=fx.predictor
        fx.predictor.probs=dict(zip(CLASSES,(.34,.33,.33)))
        unknown=fx.assess(fx.task())
        self.assertIsNone(unknown.label);self.assertIsNone(unknown.policy_outcome)
        self.assertEqual(unknown.logo_verification['status'],'Not trained')
        self.assertFalse(unknown.logo_verification['executed'])
        fx.predictor.probs=dict(zip(CLASSES,(.02,.95,.03)))
        reliable=fx.assess(fx.task());cached=fx.assess(fx.task())
        self.assertEqual(reliable.policy_outcome,'compliant')
        self.assertEqual(reliable.passage_id,unknown.passage_id)
        self.assertTrue(cached.cached);self.assertEqual(fx.predictor.calls,2)
        fx.session.invalidate()
        self.assertFalse(fx.session.result_valid(cached,fx.task().uniform_tickets[0]))


if __name__=='__main__':unittest.main()
