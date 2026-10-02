"""Student Management preview scheduling and capture safety without Tk/camera."""
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from core.camera import CameraSample
from core.face_capture import FaceCapture, POSITION_MESSAGE, STALE_MESSAGE, CAPTURE_TIMEOUT, CaptureValidation
from tests import test_face_capture as capture_tests
from ui.enrollment import EnrollmentPanel


def camera_sample(sequence=1, at=10.0, shift=0, source="camera"):
    frame = np.zeros((480, 640, 3), np.uint8)
    rng = np.random.default_rng(4)
    frame[120:350, 240 + shift:390 + shift] = rng.integers(
        0, 255, (230, 150, 3), dtype=np.uint8)
    frame[10:30, 10:30] = sequence
    return CameraSample(frame, (source, sequence), at)


def detection():
    embedding = np.zeros(512, np.float32)
    embedding[0] = 1
    return ([240, 120, 390, 350], embedding, .9, None)


def fake_panel():
    panel, state, _ = capture_tests.SaveFlowTests().panel(MagicMock(), 7, "S7")
    panel._init_wizard_preview = EnrollmentPanel._init_wizard_preview
    panel._wizard_feedback = EnrollmentPanel._wizard_feedback
    panel._start_wizard_validation = EnrollmentPanel._start_wizard_validation.__get__(panel)
    panel._draw_pose_guide = EnrollmentPanel._draw_pose_guide
    panel._capture_inference_lock = threading.Lock()
    panel._render_capture = MagicMock()
    panel.get_frame_sample = lambda: None
    state.update(reviewing=False, step=0, angle_frames={}, results=queue.Queue(),
                 worker_busy=False, mirror=MagicMock())
    state["mirror"].display_mirror.return_value = False
    panel._init_wizard_preview(state)
    state["preview_session"] = state["session"]
    return panel, state


class EnrollmentPreviewTests(unittest.TestCase):
    def tick(self, panel, state, sample, now=None):
        panel.get_frame_sample = lambda: sample
        now = sample.captured_at + .01 if now is None else now
        with patch("ui.enrollment.time.monotonic", return_value=now), \
                patch("ui.enrollment.threading.Thread"):
            panel._wizard_tick(state)

    def test_valid_detections_reach_ready_without_optical_flow_points(self):
        panel, state = fake_panel()
        state['worker_busy'] = True
        with patch('core.face_capture.cv2.goodFeaturesToTrack', return_value=None):
            for seq in range(1, 4):
                current = camera_sample(seq, at=10 + seq*.3)
                state['results'].put((state['session'], current, [detection()], None))
                self.tick(panel, state, current)
        self.assertTrue(state['session'].ready)
        self.assertEqual(state['cap_btn'].configure.call_args.kwargs['state'], 'normal')

    def test_flow_initialization_failure_does_not_cancel_fresh_capture(self):
        panel, state = fake_panel()
        session = state['session']
        session.observe(camera_sample(1,at=10),[detection()],0,10.01)
        session.observe(camera_sample(2,at=10.3),[detection()],0,10.31)
        self.assertTrue(session.request(10.32))
        state['requested_at'] = 10.32
        fresh = camera_sample(3,at=10.4)
        state['results'].put((session,fresh,[detection()],None))
        panel._show_frozen = MagicMock()
        with patch('core.face_capture.cv2.goodFeaturesToTrack',return_value=None):
            self.tick(panel,state,fresh)
        self.assertIsNotNone(session.frozen)
        self.assertEqual(session.frozen.frame_id,fresh.frame_id)

    def test_busy_validation_does_not_gate_unique_frame_preview(self):
        panel, state = fake_panel()
        state["worker_busy"] = True
        for sequence in range(12):
            sample = camera_sample(sequence, at=10 + sequence / 30)
            self.tick(panel, state, sample)
            self.assertIs(panel._render_capture.call_args.args[1], sample.frame)
        self.assertEqual(panel._render_capture.call_count, 12)
        self.assertEqual(state["det_status"].configure.call_count, 1)
        self.assertEqual(state["cap_btn"].configure.call_count, 1)
        self.assertEqual(state["modal"].after.call_count, 12)

    def test_cached_frame_is_not_rendered_repeatedly(self):
        panel, state = fake_panel()
        state["worker_busy"] = True
        sample = camera_sample()
        for _ in range(6):
            self.tick(panel, state, sample)
        panel._render_capture.assert_called_once()

    def test_mirror_toggle_redraws_cached_frame_without_counting_another_source_frame(self):
        panel, state = fake_panel()
        state["worker_busy"] = True
        sample = camera_sample()
        self.tick(panel, state, sample)
        state["mirror"].display_mirror.return_value = True
        self.tick(panel, state, sample)
        self.assertEqual(panel._render_capture.call_count, 2)
        self.assertEqual(len(state["preview_metrics"].render_times), 1)

    def test_render_duration_is_subtracted_from_next_preview_delay(self):
        panel, state = fake_panel()
        state["worker_busy"] = True
        panel.get_frame_sample = lambda: camera_sample()
        with patch("ui.enrollment.time.monotonic", side_effect=[10, 10, 10.02, 10.02]):
            panel._wizard_tick(state)
        self.assertEqual(state["modal"].after.call_args.args[0], 13)

    def test_full_validation_is_throttled_and_only_one_worker_is_inflight(self):
        panel, state = fake_panel()
        with patch("ui.enrollment.threading.Thread") as worker:
            for sequence in range(5):
                sample = camera_sample(sequence, at=10 + sequence * .01)
                panel.get_frame_sample = lambda: sample
                with patch("ui.enrollment.time.monotonic", return_value=sample.captured_at):
                    panel._wizard_tick(state)
            worker.assert_called_once()
            # Completion before the validation interval does not dispatch again.
            state["results"].put((state["session"], camera_sample(0), [], None))
            with patch("ui.enrollment.time.monotonic", return_value=10.10):
                panel._wizard_tick(state)
            worker.assert_called_once()
        self.assertEqual(panel._render_capture.call_count, 5)

    def test_fast_validation_stays_at_four_per_second_while_preview_runs_at_thirty(self):
        panel, state = fake_panel()
        validation_times = []

        def detect(frame):
            validation_times.append(time.monotonic())
            return [detection()]

        panel.recognizer.enrollment_faces.side_effect = detect
        # Execute each worker immediately: the inference rate must still be bounded.
        with patch("ui.enrollment.threading.Thread") as worker:
            worker.return_value.start.side_effect = lambda: worker.call_args.kwargs["target"]()
            for sequence in range(30):
                sample = camera_sample(sequence, at=10 + sequence / 30)
                panel.get_frame_sample = lambda: sample
                with patch("ui.enrollment.time.monotonic", return_value=sample.captured_at):
                    panel._wizard_tick(state)
        self.assertEqual(len(validation_times), 4)
        self.assertTrue(all(interval >= .25 for interval in np.diff(validation_times)))
        self.assertEqual(panel._render_capture.call_count, 30)

    def test_result_tracks_highlight_into_current_frame_without_rewinding(self):
        panel, state = fake_panel()
        analyzed = camera_sample(1)
        latest = camera_sample(7, at=10.2, shift=8)
        state["worker_busy"] = True
        state["results"].put((state["session"], analyzed, [detection()], None))
        self.tick(panel, state, latest)
        self.assertIs(panel._render_capture.call_args.args[1], latest.frame)
        box = panel._render_capture.call_args.args[2]
        self.assertIsNotNone(box)
        np.testing.assert_allclose(box, np.array(detection()[0]) + [8, 0, 8, 0], atol=2)

    def test_stale_result_does_not_supply_current_highlight_or_capture(self):
        panel, state = fake_panel()
        state["results"].put((state["session"], camera_sample(1, at=8), [detection()], None))
        latest = camera_sample(20, at=10)
        self.tick(panel, state, latest)
        self.assertIs(panel._render_capture.call_args.args[1], latest.frame)
        self.assertIsNone(panel._render_capture.call_args.args[2])
        self.assertIsNone(state["session"].previous)
        self.assertIsNone(state["session"].frozen)

    def test_missing_or_stale_camera_clears_preview_and_cancels_capture(self):
        for sample in (None, camera_sample(3, at=8)):
            with self.subTest(sample=sample is None):
                panel, state = fake_panel()
                state["worker_busy"] = True
                self.tick(panel, state, camera_sample(0, at=9.7))
                panel._render_capture.reset_mock()
                session = state["session"]
                session.observe(camera_sample(1, at=9.8), [detection()], 0, 9.81)
                session.observe(camera_sample(2, at=9.9), [detection()], 0, 9.91)
                self.assertTrue(session.request(9.92))
                state["worker_busy"] = True
                self.tick(panel, state, sample, now=10)
                panel._render_capture.assert_not_called()
                state["canvas"].delete.assert_called_with("all")
                self.assertFalse(session.pending)
                self.assertFalse(session.ready)
                self.assertEqual(session.message, STALE_MESSAGE)

    def test_movement_loss_cancels_pending_capture_even_while_worker_busy(self):
        panel, state = fake_panel()
        first, second = camera_sample(1), camera_sample(2, at=10.1)
        session = state["session"]
        session.observe(first, [detection()], 0, 10.01)
        session.observe(second, [detection()], 0, 10.11)
        self.assertTrue(session.request(10.12))
        state["preview_tracker"].reset(second, detection()[0])
        state["worker_busy"] = True
        gone = CameraSample(np.zeros_like(second.frame), ("camera", 3), 10.2)
        self.tick(panel, state, gone)
        self.assertFalse(session.pending)
        self.assertFalse(session.ready)
        self.assertIsNone(session.frozen)
        self.assertIsNone(panel._render_capture.call_args.args[2])

    def test_target_leaving_while_capture_result_arrives_still_cancels_pending_capture(self):
        panel, state = fake_panel()
        first, second = camera_sample(1), camera_sample(2, at=10.1)
        session = state["session"]
        session.observe(first, [detection()], 0, 10.01)
        session.observe(second, [detection()], 0, 10.11)
        self.assertTrue(session.request(10.12))
        state["requested_at"] = 10.12
        state["preview_tracker"].reset(second, detection()[0])
        state["worker_busy"] = True
        # Inference captured the target, but they left before its callback arrived.
        state["results"].put((session, camera_sample(3, at=10.2), [detection()], None))
        gone = CameraSample(np.zeros_like(second.frame), ("camera", 4), 10.3)
        panel._show_frozen = MagicMock()
        self.tick(panel, state, gone)
        self.assertFalse(session.pending)
        self.assertFalse(session.ready)
        self.assertIsNone(session.frozen)
        panel._show_frozen.assert_not_called()

    def test_delayed_capture_keeps_validated_frame_when_current_target_is_still_tracked(self):
        panel, state = fake_panel()
        first, second = camera_sample(1), camera_sample(2, at=10.1)
        session = state["session"]
        session.observe(first, [detection()], 0, 10.01)
        session.observe(second, [detection()], 0, 10.11)
        self.assertTrue(session.request(10.12))
        state["requested_at"] = 10.12
        state["preview_tracker"].reset(second, detection()[0])
        captured = camera_sample(3, at=10.2)
        state["results"].put((session, captured, [detection()], None))
        panel._show_frozen = MagicMock()
        self.tick(panel, state, camera_sample(4, at=10.3, shift=8))
        self.assertEqual(session.frozen.frame_id, captured.frame_id)
        self.assertEqual(session.frozen.student_key, state["student_key"])
        np.testing.assert_array_equal(session.frozen.frame, captured.frame)
        np.testing.assert_array_equal(session.frozen.embedding, detection()[1])
        panel._show_frozen.assert_called_once_with(state, session.frozen)
        panel._render_capture.assert_not_called()

    def test_retake_ignores_old_session_but_resumes_current_preview(self):
        panel, state = fake_panel()
        old = state["session"]
        old.frozen = FaceCapture.freeze(state["student_key"], camera_sample(), detection())
        panel._wizard_retake(state)
        state["results"].put((old, camera_sample(2, at=10.1), [detection()], None))
        latest = camera_sample(3, at=10.2)
        self.tick(panel, state, latest)
        self.assertIsNot(state["session"], old)
        self.assertIsNone(state["session"].previous)
        self.assertIsNone(state["session"].frozen)
        self.assertIs(panel._render_capture.call_args.args[1], latest.frame)
        self.assertIsNone(panel._render_capture.call_args.args[2])

    def test_retake_resets_live_metrics_so_review_pause_is_not_counted_as_low_fps(self):
        panel, state = fake_panel()
        state["worker_busy"] = True
        for sequence in range(4):
            self.tick(panel, state, camera_sample(sequence, at=10 + sequence / 30))
        self.assertEqual(len(state["preview_metrics"].render_times), 4)
        old_metrics = state["preview_metrics"]
        state["session"].frozen = FaceCapture.freeze(
            state["student_key"], camera_sample(4, at=10.2), detection())
        panel._wizard_retake(state)
        self.tick(panel, state, camera_sample(100, at=30))
        self.assertIsNot(state["preview_metrics"], old_metrics)
        self.assertEqual(len(state["preview_metrics"].render_times), 1)
        self.assertEqual(list(state["source_times"]), [30])
        self.assertEqual(state["last_source_sample"], ("camera", 100))
        self.assertGreater(state["next_diagnostics"], 30)

    def test_camera_source_change_rejects_old_validation(self):
        panel, state = fake_panel()
        first, second = camera_sample(1), camera_sample(2, at=10.1)
        session = state["session"]
        session.observe(first, [detection()], 0, 10.01)
        session.observe(second, [detection()], 0, 10.11)
        self.assertTrue(session.request(10.12))
        state["preview_tracker"].reset(second, detection()[0])
        state["results"].put((session, camera_sample(3, at=10.2), [detection()], None))
        current = camera_sample(1, at=10.3, source="replacement")
        self.tick(panel, state, current)
        self.assertFalse(session.pending)
        self.assertIsNone(session.frozen)
        self.assertIs(panel._render_capture.call_args.args[1], current.frame)
        self.assertIsNone(panel._render_capture.call_args.args[2])

    def test_closed_or_destroyed_panel_never_reschedules(self):
        for destroyed in (False, True):
            panel, state = fake_panel()
            state["alive"] = destroyed
            state["modal"].winfo_exists.return_value = not destroyed
            self.tick(panel, state, camera_sample())
            panel._render_capture.assert_not_called()
            state["modal"].after.assert_not_called()

    def test_student_switch_stops_preview_and_disables_save(self):
        panel, state = fake_panel()
        panel._selected_pk = 8
        self.tick(panel, state, camera_sample())
        panel._render_capture.assert_not_called()
        state["modal"].after.assert_not_called()
        state["cap_btn"].configure.assert_called_with(state="disabled")
        self.assertEqual(state["angle_frames"], {})

    def test_frozen_preview_remains_frozen_without_new_validation(self):
        panel, state = fake_panel()
        captured = FaceCapture.freeze(state["student_key"], camera_sample(), detection())
        state["session"].frozen = captured
        with patch("ui.enrollment.threading.Thread") as worker:
            for sequence in range(2, 6):
                panel.get_frame_sample = lambda: camera_sample(sequence, at=10.2)
                panel._wizard_tick(state)
        worker.assert_not_called()
        panel._render_capture.assert_not_called()
        self.assertIs(state["session"].frozen, captured)

    def test_current_pixels_and_tracked_highlight_mirror_and_resize_together(self):
        rendered = []
        latest = camera_sample(7, at=10.2, shift=8)
        for mirrored in (False, True):
            panel, state = fake_panel()
            panel._render_capture = EnrollmentPanel._render_capture.__get__(panel)
            state["mirror"].display_mirror.return_value = mirrored
            state["results"].put((state["session"], camera_sample(1), [detection()], None))
            with patch("ui.enrollment.ImageTk.PhotoImage", side_effect=lambda image, **kw:
                       rendered.append(np.array(image)) or MagicMock()):
                self.tick(panel, state, latest)
        self.assertEqual(rendered[0].shape[:2], (240, 320))
        np.testing.assert_array_equal(rendered[1], rendered[0][:, ::-1])
        self.assertEqual(int(rendered[0][8, 8, 0]), 7)
        # x=(240+8)/2: the box follows the current person's eight-pixel movement.
        np.testing.assert_array_equal(rendered[0][100, 124], [40, 220, 90])


class EnrollmentWorkerTests(unittest.TestCase):
    def test_obsolete_session_is_rejected_before_worker_inference(self):
        panel, state = fake_panel()
        old_session = state["session"]
        with patch("ui.enrollment.threading.Thread") as worker:
            panel._start_wizard_validation(state, old_session, camera_sample())
        run = worker.call_args.kwargs["target"]
        panel._wizard_retake(state)
        run()
        owner, _, faces, _ = state["results"].get_nowait()
        self.assertIs(owner, old_session)
        self.assertEqual(faces, [])
        panel.recognizer.enrollment_faces.assert_not_called()
        self.assertFalse(panel._capture_inference_lock.locked())

    def test_thread_start_failure_clears_busy_and_releases_inference_lock(self):
        panel, state = fake_panel()
        with patch("ui.enrollment.threading.Thread") as worker:
            worker.return_value.start.side_effect = RuntimeError("cannot create worker")
            panel._start_wizard_validation(state, state["session"], camera_sample())
            self.assertIn("cannot create worker",state["session"].message)
        self.assertFalse(state["worker_busy"])
        self.assertFalse(panel._capture_inference_lock.locked())

    def test_reopening_during_inference_does_not_create_concurrent_work(self):
        panel, state = fake_panel()
        entered, release = threading.Event(), threading.Event()

        def detect(frame):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test worker was not released")
            return [detection()]

        panel.recognizer.enrollment_faces.side_effect = detect
        panel._start_wizard_validation(state, state["session"], camera_sample())
        try:
            self.assertTrue(entered.wait(2))
            state["alive"] = False
            _, reopened = fake_panel()
            for _ in range(5):
                panel._start_wizard_validation(reopened, reopened["session"], camera_sample(2))
            self.assertEqual(panel.recognizer.enrollment_faces.call_count, 1)
            self.assertTrue(panel._capture_inference_lock.locked())
        finally:
            release.set()
        state["results"].get(timeout=2)
        self.assertTrue(panel._capture_inference_lock.acquire(timeout=2))
        panel._capture_inference_lock.release()
        panel._start_wizard_validation(reopened, reopened["session"], camera_sample(3))
        reopened["results"].get(timeout=2)
        self.assertEqual(panel.recognizer.enrollment_faces.call_count, 2)

    def test_worker_error_releases_lock_and_delivers_error_to_current_session(self):
        panel, state = fake_panel()
        panel.recognizer.enrollment_faces.side_effect = RuntimeError("model failed")
        sample = camera_sample(at=time.monotonic())
        panel._start_wizard_validation(state, state["session"], sample)
        owner, captured, faces, error = state["results"].get(timeout=2)
        self.assertIs(owner, state["session"])
        self.assertEqual(captured.frame_id,sample.frame_id)
        np.testing.assert_array_equal(captured.frame,sample.frame)
        self.assertFalse(captured.frame.flags.writeable)
        self.assertEqual(faces, [])
        self.assertIn("model failed", error)
        self.assertTrue(panel._capture_inference_lock.acquire(timeout=2))
        panel._capture_inference_lock.release()


if __name__ == "__main__":
    unittest.main()
