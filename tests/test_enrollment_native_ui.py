"""Actual enrollment/update widgets, synthetic camera driver, isolated SQLite/gallery."""
import gc
import json
import pickle
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import customtkinter as ctk
import cv2
import numpy as np
from core.camera import CameraCapture
from core.face_capture import guide_geometry
from core.recognizer import FaceRecognizer
from database.db_manager import CBVMSDatabase
from ui.enrollment import EnrollmentPanel


class FixtureCamera:
    """Exercise CameraCapture.read/get_latest_sample with a deterministic fake driver."""
    def __init__(self):
        self.camera=CameraCapture();self.camera._cap=self;self.camera.is_open=True
        self.step=0;self.person=0;self.disconnected=False
        self.stop=threading.Event();self.thread=threading.Thread(target=self.pump,daemon=True)
        self.thread.start()

    def read(self):
        frame=np.full((480,640,3),80+self.step*20,np.uint8)
        frame[0,0]=self.step;frame[0,1]=self.person
        return True,frame

    def release(self):pass

    def pump(self):
        while not self.stop.wait(1/30):
            if not self.disconnected:self.camera.read()

    def latest(self):
        return None if self.disconnected else self.camera.get_latest_sample()

    def close(self):
        self.stop.set();self.thread.join(2);self.camera.release()


class EnrollmentNativeTests(unittest.TestCase):
    metrics=[]
    def setUp(self):
        gc.collect()
        self.tmp=tempfile.TemporaryDirectory()
        self.db=CBVMSDatabase(Path(self.tmp.name)/'native.db');self.db.initialize()
        self.camera=FixtureCamera()
        self.recognizer=FaceRecognizer(self.db)
        self.recognizer._ensure_models=lambda:True
        self.inference_delay=.08
        def detect(frame):
            time.sleep(self.inference_delay)
            step,person=int(frame[0,0,0]),int(frame[0,1,0])
            if person==9:return []
            cx,cy,_,_=guide_geometry(frame.shape,min(step,2))
            embedding=np.zeros(512,np.float32);embedding[min(person,2)]=1
            return [([int(cx-80),int(cy-110),int(cx+80),int(cy+110)],embedding,.99,None)]
        self.recognizer._detect=detect
        self.root=ctk.CTk();self.root.geometry('1100x900')
        self.errors=[];self.root.report_callback_exception=lambda *exc:self.errors.append(exc)
        self.panel=EnrollmentPanel(self.root,self.db,self.recognizer,
            get_frame=lambda:None,get_frame_sample=self.camera.latest)
        self.panel.pack(fill='both',expand=True)
        original_form=self.panel._build_enroll_form
        def form(body,state):self.state=state;original_form(body,state)
        self.panel._build_enroll_form=form
        original_wizard=self.panel._build_capture_wizard
        def wizard(card,state,**kwargs):self.state=state;original_wizard(card,state,**kwargs)
        self.panel._build_capture_wizard=wizard
        self.renders=[]
        render=self.panel._render_capture
        def render_frame(state,frame,box=None,**kw):
            if not kw.get('frozen'):self.renders.append(time.monotonic())
            render(state,frame,box,**kw)
        self.panel._render_capture=render_frame
        self.no_flow=patch('core.face_capture.cv2.goodFeaturesToTrack',return_value=None)
        self.no_flow.start()

    def tearDown(self):
        if hasattr(self,'state'):
            self.state['alive']=False
            self.state['session'].invalidate('Test closed') if self.state.get('session') else None
        self.camera.close()
        self.assertTrue(self.panel._capture_inference_lock.acquire(timeout=3))
        self.panel._capture_inference_lock.release()
        self.no_flow.stop()
        for job in self.root.tk.splitlist(self.root.tk.call('after','info')):
            self.root.tk.call('after','cancel',job)
        self.root.destroy();gc.collect();self.tmp.cleanup()
        self.assertEqual(self.errors,[])

    @classmethod
    def tearDownClass(cls):print('ENROLLMENT_NATIVE_METRICS='+json.dumps(cls.metrics))

    def until(self,predicate,seconds=6):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:
            self.root.after(15,self.root.quit);self.root.mainloop()
        self.assertTrue(predicate(),self.state.get('session').message if self.state.get('session') else 'wizard did not start')

    def begin(self,sid='FIXTURE'):
        self.panel._open_enroll_flow()
        for key,value in dict(name='Disposable Fixture',student_id=sid,course='BSIT',year_and_section='3A').items():
            self.panel._entries[key].insert(0,value)
        self.panel._enroll_continue(self.state)

    def capture(self,step):
        self.camera.step=step
        began=time.monotonic();render_count=len(self.renders)
        self.until(lambda:self.state['session'].ready)
        ready=time.monotonic()
        self.assertEqual(self.state['cap_btn'].cget('state'),'normal')
        self.state['cap_btn'].invoke()
        self.assertIn('Validating capture',self.state['det_status'].cget('text'))
        self.state['cap_btn'].invoke()  # disabled double click cannot create another request
        self.until(lambda:self.state['session'].frozen is not None)
        frozen=self.state['session'].frozen
        self.assertGreater(frozen.captured_at,self.state['session'].requested_at)
        x1,y1,x2,y2=frozen.box
        exact=cv2.imencode('.jpg',frozen.frame[y1:y2,x1:x2],[cv2.IMWRITE_JPEG_QUALITY,90])[1].tobytes()
        self.assertEqual(frozen.photo,exact)
        self.assertFalse(frozen.frame.flags.writeable)
        self.assertIsNone(self.state['preview_tracker'].sample)
        ended=time.monotonic()
        self.metrics.append(dict(angle=step,ready_ms=round((ready-began)*1000,1),
            capture_ms=round((ended-ready)*1000,1),live_renders=len(self.renders)-render_count,
            interval_seconds=round(ended-began,3)))
        self.state['cap_btn'].invoke()
        return frozen

    def test_three_repeated_full_enrollments_without_flow_then_real_gallery_match(self):
        for attempt in range(3):
            self.camera.step=0
            self.begin(f'FIXTURE-{attempt}')
            captures=[self.capture(step) for step in range(3)]
            self.assertTrue(self.state['reviewing'])
            # Final review cycles actual confirmed captures, not the live source.
            buttons=[w for w in self.state['review_controls'].winfo_children() if isinstance(w,ctk.CTkButton)]
            buttons[-1].invoke();self.assertEqual(self.state['review_index'],1)
            calls=[]
            real=self.db.insert_student
            def saved(**kw):calls.append(kw);return real(**kw)
            with patch.object(self.db,'insert_student',side_effect=saved):
                self.state['cap_btn'].invoke();self.state['cap_btn'].invoke()
                self.until(lambda:self.state.get('saved',False))
            self.assertEqual(len(calls),1)
            row=self.db.get_student_by_student_id(f'FIXTURE-{attempt}')
            self.assertEqual(row['photo'],captures[0].photo)
            for stored,reviewed in zip(pickle.loads(row['encoding']),captures):
                np.testing.assert_array_equal(stored,reviewed.embedding)
            self.assertEqual(len(self.db.get_all_student_accounts()),attempt+1)
            self.assertEqual(len(self.recognizer._known),attempt+1)
            if attempt==0:
                result=self.recognizer.recognize_faces(captures[0].frame)
                self.assertEqual(result[0]['student_id'],'FIXTURE-0')

    def test_failed_save_preserves_details_and_captures_then_retry(self):
        self.begin();captured=self.capture(0)
        self.state['skip_btn'].invoke();self.state['skip_btn'].invoke()
        with patch.object(self.db,'insert_student',side_effect=RuntimeError('fixture disk failure')):
            self.state['cap_btn'].invoke()
            self.until(lambda:not self.state['capturing'])
        self.assertEqual(self.panel._entries['student_id'].get(),'FIXTURE')
        self.assertEqual(self.state['angle_frames']['front'][0].photo,captured.photo)
        self.assertFalse(self.db.student_id_exists('FIXTURE'))
        self.state['cap_btn'].invoke();self.until(lambda:self.state.get('saved',False))
        self.assertEqual(self.db.get_student_by_student_id('FIXTURE')['photo'],captured.photo)

    def test_update_photo_retake_and_owner_preservation(self):
        pk=self.db.insert_student('UPDATE','Fixture','BSIT','3A',b'',b'old')
        other=self.db.insert_student('OTHER','Other','BSIT','3A',b'',b'untouched')
        self.panel._selected_pk=pk
        self.panel._open_update_modal(dict(self.db.get_student(pk)))
        first=self.capture(0)
        self.state['skip_btn'].invoke();self.state['skip_btn'].invoke()
        self.state['skip_btn'].invoke() # Retake from final review
        self.assertEqual(self.state['angle_frames'],{})
        self.camera.step=0
        final=self.capture(0)
        self.assertNotEqual(first.frame_id,final.frame_id)
        self.state['skip_btn'].invoke();self.state['skip_btn'].invoke()
        self.state['cap_btn'].invoke();self.until(lambda:self.state.get('saved',False))
        self.assertEqual(self.db.get_student(pk)['photo'],final.photo)
        self.assertEqual(self.db.get_student(other)['photo'],b'untouched')

    def test_postclick_departure_replacement_and_camera_switch_do_not_freeze(self):
        self.begin()
        for person in (9,1):
            self.camera.person=0
            self.until(lambda:self.state['session'].ready)
            self.state['cap_btn'].invoke()
            self.camera.person=person
            self.until(lambda:not self.state['session'].pending)
            self.assertIsNone(self.state['session'].frozen)
            self.assertFalse(self.db.student_id_exists('FIXTURE'))
        self.camera.person=0
        self.until(lambda:self.state['session'].ready)
        self.inference_delay=.3
        self.state['cap_btn'].invoke()
        self.until(lambda:self.state['worker_busy'])
        with self.camera.camera._lock:
            self.camera.camera._source_token=uuid.uuid4().hex
        self.until(lambda:not self.state['session'].pending)
        self.assertIsNone(self.state['session'].frozen)
        self.until(lambda:self.state['session'].ready)

    def test_native_capture_deadline_keeps_preview_alive_and_discards_late_result(self):
        self.begin();self.until(lambda:self.state['session'].ready)
        entered,release=threading.Event(),threading.Event()
        original=self.recognizer._detect
        def blocked(frame):
            entered.set();release.wait(6)
            return original(frame)
        self.recognizer._detect=blocked
        count=len(self.renders);started=time.monotonic()
        try:
            self.state['cap_btn'].invoke()
            self.until(entered.is_set)
            self.until(lambda:not self.state['session'].pending)
            elapsed=time.monotonic()-started
            self.assertLess(elapsed,4.4)
            self.assertIsNone(self.state['session'].frozen)
            self.assertGreater(len(self.renders)-count,30)
            self.assertTrue('timed out' in self.state['session'].message or 'busy' in self.state['session'].message)
            self.metrics.append(dict(timeout_seconds=round(elapsed,3),live_renders=len(self.renders)-count))
        finally:
            release.set();self.recognizer._detect=original
        self.until(lambda:self.state['session'].ready)
        self.assertIsNone(self.state['session'].frozen)

    def test_back_during_inference_preserves_details_reopen_and_disconnect_recovers(self):
        self.inference_delay=.4;self.begin()
        self.until(lambda:self.state['worker_busy'])
        old=self.state['session']
        self.panel._enroll_back_to_form(self.state)
        self.assertEqual(self.panel._entries['student_id'].get(),'FIXTURE')
        self.panel._enroll_continue(self.state)
        self.assertIsNot(self.state['session'],old)
        self.until(lambda:self.state['session'].ready)
        self.camera.disconnected=True
        self.until(lambda:not self.state['session'].ready)
        self.assertIn('stale',self.state['session'].message)
        self.camera.disconnected=False
        with self.camera.camera._lock:
            self.camera.camera._source_token=uuid.uuid4().hex
        self.until(lambda:self.state['session'].ready)
        self.assertIsNone(self.state['session'].frozen)
        self.assertFalse(self.db.student_id_exists('FIXTURE'))


if __name__=='__main__':unittest.main()
