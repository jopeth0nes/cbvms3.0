"""No model/camera required: guide geometry, tracking, and frame/identity binding."""
import pickle
import queue
import threading
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from core.camera import CameraCapture, CameraSample
from core.face_capture import (AMBIGUOUS_MESSAGE, POSITION_MESSAGE, CaptureSession,
                               FaceCapture, capture_payload, guide_geometry, select_target)
from database.db_manager import CBVMSDatabase
from ui.enrollment import EnrollmentPanel


def face(box=(220, 100, 400, 360), identity=0, score=.75):
    emb = np.zeros(512, np.float32)
    emb[identity] = 1
    return (list(box), emb, score, None)


def sample(seq, at=None, source=1):
    frame = np.zeros((480, 640, 3), np.uint8)
    frame[100:360, 220:400] = (20, 100, seq)
    frame[40:300, 10:150] = (200, 10, 10)
    return CameraSample(frame, (source, seq), float(seq) if at is None else at)


class TargetSelectionTests(unittest.TestCase):
    def test_centered_alone(self):
        selected, _ = select_target([face()], (480, 640, 3))
        self.assertEqual(selected[0], face()[0])

    def test_bystander_order_area_and_confidence_do_not_choose_target(self):
        centered = face()
        outsiders = [face((0, 0, 180, 479), 1, .999), face((500, 290, 570, 380), 2, .99)]
        for detections in ([*outsiders, centered], [centered, *outsiders]):
            selected, _ = select_target(detections, (480, 640, 3))
            self.assertIs(selected, centered)

    def test_outside_only_and_empty_block(self):
        for detections in ([], [face((0, 0, 180, 479))]):
            self.assertEqual(select_target(detections, (480, 640, 3)), (None, POSITION_MESSAGE))

    def test_overlapping_guide_is_ambiguous_even_if_one_center_outside(self):
        for bystander in (face((350, 180, 450, 320), 1), face((430, 210, 530, 290), 1)):
            self.assertEqual(select_target([face(), bystander], (480, 640, 3)), (None, AMBIGUOUS_MESSAGE))

    def test_invalid_or_clipped_face_never_captured(self):
        for box in ((220, 100, 200, 360), (float('nan'), 100, 400, 360), (-10, 50, 640, 430)):
            self.assertIsNone(select_target([face(box)], (480, 640, 3))[0])

    def test_resizing_preserves_selection_for_every_angle(self):
        for step in range(3):
            cx, cy, _, _ = guide_geometry((480, 640), step)
            box = np.array((cx-60, cy-100, cx+60, cy+100))
            for width, height in ((320, 240), (520, 390), (960, 360)):
                scale = np.array((width/640, height/480)*2)
                selected, _ = select_target([face(box*scale)], (height, width), step)
                self.assertIsNotNone(selected)


class CaptureStateTests(unittest.TestCase):
    def ready(self):
        session = CaptureSession(('update', 7, 'S7'))
        session.observe(sample(1), [face()], 0, 1.1)
        self.assertFalse(session.ready)
        session.observe(sample(2), [face()], 0, 2.1)
        self.assertTrue(session.request(2.2))
        return session

    def test_capture_freezes_one_frame_box_embedding_and_crop(self):
        session = self.ready()
        chosen = face()
        source = sample(3)
        session.observe(source, [face((0, 0, 180, 479), 1, .99), chosen], 0, 3.1)
        capture = session.frozen
        self.assertEqual(capture.frame_id, (1, 3))
        x1, y1, x2, y2 = capture.box
        expected = cv2.imencode('.jpg', source.frame[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
        self.assertEqual(capture.photo, expected)
        np.testing.assert_array_equal(capture.embedding, chosen[1])
        source.frame[:] = 255
        chosen[1][:] = 0
        self.assertEqual(capture.frame[150, 250, 2], 3)
        self.assertEqual(capture.embedding[0], 1)
        self.assertFalse(capture.frame.flags.writeable)
        self.assertFalse(capture.embedding.flags.writeable)

    def test_moving_away_ambiguity_switch_or_identity_swap_cancels(self):
        for detections, source in (([], 1), ([face(), face((350, 180, 450, 320), 1)], 1),
                                    ([face(identity=1)], 1), ([face()], 2)):
            session = self.ready()
            session.observe(sample(3, source=source), detections, 0, 3.1)
            self.assertIsNone(session.frozen)
            self.assertFalse(session.pending)
            self.assertFalse(session.ready)

    def test_cached_and_stale_frames_cannot_capture(self):
        session = self.ready()
        session.observe(sample(2), [face()], 0, 2.4)
        self.assertIsNone(session.frozen)
        session.observe(sample(3), [face()], 0, 9)
        self.assertIsNone(session.frozen)
        self.assertFalse(session.pending)
        session = CaptureSession(('update', 7))
        session.observe(sample(1), [face()], 0, 1.1)
        session.observe(sample(1), [face()], 0, 1.2)
        self.assertFalse(session.request(1.3))

    def test_student_mismatch_rejected(self):
        session = self.ready()
        session.observe(sample(3), [face()], 0, 3.1)
        with self.assertRaises(ValueError):
            capture_payload([session.frozen], ('update', 8, 'S8'))

    def test_camera_sample_pixels_sequence_and_timestamp_are_atomic(self):
        camera = CameraCapture()
        camera._cap = MagicMock()
        camera._cap.read.return_value = (True, sample(1).frame)
        camera.is_open = True
        with patch('core.camera.time.monotonic', return_value=42):
            camera.read()
        snapshot = camera.get_latest_sample()
        self.assertEqual(snapshot.captured_at, 42)
        self.assertEqual(snapshot.frame_id[1], 1)
        camera._latest_frame[:] = 255
        self.assertNotEqual(int(snapshot.frame[0, 0, 0]), 255)
        camera.release()
        self.assertIsNone(camera.get_latest_sample())


class SaveFlowTests(unittest.TestCase):
    def panel(self, db, pk, sid):
        panel = SimpleNamespace(database=db, _selected_pk=pk, recognizer=MagicMock(),
                                _reload_students=MagicMock(), _set_status=MagicMock(),
                                _capture_inference_lock=threading.Lock())
        for method in ('_capture_student_key', '_capture_current', '_capture_save_payload', '_finish_update',
                       '_ordered_captures', '_wizard_retake', '_wizard_refresh', '_update_pills',
                       '_wizard_tick', '_render_capture', '_show_frozen', '_wizard_next',
                       '_wizard_skip', '_wizard_capture', '_init_wizard_preview', '_wizard_feedback',
                       '_start_wizard_validation'):
            original = getattr(EnrollmentPanel, method)
            setattr(panel, method, original if method in ('_ordered_captures', '_init_wizard_preview', '_wizard_feedback') else original.__get__(panel))
        def save_immediately(state,operation,completed):
            state['capturing'] = True
            try:
                result = operation()
            except Exception as exc:
                state['capturing'] = False
                state['det_status'].configure(text=str(exc))
                return
            state['capturing'] = False
            state['saved'] = True
            completed(result)
        panel._save_capture_async = save_immediately
        panel._clear_form = MagicMock()
        state = dict(alive=True, modal=MagicMock(), target_pk=pk, target_student_id=sid,
                     student_key=('update', pk, sid), reviewing=True, capturing=False,
                     step=3, cap_btn=MagicMock(), skip_btn=MagicMock(), det_status=MagicMock(),
                     dot=MagicMock(), close=MagicMock(), circles=[MagicMock() for _ in range(3)],
                     step_caption=MagicMock(), identity='Student Seven · S7', pills={}, finish_text='Save', preview_w=320,
                     preview_h=240, canvas=MagicMock(), canvas_item=None)
        captured = FaceCapture.freeze(state['student_key'], sample(3), face())
        state['angle_frames'] = {'front': [captured]}
        state['session'] = CaptureSession(state['student_key'])
        panel._init_wizard_preview(state)
        return panel, state, captured

    def test_update_saves_exact_frozen_payload_only_to_selected_student(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = CBVMSDatabase(Path(tmp)/'capture.db')
            db.initialize()
            pk = db.insert_student('S7', 'Student Seven', 'BSIT', '1A', b'old', b'old')
            other = db.insert_student('S8', 'Student Eight', 'BSIT', '1A', b'other', b'other')
            panel, state, capture = self.panel(db, pk, 'S7')
            panel._finish_update(state['modal'], state)
            saved = db.get_student(pk)
            self.assertEqual(saved['photo'], capture.photo)
            np.testing.assert_array_equal(pickle.loads(saved['encoding'])[0], capture.embedding)
            self.assertEqual(db.get_student(other)['photo'], b'other')
            panel.recognizer.enrollment_faces.assert_not_called()
            panel.recognizer.encode_face_multi.assert_not_called()
            state['close'].assert_called_once()

    def test_student_switch_closed_flow_and_record_remap_prevent_writes(self):
        for change in ('selection', 'close', 'remap', 'delete'):
            db = MagicMock()
            db.get_student.return_value = {'student_id': 'S7'}
            panel, state, capture = self.panel(db, 7, 'S7')
            if change == 'selection':
                panel._selected_pk = 8
            elif change == 'close':
                state['alive'] = False
            elif change == 'delete':
                db.get_student.return_value = None
            else:
                db.get_student.return_value = {'student_id': 'S8'}
            panel._finish_update(state['modal'], state)
            db.update_student_encoding.assert_not_called()

    def test_retake_discards_frozen_payload_and_requires_new_capture(self):
        panel, state, capture = self.panel(MagicMock(), 7, 'S7')
        old_session = state['session']
        panel._wizard_retake(state)
        self.assertEqual(state['angle_frames'], {})
        self.assertFalse(state['reviewing'])
        self.assertIsNot(state['session'], old_session)
        with self.assertRaises(ValueError):
            panel._capture_save_payload(state)

    def test_preview_decodes_exact_saved_photo_bytes(self):
        panel, state, capture = self.panel(MagicMock(), 7, 'S7')
        panel._render_capture = MagicMock()
        panel._show_frozen(state, capture)
        decoded = cv2.imdecode(np.frombuffer(capture.photo, np.uint8), cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(panel._render_capture.call_args.args[1], decoded)
        self.assertTrue(panel._render_capture.call_args.kwargs['frozen'])

    def test_highlight_and_guide_mirror_and_resize_with_frame(self):
        panel, state, capture = self.panel(MagicMock(), 7, 'S7')
        panel._draw_pose_guide = EnrollmentPanel._draw_pose_guide
        state['step'] = 1
        state['mirror'] = MagicMock()
        frame = sample(3).frame
        images = []
        with patch('ui.enrollment.ImageTk.PhotoImage', side_effect=lambda image, **kw: images.append(np.array(image)) or MagicMock()):
            state['mirror'].display_mirror.return_value = False
            panel._render_capture(state, frame, capture.box)
            state['mirror'].display_mirror.return_value = True
            panel._render_capture(state, frame, capture.box)
        np.testing.assert_array_equal(images[1], images[0][:, ::-1])
        self.assertEqual(images[0].shape[:2], (240, 320))

    def test_old_detection_callback_after_retake_is_ignored(self):
        panel, state, _ = self.panel(MagicMock(), 7, 'S7')
        old = state['session']
        panel._wizard_retake(state)
        state.update(results=queue.Queue(), worker_busy=True, mirror=MagicMock())
        state['results'].put((old, sample(3), [face(identity=1)], None))
        panel.get_frame_sample = lambda: sample(4)
        panel._render_capture = MagicMock()
        with patch('ui.enrollment.time.monotonic', return_value=4.1), patch('ui.enrollment.threading.Thread'):
            panel._wizard_tick(state)
        self.assertIsNone(state['session'].previous)
        self.assertIsNone(state['session'].frozen)
        # Old inference cannot select/freeze a face, but the latest live frame still paints.
        panel._render_capture.assert_called_once()
        self.assertIsNone(panel._render_capture.call_args.args[2])

    def test_capture_ignores_inflight_preclick_frame_then_freezes_postclick_frame(self):
        panel, state, _ = self.panel(MagicMock(), 7, 'S7')
        state.update(reviewing=False, step=0, results=queue.Queue(), worker_busy=True,
                     requested_at=2.5, mirror=MagicMock())
        session = state['session']
        session.observe(sample(1), [face()], 0, 1.1)
        session.observe(sample(2), [face()], 0, 2.1)
        session.request(2.5)
        state['preview_session'] = session
        state['preview_tracker'] = MagicMock()
        state['preview_tracker'].advance.return_value = face()[0]
        state['results'].put((session, sample(3, at=2.4), [face()], None))
        panel.get_frame_sample = lambda: sample(4, at=2.8)
        panel._render_capture = MagicMock()
        panel._show_frozen = MagicMock()
        with patch('ui.enrollment.time.monotonic', return_value=2.9), patch('ui.enrollment.threading.Thread'):
            panel._wizard_tick(state)
            self.assertIsNone(session.frozen)
            state['results'].put((session, sample(4, at=2.8), [face()], None))
            panel._wizard_tick(state)
        self.assertEqual(session.frozen.frame_id, (1, 4))
        panel._show_frozen.assert_called_once_with(state, session.frozen)

    def test_enrollment_insert_uses_reviewed_face_without_new_inference(self):
        panel, state, captured = self.panel(MagicMock(), 7, 'S7')
        del state['target_pk'], state['target_student_id']
        values = dict(name='New Student', student_id='NEW', course='BSIT', year_and_section='1A',
                      email='', mobile_number='')
        panel._entries = {key: MagicMock() for key in values}
        for key, value in values.items():
            panel._entries[key].get.return_value = value
        from core.academics import academic_values, COURSES
        panel._academics = MagicMock()
        panel._academics.values.return_value = academic_values(*COURSES['it'], '1', 'A')
        panel._academics.raw.return_value = (*COURSES['it'], '1st Year', 'A')
        panel._gender_var = MagicMock()
        panel._gender_var.get.return_value = 'Male'
        panel._set_enroll_status = MagicMock()
        panel.database.student_id_exists.return_value = False
        state['student_key'] = panel._capture_student_key(state)
        capture = FaceCapture.freeze(state['student_key'], sample(3), face())
        state['angle_frames'] = {'front': [capture]}
        with patch('ui.enrollment.threading.Thread'):
            EnrollmentPanel._finish_enroll(panel, state['modal'], state)
        saved = panel.database.insert_student.call_args.kwargs
        self.assertEqual(saved['student_id'], 'NEW')
        self.assertEqual(saved['photo'], capture.photo)
        np.testing.assert_array_equal(pickle.loads(saved['encoding'])[0], capture.embedding)
        panel.recognizer.enrollment_faces.assert_not_called()
        panel._academics.raw.return_value = (*COURSES['cs'], '1st Year', 'A')
        self.assertFalse(panel._capture_current(state))

    def test_confirm_angle_final_review_and_retake_do_not_save_implicitly(self):
        panel, state, captured = self.panel(MagicMock(), 7, 'S7')
        state.update(reviewing=False, step=0, angle_frames={})
        state['session'].frozen = captured
        panel._show_frozen = MagicMock()
        finish = MagicMock()
        panel._wizard_capture(state, finish, 'Save')
        self.assertEqual(state['angle_frames']['front'], [captured])
        self.assertEqual(state['step'], 1)
        panel._wizard_skip(state, finish, 'Save')
        panel._wizard_skip(state, finish, 'Save')
        self.assertTrue(state['reviewing'])
        state['cap_btn'].configure.assert_called_with(text='Save', state='normal')
        state['skip_btn'].configure.assert_called_with(text='Retake', state='normal')
        finish.assert_not_called()
        panel._wizard_skip(state, finish, 'Save')
        self.assertFalse(state['reviewing'])
        self.assertEqual(state['angle_frames'], {})
        finish.assert_not_called()

    def test_multiple_angle_embeddings_each_keep_their_confirmed_frame(self):
        key = ('update', 7, 'S7')
        front = FaceCapture.freeze(key, sample(3), face())
        angled = face()
        angled[1][1] = .2
        left = FaceCapture.freeze(key, sample(4), angled)
        blob, photo = capture_payload([front, left], key)
        embeddings = pickle.loads(blob)
        np.testing.assert_array_equal(embeddings[0], front.embedding)
        np.testing.assert_array_equal(embeddings[1], left.embedding)
        self.assertEqual(photo, front.photo)


class RegistrationSaveTests(unittest.TestCase):
    def registration(self):
        from auth.register import StudentRegistrationWindow
        win = SimpleNamespace(_submitting=False, _alive=True, database=MagicMock(),
                              _cap_lock=__import__('threading').Lock(), _cap=object(),
                              _recognizer=MagicMock(), _set_err=MagicMock(), _cap_btn=MagicMock(),
                              _on_success=MagicMock(), _get_frame=MagicMock())
        for key, value in dict(name='Student', username='student', password='secret123', sid='S7',
                               course='BSIT', year='1A').items():
            entry = MagicMock()
            entry.get.return_value = value
            setattr(win, '_e_'+key, entry)
        from core.academics import academic_values, COURSES
        win._academics = MagicMock()
        win._academics.values.return_value = academic_values(*COURSES['it'], '1', 'A')
        win._save_task = MagicMock()
        win._save_task.run.side_effect = lambda operation, done, error: done(operation())
        win._gender_var = MagicMock()
        win._gender_var.get.return_value = 'Male'
        win._contact_entries = {}
        win.database.student_id_exists.return_value = False
        win.database.username_exists.return_value = False
        win._capture_key = StudentRegistrationWindow._capture_key.__get__(win)
        win._capture_session = CaptureSession(win._capture_key())
        win._capture_session.frozen = FaceCapture.freeze(win._capture_key(), sample(3), face())
        win._captured_frame = win._capture_session.frozen.frame
        return win

    def test_register_saves_frozen_face_without_camera_or_inference(self):
        from auth.register import StudentRegistrationWindow
        win = self.registration()
        StudentRegistrationWindow._submit(win)
        saved = win.database.insert_student.call_args.kwargs
        self.assertEqual(saved['student_id'], 'S7')
        self.assertEqual(saved['photo'], win._capture_session.frozen.photo)
        np.testing.assert_array_equal(pickle.loads(saved['encoding'])[0], win._capture_session.frozen.embedding)
        win._get_frame.assert_not_called()
        win._recognizer.enrollment_faces.assert_not_called()
        win._recognizer.encode_face_multi.assert_not_called()
        StudentRegistrationWindow._submit(win)
        win.database.insert_student.assert_called_once()

    def test_register_rejects_identity_edited_after_preview(self):
        from auth.register import StudentRegistrationWindow
        win = self.registration()
        win._e_sid.get.return_value = 'S8'
        StudentRegistrationWindow._submit(win)
        win.database.insert_student.assert_not_called()


if __name__ == '__main__':
    unittest.main()
