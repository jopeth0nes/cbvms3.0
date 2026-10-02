"""Recovery and persistence proofs using synthetic exposures, never live student data."""
import pickle
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import cv2
import numpy as np
from core.camera import CameraSample
from core.face_capture import CaptureSession, CaptureValidation, FacePreviewTracker, CAPTURE_TIMEOUT, capture_payload, FaceCapture
from core.recognizer import FaceRecognizer
from database.db_manager import CBVMSDatabase
from tests.test_enrollment_preview import fake_panel, camera_sample, detection
from tests.test_face_capture import face


class CaptureRecoveryTests(unittest.TestCase):
    def ready(self):
        session=CaptureSession(('new','fixture'))
        session.observe(camera_sample(1,at=10),[detection()],0,10.01)
        session.observe(camera_sample(2,at=10.3),[detection()],0,10.31)
        self.assertTrue(session.ready)
        return session

    def test_duplicates_out_of_order_and_temporary_misses(self):
        session=self.ready()
        previous=session.last_sample.frame_id
        session.observe(camera_sample(1,at=10),[face(identity=1)],0,10.4)
        self.assertEqual(session.last_sample.frame_id,previous)
        self.assertTrue(session.ready)
        session.observe(camera_sample(3,at=10.6),[],0,10.61)
        self.assertFalse(session.ready)
        for seq in (4,5):
            moved=detection();moved[0][:]=[246,123,396,353]
            session.observe(camera_sample(seq,at=10+seq*.3),[moved],0,10+seq*.3+.01)
        self.assertTrue(session.ready)
        self.assertIsNone(session.frozen)

    def test_same_spot_replacement_requires_original_person_or_restart(self):
        session=self.ready()
        session.observe(camera_sample(3,at=10.6),[],0,10.61)
        for seq in (4,5,6):
            session.observe(camera_sample(seq,at=10+seq*.3),[face(identity=1)],0,10+seq*.3+.01)
        self.assertFalse(session.ready)
        self.assertIn('Target changed',session.message)

    def test_double_click_and_preclick_exposure_cannot_complete_request(self):
        session=self.ready()
        self.assertTrue(session.request(10.32))
        request_id=session.request_id
        self.assertFalse(session.request(10.33))
        self.assertEqual(session.request_id,request_id)
        session.observe(camera_sample(3,at=10.31),[detection()],0,10.4)
        self.assertTrue(session.pending)
        self.assertIsNone(session.frozen)
        session.observe(camera_sample(4,at=10.5),[detection()],0,10.51)
        self.assertEqual(session.frozen.frame_id,('camera',4))

    def test_timeout_cancels_old_request_and_allows_recovery(self):
        session=self.ready();session.request(10.32);old=session.request_id
        self.assertTrue(session.expire(10.32+CAPTURE_TIMEOUT))
        self.assertFalse(session.pending)
        self.assertGreater(session.request_id,old)
        self.assertIn('timed out',session.message)
        for seq in (20,21):session.observe(camera_sample(seq,at=seq),[detection()],0,seq+.01)
        self.assertTrue(session.request(21.1))

    def test_late_worker_request_token_is_discarded(self):
        panel,state=fake_panel();session=state['session']
        session.observe(camera_sample(1,at=10),[detection()],0,10.01)
        session.observe(camera_sample(2,at=10.3),[detection()],0,10.31)
        session.request(10.32);old=session.request_id
        session.invalidate('Cancelled')
        state['results'].put(CaptureValidation(session,camera_sample(3,at=10.4),[detection()],None,old,.2))
        panel.get_frame_sample=lambda:camera_sample(4,at=10.5)
        state['worker_busy']=True
        with patch('ui.enrollment.time.monotonic',return_value=10.51),patch('ui.enrollment.threading.Thread'):
            panel._wizard_tick(state)
        self.assertIsNone(session.previous)
        self.assertIsNone(session.frozen)

    def test_timeout_is_visible_even_if_native_worker_does_not_return(self):
        panel,state=fake_panel();state['session']=self.ready()
        state['preview_session']=state['session']
        session=state['session'];session.request(10.32)
        state.update(worker_busy=True,validation_started_at=10.32)
        panel.get_frame_sample=lambda:camera_sample(50,at=14.32)
        with patch('ui.enrollment.time.monotonic',return_value=14.33):panel._wizard_tick(state)
        self.assertFalse(session.pending)
        self.assertIsNone(session.frozen)
        self.assertIn('detector is busy',session.message)
        self.assertEqual(state['skip_btn'].configure.call_args.kwargs['state'],'normal')

    def test_flow_replenishes_points_before_attrition(self):
        tracker=FacePreviewTracker();sample=camera_sample()
        tracker.reset(sample,detection()[0]);self.assertIsNotNone(tracker.points)
        tracker.points=tracker.points[:7]
        self.assertIsNotNone(tracker.advance(sample))
        self.assertGreater(len(tracker.points),7)

    def test_skipping_unconfirmed_angle_preserves_target_continuity(self):
        panel,state=fake_panel();state['session']=self.ready()
        anchor=state['session'].anchor
        panel._wizard_next(state,MagicMock(),'Save')
        np.testing.assert_array_equal(state['session'].anchor,anchor)
        for seq in (3,4):
            state['session'].observe(camera_sample(seq,at=seq),[face(identity=1)],1,seq+.01)
        self.assertFalse(state['session'].ready)
        self.assertIn('Target changed',state['session'].message)

    def test_worker_copies_mutable_provider_before_inference(self):
        panel,state=fake_panel();sample=camera_sample()
        original=sample.frame.copy()
        with patch('ui.enrollment.threading.Thread') as worker:
            panel._start_wizard_validation(state,state['session'],sample)
        sample.frame[:]=255
        worker.call_args.kwargs['target']()
        frame=panel.recognizer.enrollment_faces.call_args.args[0]
        np.testing.assert_array_equal(frame,original)
        self.assertFalse(frame.flags.writeable)
        self.assertFalse(state['worker_busy'])

    def test_sample_copy_failure_releases_worker_lock(self):
        panel,state=fake_panel()
        sample=MagicMock()
        sample.frame.copy.side_effect=RuntimeError('buffer unavailable')
        panel._start_wizard_validation(state,state['session'],sample)
        self.assertFalse(state['worker_busy'])
        self.assertFalse(panel._capture_inference_lock.locked())
        self.assertIn('buffer unavailable',state['session'].message)

    def test_model_failure_is_not_misreported_as_no_face(self):
        recognizer=object.__new__(FaceRecognizer)
        recognizer.last_error='model unavailable'
        with patch.object(recognizer,'_ensure_models',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'model unavailable'):
                recognizer.enrollment_faces(camera_sample().frame)

    def test_payload_rejects_mixed_identity_even_if_caller_bypasses_ui(self):
        key=('new','test')
        first=FaceCapture.freeze(key,camera_sample(),face())
        other=FaceCapture.freeze(key,camera_sample(2),face(identity=1))
        with self.assertRaisesRegex(ValueError,'different targets'):capture_payload([first,other],key)


class EnrollmentAtomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=CBVMSDatabase(Path(self.tmp.name)/'fixture.db');self.db.initialize()
        self.capture=FaceCapture.freeze(('fixture',),camera_sample(),detection())
        self.blob,self.photo=capture_payload([self.capture],('fixture',))

    def insert(self,sid='NEW'):
        return self.db.insert_student(sid,'Fixture','BSIT','3A',self.blob,self.photo,account_password='fixture-password')

    def test_atomic_student_account_and_duplicate_failure(self):
        pk=self.insert()
        self.assertEqual(self.db.verify_student_account('NEW','fixture-password')['student_id'],'NEW')
        with self.assertRaises(Exception):self.insert()
        self.assertEqual(len(self.db.get_all_students()),1)
        row=self.db.get_student(pk)
        self.assertEqual(row['photo'],self.photo)
        np.testing.assert_array_equal(pickle.loads(row['encoding'])[0],self.capture.embedding)

    def test_account_conflict_rolls_back_student_and_update_checks_owner_under_write(self):
        self.db.insert_student_account('different','NEW','password')
        with self.assertRaises(Exception):self.insert()
        self.assertFalse(self.db.student_id_exists('NEW'))
        pk=self.insert('SECOND')
        self.assertFalse(self.db.update_student_encoding(pk,b'bad',b'bad',expected_student_id='NEW'))
        self.assertEqual(self.db.get_student(pk)['photo'],self.photo)

    def test_gallery_load_and_existing_recognition_accept_saved_embedding(self):
        self.insert();recognizer=FaceRecognizer(self.db)
        with patch.object(recognizer,'_ensure_models',return_value=True),patch.object(recognizer,'_detect',return_value=[detection()]):
            result=recognizer.recognize_faces(camera_sample().frame)
        self.assertEqual(result[0]['student_id'],'NEW')
        self.assertTrue(result[0]['matched'])


if __name__=='__main__':unittest.main()
