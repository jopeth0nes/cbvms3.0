"""Observation safety on disposable synthetic pixels; no accuracy claims."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np

from core.live_pipeline import MonitorTask,TrackedFace,LiveProcessor,LiveWorker
from core.live_state import FrameContext
from core.uniform_observation import ObservationConfig,ObservationUniformSession,CONFIG_ENV
from core.uniform_model_registry import CLASSES,policy_rule


class FakePredictor:
    def __init__(self):
        self.calls=0; self.probs=dict(zip(CLASSES,(.05,.9,.05))); self.hook=None
    def timed_probabilities(self,bgr):
        self.calls+=1
        if self.hook: self.hook()
        return {'probabilities':self.probs,'timings':{'preprocessing_ms':1.,'inference_ms':2.,'total_ms':3.}}
    def backend_status(self): return {'active':'cpu'}


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.now=100.; self.cancelled=threading.Event(); self.seq=0
        self.config=ObservationConfig('/unused','a'*64,.8,assessment_zone=(0.,0.,1.,1.))
        self.session=ObservationUniformSession(self.config,clock=lambda:self.now)
        self.predictor=FakePredictor();self.session.predictor=self.predictor
        self.session.descriptor={'synthetic':True}
        self.obs=TrackedFace((220,20,280,100),.99,(),1,'person:1:1',0,(150,0,370,450),(170,120,350,320))
        gray=np.random.default_rng(5).integers(20,240,(480,640,1),dtype=np.uint8)
        self.frame=np.repeat(gray,3,axis=2)
        self.time_patch=patch('core.live_pipeline.time.monotonic',side_effect=lambda:self.now)
        self.time_patch.start();self.addCleanup(self.time_patch.stop)

    def task(self, *, observation=None,frame=None,dt=.1,**kw):
        self.now+=dt;self.seq+=1
        observations=(observation or self.obs,)
        task=MonitorTask(FrameContext(1,('test',self.seq),self.now-.02),
            self.frame if frame is None else frame,self.now,self.cancelled,
            observations=observations,source_id='camera-1',camera_generation=3,observation_only=True,**kw)
        return self.session.observe(task,observations)

    def assess(self,task):return self.session.assess(task,task.uniform_tickets[0])

    def test_recognition_policy_separation_and_explicit_existing_policy(self):
        result=self.assess(self.task())
        self.assertEqual(result.label,'organizational_shirt')
        self.assertIsNone(result.policy_outcome)
        self.assertIn('Policy not configured',result.reason)
        self.assertEqual(result.state,'Organizational Shirt recognized')
        self.session=ObservationUniformSession(replace(self.config,policy=policy_rule(.8)),clock=lambda:self.now)
        self.session.predictor=self.predictor
        result=self.assess(self.task())
        self.assertEqual(result.policy_outcome,'compliant')
        self.predictor.probs=dict(zip(CLASSES,(.02,.03,.95)))
        result=self.assess(self.task(dt=.6))
        self.assertEqual((result.label,result.policy_outcome),('other_clothing','violation'))
        self.assertIn('permissions unassessed',result.reason)

    def test_cached_result_one_logical_passage_and_refresh(self):
        first=self.assess(self.task());cached=self.assess(self.task())
        self.assertEqual(first.passage_id,cached.passage_id)
        self.assertTrue(cached.cached);self.assertEqual(self.predictor.calls,1)
        self.assess(self.task(dt=.6));self.assertEqual(self.predictor.calls,2)
        self.assertEqual(self.session.snapshot()['passages'],1)

    def test_uncertainty_never_cached_and_new_frame_resolves(self):
        self.predictor.probs=dict(zip(CLASSES,(.34,.33,.33)))
        unknown=self.assess(self.task());self.assertIsNone(unknown.label)
        self.assertIsNone(unknown.policy_outcome)
        self.predictor.probs=dict(zip(CLASSES,(.9,.05,.05)))
        resolved=self.assess(self.task());self.assertEqual(resolved.label,'campus_uniform')
        self.assertEqual(unknown.passage_id,resolved.passage_id);self.assertEqual(self.predictor.calls,2)

    def test_blur_resolution_obstruction_and_invalid_association_abstain(self):
        cases=[(self.obs,np.full_like(self.frame,100),'blur'),
               (replace(self.obs,torso_box=(200,120,300,200)),self.frame,'resolution'),
               (replace(self.obs,torso_box=(170,40,350,200)),self.frame,'Torso'),
               (replace(self.obs,body_box=None),self.frame,'association')]
        for observation,frame,reason in cases:
            with self.subTest(reason=reason):
                result=self.assess(self.task(observation=observation,frame=frame))
                self.assertIsNone(result.label);self.assertIsNone(result.policy_outcome)
                self.assertIn(reason.lower(),result.reason.lower())
        with patch('core.uniform_observation.skin_fraction',return_value=1.):
            self.assertIn('obscured',self.assess(self.task()).reason)
        self.assertEqual(self.predictor.calls,0)

    def test_new_bad_association_revokes_cached_and_inflight_result(self):
        old=self.task();result=self.assess(old)
        bad=self.task(observation=replace(self.obs,torso_box=None))
        self.assertFalse(self.session.result_valid(result,old.uniform_tickets[0]))
        self.assertIsNone(self.assess(bad).label)
        fresh=self.task()
        self.predictor.hook=lambda:self.task(observation=replace(self.obs,body_box=None))
        self.assertIsNone(self.assess(fresh))

    def test_identity_reassignment_invalidates_without_new_passage_card(self):
        first=self.task();ticket=first.uniform_tickets[0]
        ticket=self.session.bind_identity(ticket,'student-A')
        old=self.session.assess(first,ticket)
        second=self.task();other=self.session.bind_identity(second.uniform_tickets[0],'student-B')
        self.assertFalse(self.session.result_valid(old,ticket))
        new=self.session.assess(second,other)
        self.assertEqual(old.passage_id,new.passage_id);self.assertEqual(self.predictor.calls,2)

    def test_all_scopes_expiry_and_disappearance_invalidate(self):
        for field in ('source','session','generation','camera_generation','track','presence','model_policy'):
            with self.subTest(field=field):
                old=self.task();result=self.assess(old)
                if field=='model_policy': self.session.invalidate()
                else:
                    next_task=self.task();o=self.obs
                    if field=='source':next_task=replace(next_task,source_id='camera-2')
                    if field=='session':next_task=replace(next_task,cancelled=threading.Event())
                    if field=='generation':next_task=replace(next_task,context=replace(next_task.context,generation=2))
                    if field=='camera_generation':next_task=replace(next_task,camera_generation=4)
                    if field=='track':o=replace(o,track_id=2)
                    if field=='presence':o=replace(o,presence_id='replacement')
                    # Advance timestamp because duplicate observations are intentionally rejected.
                    self.now+=.01
                    next_task=replace(next_task,context=replace(next_task.context,captured_at=self.now))
                    self.session.observe(next_task,(o,))
                self.assertFalse(self.session.result_valid(result,old.uniform_tickets[0]))
        old=self.task();result=self.assess(old);self.now+=1.1
        self.assertFalse(self.session.result_valid(result,old.uniform_tickets[0]))
        current=self.task();self.now+=.01
        gone=replace(current,context=replace(current.context,captured_at=self.now))
        self.session.observe(gone,());self.assertEqual(self.session.snapshot()['passages'],0)

    def test_duplicate_out_of_order_and_stale_frames_do_not_supply_evidence(self):
        task=self.task();self.assess(task)
        duplicate=self.session.observe(task,task.observations)
        self.assertEqual(duplicate.uniform_tickets,())
        self.assess(task);self.assertEqual(self.predictor.calls,1)
        self.now+=2.;self.assertIsNone(self.assess(task))
        self.assertEqual(self.session.observe(task,task.observations).uniform_tickets,())

    def test_old_result_cannot_cross_passages_or_replace_newer_evidence(self):
        first=self.task();old=self.assess(first)
        second=self.task(dt=.6);new=self.assess(second)
        self.assertIsNone(self.assess(first))
        forged=replace(new,passage_id='different-passage')
        self.assertFalse(self.session.result_valid(forged,second.uniform_tickets[0]))
        self.cancelled.set()
        self.assertFalse(self.session.result_valid(new,second.uniform_tickets[0]))

    def test_zone_and_first_usable_timing_exclude_bad_frame_wait(self):
        self.assess(self.task(frame=np.zeros_like(self.frame)))
        result=self.assess(self.task(dt=.4))
        t=result.timings
        self.assertGreater(t['zone_entry_to_decision_ms'],t['first_usable_to_decision_ms']+300)
        self.assertIsNone(t['queue_delay_ms']) # direct invocation has no worker queue measurement
        self.session=ObservationUniformSession(replace(self.config,assessment_zone=None),clock=lambda:self.now)
        self.session.predictor=self.predictor
        self.assertIsNone(self.assess(self.task()).timings['zone_entry_to_decision_ms'])

    def test_capacity_bounds(self):
        task=self.task()
        self.now+=.01
        task=replace(task,context=replace(task.context,captured_at=self.now))
        observations=tuple(replace(self.obs,track_id=i,presence_id=str(i)) for i in range(100))
        self.session.observe(task,observations)
        self.assertLessEqual(self.session.snapshot()['passages'],48)

    def test_toggle_off_and_failed_identity_analysis_revoke_evidence(self):
        old=self.task();result=self.assess(old)
        self.task(uniform_enabled=False)
        self.assertFalse(self.session.result_valid(result,old.uniform_tickets[0]))
        old=self.task();result=self.assess(old)
        from tests.test_live_pipeline import PipelineFixture
        fx=PipelineFixture();fx.processor.observation_only=True;fx.processor.uniform_observation=self.session
        fx.processor.assessment_failed(old)
        self.assertFalse(self.session.result_valid(result,old.uniform_tickets[0]))

    def test_load_validation_no_fallback_and_environment_requires_observation(self):
        for config in (replace(self.config,backend='cuda'),replace(self.config,recognition_confidence=float('nan')),
                       replace(self.config,policy={'kind':'invented'}),replace(self.config,assessment_zone=(0,0,2,1))):
            with self.assertRaises((ValueError,TypeError)):config.validate()
        with patch.dict('os.environ',{CONFIG_ENV:'/fixture'},clear=True):
            with self.assertRaisesRegex(ValueError,'OBSERVATION_ONLY'):ObservationUniformSession.from_environment(observation_only=False)
        session=ObservationUniformSession(self.config,predictor_factory=Mock())
        with patch('core.uniform_observation.candidate_descriptor',return_value={'id':'changed'}):
            with self.assertRaisesRegex(ValueError,'hash changed'):session.prepare()
        session._predictor_factory.assert_not_called()

    def test_config_relative_bundle_and_invalid_unknown_options(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'config.json'
            value={'schema':1,'mode':'observation_only','candidate_bundle':'bundle','candidate_sha256':'a'*64,'recognition_confidence':.8}
            p.write_text(json.dumps(value));self.assertEqual(ObservationConfig.load(p).candidate_bundle,str(Path(temp).resolve()/'bundle'))
            value['activate']=True;p.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'Unknown'):ObservationConfig.load(p)

    def test_pipeline_unknown_identity_recognizes_without_side_effects_or_legacy_classifier(self):
        from tests.test_live_pipeline import PipelineFixture
        fx=PipelineFixture();fx.processor.observation_only=True;fx.processor.uniform_observation=self.session
        task=self.task();fx.recognizer.recognize_faces.return_value=[{'box':self.obs.box,'matched':False,'identity_uncertain':True}]
        result=fx.processor.analyze(task)
        self.assertEqual(result.assessments[0].garment.label,'organizational_shirt')
        self.assertEqual(result.assessments[0].accepted_categories,())
        self.assertIsNone(result.assessments[0].uniform_label)
        fx.processor.persist(result);fx.processor.submit_attendance(result);fx.processor.submit_unknowns(result)
        fx.processor.submit_detection_audio(result)
        for name in ('record_attendance','record_premises_entry','log_violation','log_security_event'):
            getattr(fx.database,name).assert_not_called()
        fx.notifier.notify.assert_not_called();fx.trainer.predict_proba.assert_not_called()
        worker=LiveWorker(fx.processor,persistence_worker=True)
        self.assertFalse(worker.persistence_worker);self.assertIsNone(worker.attendance_writer)
        with self.assertRaisesRegex(ValueError,'observation-only'):
            LiveProcessor(None,None,None,None,None,None,uniform_observation=self.session)

    def test_two_people_and_reordered_recognition_keep_their_own_torso_pixels(self):
        from tests.test_live_pipeline import PipelineFixture
        fx=PipelineFixture();fx.processor.observation_only=True;fx.processor.uniform_observation=self.session
        fx.recognizer.recognize_faces.side_effect=lambda frame,detections:[dict(d,matched=False,identity_uncertain=True) for d in detections]
        left=replace(self.obs,box=(140,20,200,100),body_box=(40,0,270,450),torso_box=(60,120,240,320))
        right=replace(self.obs,box=(460,20,520,100),body_box=(360,0,590,450),torso_box=(380,120,560,320),
                      body_index=1,track_id=2,presence_id='person:1:2')
        pixels=self.frame.copy();pixels[120,60]=10;pixels[120,380]=200
        def predict(crop):
            label='campus_uniform' if crop[0,0,0]==10 else 'organizational_shirt'
            return {'probabilities':{c:.9 if c==label else .05 for c in CLASSES},'timings':{}}
        self.predictor.timed_probabilities=predict
        for observations in ((left,right),(right,left)):
            task=self.task(dt=.6);self.now+=.01
            task=replace(task,frame=pixels,context=replace(task.context,captured_at=self.now),
                         observations=observations,assessment_order_ids=(right.presence_id,left.presence_id))
            task=self.session.observe(task,observations)
            result=fx.processor.analyze(task)
            self.assertEqual({a.presence_id:a.garment.label for a in result.assessments},
                {left.presence_id:'campus_uniform',right.presence_id:'organizational_shirt'})


if __name__=='__main__':unittest.main()
