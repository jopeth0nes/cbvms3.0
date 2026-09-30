"""Same-frame ownership handoff, anonymous torso display and clipped webcam faces."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

import numpy as np

from core.live_pipeline import FaceTrackingProcessor, MotionProjection, TrackedFace
from core.live_state import LiveState, FrameContext
from ui.dashboard import CBVMSDashboard
from ui.live_alerts import LiveAlertsModel
from tests.test_live_pipeline import PipelineFixture, detection
from tests.test_live_dashboard import dashboard, result_for, sample


class CoordinatedMonitorTests(unittest.TestCase):
    def tracker(self, fx):
        self.deliveries=[]
        recognizer=SimpleNamespace(detect_faces=lambda _: [dict(
            box=[80,20,130,90],score=.99,keypoints=((90,40),(115,40),(105,55),(93,72),(117,72)))])
        return FaceTrackingProcessor(recognizer,fx.detector,on_localized=self.deliveries.append)

    def localized(self, tracker, fx, seq):
        task=fx.task(seq)
        with patch('core.live_pipeline.time.monotonic',side_effect=lambda:fx.now):
            return tracker.analyze(task)

    def test_anonymous_torso_is_visible_before_recognition_and_never_writes(self):
        fx=PipelineFixture();tracker=self.tracker(fx)
        result=self.localized(tracker,fx,1)
        a=result.assessments[0]
        self.assertEqual(a.state,'Checking uniform')
        self.assertEqual(a.torso_box,(65,100,145,210))
        self.assertFalse(a.reliable_identity or a.accepted_categories or a.student_id)
        self.assertEqual(self.deliveries,[result])
        self.assertEqual(result.observations[0].box,a.face_box)
        fx.recognizer.recognize_faces.assert_not_called()
        fx.database.log_violation.assert_not_called()

    def test_same_capture_and_presence_survive_identification_and_evidence(self):
        fx=PipelineFixture();tracker=self.tracker(fx);pending=[];completed=[]
        for seq in range(1,5):
            local=self.localized(tracker,fx,seq);pending.append(local.assessments[0])
            task=replace(local.task,observations=local.observations)
            with patch('core.live_pipeline.time.monotonic',side_effect=lambda:fx.now), \
                    patch('core.live_pipeline.skin_fraction',return_value=0.):
                result=fx.processor.analyze(task)
            completed.append(result.assessments[0])
        self.assertEqual(len(set(a.presence_id for a in pending+completed)),1)
        self.assertEqual(completed[-1].accepted_categories,('wrong_uniform',))
        self.assertEqual(result.task.context.frame_id,local.task.context.frame_id)
        self.assertIn('detections',fx.recognizer.recognize_faces.call_args.kwargs)
        # Body inference happens once per tracking frame, not again during recognition.
        self.assertEqual(fx.detector.detect_persons.call_count,4)
        model=LiveAlertsModel()
        from dataclasses import asdict
        for a in pending+completed:model.update_assessments([asdict(a)])
        self.assertEqual(len(model.rows),1)

    def test_changed_presence_cannot_inherit_student_evidence_at_same_position(self):
        fx=PipelineFixture();tracker=self.tracker(fx)
        for seq in range(1,4):
            local=self.localized(tracker,fx,seq)
            task=replace(local.task,observations=local.observations)
            with patch('core.live_pipeline.time.monotonic',side_effect=lambda:fx.now), \
                    patch('core.live_pipeline.skin_fraction',return_value=0.):
                fx.processor.analyze(task)
        local=self.localized(tracker,fx,4)
        changed=replace(local.observations[0],track_id=2,presence_id='1:track:2')
        with patch('core.live_pipeline.time.monotonic',side_effect=lambda:fx.now), \
                patch('core.live_pipeline.skin_fraction',return_value=0.):
            result=fx.processor.analyze(replace(local.task,observations=(changed,)))
        self.assertFalse(result.assessments[0].reliable_identity)
        self.assertFalse(result.assessments[0].accepted_categories)

    def test_two_people_keep_their_own_clothing_when_detection_order_changes(self):
        fx=PipelineFixture()
        faces=[detection('S-1'),detection('S-2',360,embedding=1)]
        bodies=[[50,0,160,300],[330,0,450,300]]
        detector=SimpleNamespace(detect_faces=lambda _: [dict(r,score=.99,keypoints=()) for r in faces])
        tracker=FaceTrackingProcessor(detector,fx.detector)
        fx.trainer.predict_proba.side_effect=lambda _,c: {'correct_uniform':.9 if c[0,0,0]>100 else .1}
        ids={}
        for seq in range(1,6):
            faces.reverse();bodies.reverse()
            fx.recognizer.recognize_faces.return_value=faces
            fx.detector.detect_persons.return_value=bodies
            local=self.localized(tracker,fx,seq)
            with patch('core.live_pipeline.time.monotonic',side_effect=lambda:fx.now), \
                    patch('core.live_pipeline.skin_fraction',return_value=0.):
                result=fx.processor.analyze(replace(local.task,observations=local.observations))
            for a in result.assessments:
                if a.reliable_identity:
                    ids.setdefault(a.student_id,set()).add(a.presence_id)
        self.assertTrue(all(len(value)==1 for value in ids.values()),ids)
        by_id={a.student_id:a for a in result.assessments}
        self.assertEqual(by_id['S-1'].state,'Uniform compliant')
        self.assertEqual(by_id['S-2'].state,'Suspected uniform violation')
        fx.persist(result)
        self.assertEqual(fx.database.log_violation.call_args.kwargs['student_id'],'S-2')

    def test_body_failure_recovers_without_retry_button_and_keeps_face_track(self):
        fx=PipelineFixture();tracker=self.tracker(fx)
        fx.detector.detect_persons.side_effect=[[],[[50,0,160,300]]]
        fx.detector.last_error='Temporary inference failure'
        first=self.localized(tracker,fx,1)
        self.assertEqual(first.assessments[0].state,'Locating torso')
        self.assertIn('retrying',first.assessments[0].reason)
        fx.detector.last_error=None
        second=self.localized(tracker,fx,2)
        self.assertEqual(first.assessments[0].presence_id,second.assessments[0].presence_id)
        self.assertIsNotNone(second.assessments[0].torso_box)

    def test_missing_classifier_never_becomes_colour_only_assessment(self):
        fx=PipelineFixture();fx.matcher.is_loaded.return_value=True
        fx.matcher.is_uniform.return_value=(True,.99)
        fx.trainer.predict_proba.return_value=None
        for seq in range(1,6):result=fx.analyze(seq)
        self.assertEqual(result.assessments[0].state,'Uniform not assessed')
        self.assertFalse(result.assessments[0].accepted_categories)

    def test_clipped_forehead_retains_flow_and_face_visibility(self):
        frame=np.random.default_rng(42).integers(0,255,(240,320,3),np.uint8)
        p=MotionProjection(frame,(100,-15,220,130))
        shifted=np.roll(frame,1,axis=1)
        box=p.advance(shifted)
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box[1],0)
        self.assertAlmostEqual(box[0],101,delta=2)
        self.assertAlmostEqual(p.offset[1],0,delta=1)

    def test_stationary_jitter_and_brief_missed_detection_do_not_churn_ids(self):
        state=LiveState();ids=[]
        for seq,x in enumerate([80,83,79,82,None,81,84,80]):
            at=seq*.2+100
            rows=[] if x is None else [dict(box=[x,20,x+50,90])]
            result=state.update(FrameContext(1,('cam',seq),at),rows,now=at+.05)
            ids.extend(a.track_id for a in result)
        self.assertEqual(set(ids),{1})

    def test_late_result_for_other_token_never_attaches_to_current_torso(self):
        panel=dashboard();old=result_for();current=replace(old.assessments[0],
            track_id=2,presence_id='2:track:2',student_id='',name='Identifying',
            state='Checking uniform',reliable_identity=False,accepted_categories=())
        panel._monitor_result=old
        panel._tracking_result=replace(old,assessments=(current,),observations=())
        panel._monitor_projections[1]=SimpleNamespace(advance=lambda *a:(50,40,90,90))
        panel._tracking_projections[2]=SimpleNamespace(advance=lambda *a:(50,40,90,90))
        with patch('ui.dashboard.time.monotonic',return_value=10.2):
            rows=panel._monitor_rows(sample(2,10.1))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['presence_id'],'2:track:2')
        self.assertEqual(rows[0]['student_id'],'')
        self.assertIsNotNone(rows[0]['torso_box'])

    def test_on_localized_submits_original_frame_not_latest_preview(self):
        panel=dashboard();local=replace(result_for(),observations=())
        with patch('core.live_pipeline.time.monotonic',return_value=10.2):
            CBVMSDashboard._on_localized(panel,local)
        task=panel._live_worker.offer.call_args.args[0]
        self.assertEqual(task.context,local.task.context)
        self.assertEqual(task.observations,())
        np.testing.assert_array_equal(task.frame,local.task.frame)


if __name__=='__main__':unittest.main()
