"""Registration preview scheduling, lifecycle, and capture safety without hardware."""
import queue
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from auth.register import StudentRegistrationWindow
from core.camera import CameraSample
from core.face_capture import CaptureSession, FacePreviewTracker, FaceCapture
from core.registration_camera import RegistrationCamera, PreviewMetrics


def camera_sample(seq=1, at=None, shift=0, source='camera'):
    frame = np.zeros((480, 640, 3), np.uint8)
    rng = np.random.default_rng(4)
    frame[120:350, 240+shift:390+shift] = rng.integers(0, 255, (230,150,3), dtype=np.uint8)
    return CameraSample(frame, (source, seq), time.monotonic() if at is None else at)


def detection():
    emb = np.zeros(512, np.float32)
    emb[0] = 1
    return ([240,120,390,350], emb, .9, None)


def fake_window(source=None):
    source = source or SimpleNamespace(latest=lambda:None, events=queue.Queue(),
                                      stop=MagicMock(), frame_times=deque())
    win = SimpleNamespace(_alive=True, winfo_exists=lambda:True, _camera_source=source,
        _stop_event=threading.Event(), _capture_session=None, _captured_frame=None,
        _capture_results=queue.Queue(), _capture_busy=False, _model_results=queue.Queue(),
        _model_load_failed=False, _recognizer=None, _model_cache={},
        _preview_job=None, _success_job=None, _initial_jobs=[], _camera_error=None,
        _next_validation=0, _last_rendered=None, _tracker=FacePreviewTracker(),
        _metrics=PreviewMetrics(), _next_diagnostics=float('inf'), _feedback_state=None,
        _submitting=False, _canvas=MagicMock(), _canvas_item=None, _cam_status=MagicMock(),
        _cap_btn=MagicMock(), _dot=MagicMock(), _err_lbl=MagicMock(),
        _capture_key=lambda:('register','S1'), after=MagicMock(return_value='tick'),
        after_cancel=MagicMock(), destroy=MagicMock(), grab_release=MagicMock())
    for name in ('_live_tick','_start_validation','_feedback','_render_frame','_capture_face',
                 '_shutdown_camera','_on_destroy','_close','_load_recognizer'):
        setattr(win, name, getattr(StudentRegistrationWindow,name).__get__(win))
    win._render_frame = MagicMock()
    return win


class PreviewLoopTests(unittest.TestCase):
    def test_preview_renders_while_model_loading_or_inference_busy(self):
        for busy in (False, True):
            win = fake_window()
            win._capture_busy = busy
            for i in range(8):
                current = camera_sample(i)
                win._camera_source.latest = lambda:current
                win._live_tick()
            self.assertEqual(win._render_frame.call_count, 8)
            self.assertEqual(win._cap_btn.configure.call_count, 1)
            self.assertEqual(win._cam_status.configure.call_count, 1)
            self.assertEqual(win._metrics.summary([])['rendered_frames'], 8)

    def test_repeated_cached_sample_is_not_rendered_or_counted_twice(self):
        win = fake_window()
        win._camera_source.latest = lambda:camera_sample(1)
        for _ in range(5):
            win._live_tick()
        win._render_frame.assert_called_once()
        self.assertEqual(len(win._metrics.render_times), 1)

    def test_validation_is_throttled_and_only_one_inflight(self):
        win = fake_window()
        win._recognizer = MagicMock()
        win._capture_session = CaptureSession(win._capture_key())
        with patch('auth.register.threading.Thread') as worker:
            for i in range(5):
                current = camera_sample(i, at=10+i*.01)
                win._camera_source.latest = lambda:current
                with patch('auth.register.time.monotonic', return_value=10+i*.01):
                    win._live_tick()
            worker.assert_called_once()
            win._capture_busy = False  # completed at t=10.04; next dispatch still rate limited
            with patch('auth.register.time.monotonic', return_value=10.10):
                win._live_tick()
            worker.assert_called_once()
        self.assertEqual(win._render_frame.call_count, 5)

    def test_result_never_rewinds_live_preview_to_analyzed_frame(self):
        win = fake_window()
        session = CaptureSession(win._capture_key())
        win._capture_session = session
        analyzed = camera_sample(1, at=10)
        current = camera_sample(7, at=10.2, shift=8)
        win._capture_results.put((session, analyzed, [detection()], .2))
        win._camera_source.latest = lambda:current
        with patch('auth.register.time.monotonic', return_value=10.21):
            win._live_tick()
        self.assertIs(win._render_frame.call_args.args[0], current.frame)
        self.assertIsNotNone(win._render_frame.call_args.args[1])

    def test_tracking_loss_cancels_pending_capture(self):
        win = fake_window()
        session = CaptureSession(win._capture_key())
        win._capture_session = session
        first, second = camera_sample(1, at=10), camera_sample(2, at=10.1)
        session.observe(first, [detection()], 0, 10.01)
        session.observe(second, [detection()], 0, 10.11)
        session.request(10.12)
        win._tracker.reset(second, detection()[0])
        blank = CameraSample(np.zeros_like(second.frame), ('camera',3), 10.2)
        win._camera_source.latest = lambda:blank
        with patch('auth.register.time.monotonic', return_value=10.21):
            win._live_tick()
        self.assertFalse(session.pending)
        self.assertFalse(session.ready)
        self.assertIsNone(session.frozen)

    def test_capture_freezes_exact_validated_frame_and_retake_resumes_live(self):
        win = fake_window()
        session = CaptureSession(win._capture_key())
        win._capture_session = session
        first, second, captured = (camera_sample(i, at=10+i*.1) for i in (1,2,3))
        session.observe(first,[detection()],0,10.11)
        session.observe(second,[detection()],0,10.21)
        win._tracker.reset(second,detection()[0])
        with patch('auth.register.time.monotonic', return_value=10.22):
            win._capture_face()
        latest = camera_sample(4,at=10.4)
        win._capture_results.put((session,captured,[detection()],.1))
        win._camera_source.latest = lambda:latest
        with patch('auth.register.time.monotonic', return_value=10.41):
            win._live_tick()
        self.assertEqual(session.frozen.frame_id, captured.frame_id)
        self.assertTrue(win._render_frame.call_args.kwargs['frozen'])
        np.testing.assert_array_equal(win._render_frame.call_args.args[0],
            cv2.imdecode(np.frombuffer(session.frozen.photo,np.uint8),cv2.IMREAD_COLOR))
        count = win._render_frame.call_count
        win._live_tick()
        self.assertEqual(win._render_frame.call_count, count)
        win._capture_face()
        self.assertIsNone(win._captured_frame)
        self.assertIsNot(win._capture_session,session)
        win._capture_results.put((session,captured,[detection()],.1))
        with patch('auth.register.time.monotonic', return_value=10.42):
            win._live_tick()
        self.assertIsNone(win._capture_session.frozen)
        self.assertIs(win._render_frame.call_args.args[0],latest.frame)

    def test_close_and_parent_destroy_cancel_callbacks_and_source_once(self):
        win = fake_window()
        win._preview_job='preview'
        win._success_job='success'
        win._initial_jobs=['initial']
        win._close()
        win._on_destroy(SimpleNamespace(widget=win))
        self.assertTrue(win._stop_event.is_set())
        win._camera_source.stop.assert_called_once()
        self.assertEqual(win.after_cancel.call_count,3)
        win._live_tick()
        win.after.assert_not_called()

    def test_old_waiting_inference_after_retake_does_not_leave_busy_stuck(self):
        win = fake_window()
        old = CaptureSession(win._capture_key())
        win._capture_session=CaptureSession(win._capture_key())
        win._recognizer=MagicMock()
        win._start_validation(old,camera_sample())
        owner, _, faces, _ = win._capture_results.get(timeout=2)
        self.assertIs(owner,old)
        self.assertEqual(faces,[])
        win._recognizer.enrollment_faces.assert_not_called()

    def test_model_is_reused_when_reopening(self):
        win = fake_window()
        recognizer = MagicMock()
        win._model_cache['recognizer'] = recognizer
        with patch('core.recognizer.FaceRecognizer') as constructor:
            win._load_recognizer()
            model, error = win._model_results.get(timeout=2)
        constructor.assert_not_called()
        self.assertIs(model,recognizer)
        self.assertIsNone(error)


class CameraOwnerTests(unittest.TestCase):
    def factory(self, open_gate=None):
        self.operations=[]
        self.kwargs=[]
        outer=self
        class FakeCamera:
            def __init__(self,**kwargs):
                outer.kwargs.append(kwargs)
                self.seq=0
            def open(self):
                outer.operations.append(('open',threading.get_ident()))
                if open_gate:
                    open_gate.wait(2)
                return True
            def get_settings(self):
                return {'fps':24,'width':1280,'height':720}
            def read(self):
                outer.operations.append(('read',threading.get_ident()))
                time.sleep(.01)
                self.seq+=1
                self.sample=camera_sample(self.seq)
                return self.sample.frame
            def get_latest_sample(self):
                return self.sample
            def release(self):
                outer.operations.append(('release',threading.get_ident()))
        return FakeCamera

    def test_requests_30_but_reports_actual_and_owns_all_camera_operations(self):
        source=RegistrationCamera(camera_factory=self.factory())
        source.start();source.start()
        event,settings=source.events.get(timeout=2)
        self.assertEqual(event,'opened')
        self.assertEqual(settings['fps'],24)
        self.assertEqual(self.kwargs,[dict(camera_index=0,width=1280,height=720,fps_cap=30)])
        time.sleep(.04)
        self.assertIsNotNone(source.latest())
        source.stop()
        self.assertTrue(source.done.wait(2))
        self.assertIsNone(source.latest())
        self.assertEqual(len({ident for _,ident in self.operations}),1)
        self.assertNotEqual(self.operations[0][1],threading.get_ident())
        self.assertEqual(sum(name=='release' for name,_ in self.operations),1)

    def test_close_during_open_releases_late_camera_and_reopen_waits(self):
        gate=threading.Event()
        factory=self.factory(gate)
        old=RegistrationCamera(camera_factory=factory)
        new=RegistrationCamera(camera_factory=factory)
        old.start()
        deadline=time.monotonic()+2
        while not self.operations and time.monotonic()<deadline:
            time.sleep(.001)
        old.stop()
        new.start()
        time.sleep(.03)
        self.assertEqual(sum(name=='open' for name,_ in self.operations),1)
        gate.set()
        self.assertTrue(old.done.wait(2))
        event,_=new.events.get(timeout=2)
        self.assertEqual(event,'opened')
        new.stop();self.assertTrue(new.done.wait(2))
        names=[name for name,_ in self.operations]
        self.assertLess(names.index('release'),names.index('open',1))
        self.assertTrue(old.events.empty())

    def test_five_reopen_cycles_do_not_accumulate_owners(self):
        factory=self.factory()
        for _ in range(5):
            source=RegistrationCamera(camera_factory=factory)
            source.start()
            self.assertEqual(source.events.get(timeout=2)[0],'opened')
            source.stop()
            self.assertTrue(source.done.wait(2))
            source._thread.join(timeout=1)
            self.assertFalse(source._thread.is_alive())
        names=[name for name,_ in self.operations]
        self.assertEqual(names.count('open'),5)
        self.assertEqual(names.count('release'),5)

    def test_release_failure_still_signals_done_and_allows_reopen(self):
        factory=self.factory()
        class BrokenRelease(factory):
            def release(self):
                super().release()
                raise RuntimeError('driver teardown error')
        source=RegistrationCamera(camera_factory=BrokenRelease)
        source.start()
        source.events.get(timeout=2)
        source.stop()
        self.assertTrue(source.done.wait(2))
        source=RegistrationCamera(camera_factory=factory)
        source.start()
        self.assertEqual(source.events.get(timeout=2)[0],'opened')
        source.stop()
        self.assertTrue(source.done.wait(2))

    def test_settings_report_negotiated_values_instead_of_requested_fps(self):
        from core.camera import CameraCapture
        camera=CameraCapture(fps_cap=30)
        camera._cap=MagicMock()
        camera._cap.getBackendName.return_value='AVFOUNDATION'
        actual={cv2.CAP_PROP_FRAME_WIDTH:1280,cv2.CAP_PROP_FRAME_HEIGHT:720,cv2.CAP_PROP_FPS:24}
        camera._cap.get.side_effect=actual.get
        self.assertEqual(camera.get_settings(),dict(backend='AVFOUNDATION',width=1280,height=720,
                                                    fps=24,requested_fps=30))
        camera._cap.set.assert_not_called()


class OpticalFlowTests(unittest.TestCase):
    def test_overlay_follows_moving_face_but_cannot_supply_saved_embedding(self):
        tracker=FacePreviewTracker()
        first=camera_sample(1,at=10)
        tracker.reset(first,detection()[0])
        box=tracker.advance(camera_sample(2,at=10.03,shift=8))
        self.assertIsNotNone(box)
        np.testing.assert_allclose(box,np.array(detection()[0])+[8,0,8,0],atol=2)
        self.assertFalse(hasattr(tracker,'embedding'))
        self.assertIsNone(tracker.advance(camera_sample(3,at=12)))

    def test_camera_switch_and_blank_face_clear_overlay(self):
        for sample in (camera_sample(2,source='different'),
                       CameraSample(np.zeros((480,640,3),np.uint8),('camera',2),time.monotonic()+.03)):
            tracker=FacePreviewTracker()
            tracker.reset(camera_sample(),detection()[0])
            self.assertIsNone(tracker.advance(sample))


if __name__=='__main__':
    unittest.main()
