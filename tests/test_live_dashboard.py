"""Dashboard integration checks with fake widgets, real frame/state data, no Tk root."""

from collections import deque
from dataclasses import replace
import queue
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from core.camera import CameraSample
from core.live_pipeline import MonitorTask, MonitorResult
from core.model_readiness import ModelReadiness, ComponentStatus
from core.live_state import FrameContext, LiveAssessment
from ui.dashboard import CBVMSDashboard
from ui.live_alerts import LiveAlertsModel


def sample(sequence=1, at=10.0, source="camera"):
    return CameraSample(np.zeros((240, 320, 3), np.uint8), (source, sequence), at)


def result_for(frame=None, generation=2, camera_generation=3, state="Uniform compliant", cancelled=None):
    frame = frame or sample()
    context = FrameContext(generation, frame.frame_id, frame.captured_at)
    task = MonitorTask(context, frame.frame, 1800000000, cancelled or threading.Event(),
                       camera_generation=camera_generation)
    assessment = LiveAssessment(context, 1, "2:track:1", (50, 40, 90, 90), 0,
        (35, 30, 130, 230), (40, 95, 125, 200), "S1", "Student One", "Male", "Enrolled", "",
        True, True, state, state, "correct_uniform", .95, (), True)
    return MonitorResult(task, (assessment,), frame.captured_at + .1)


def dashboard():
    panel = types.SimpleNamespace(
        _closed=threading.Event(), _monitor_cancel=threading.Event(), _monitor_generation=2,
        _camera_generation=3, _monitor_result=None, _monitor_projections={},
        _monitor_last_offered=None, _monitor_last_rendered=None, _monitor_card_key=None,
        _monitor_render_key=None,
        _monitor_offer_time=0, _monitor_status_text=None,
        _preview_times=deque(maxlen=120), _analysis_times=deque(maxlen=30), _tracking_times=deque(maxlen=30), _metrics_time=0,
        _live_worker=types.SimpleNamespace(requests=queue.Queue(maxsize=1), results=queue.Queue(maxsize=1), offer=MagicMock()),
        _alerts_scroll=MagicMock(), _notification_out=queue.Queue(), _stats_out=queue.Queue(),
        _readiness=ModelReadiness(), _retry_model_btn=MagicMock(), _camera=MagicMock(), _camera_needed=lambda: True,
        _active_nav="live", _checker=types.SimpleNamespace(check_uniform=True, check_earring=True),
        _mirror=types.SimpleNamespace(display_mirror=lambda: False, apply_anim=lambda frame: frame),
        _status_camera=MagicMock(), _status_models=MagicMock(),
        _attendance_writer_status=MagicMock(), _database=MagicMock(),
        _cam_sources={}, _cam_source_var=MagicMock(), _camera_index_setting=0, _status_fps=MagicMock(), camera_feed=MagicMock(),
        _drain_camera_events=MagicMock(), _on_notification=MagicMock(),
        _feed_interval_ms=33, _feed_job=None, after=MagicMock(return_value="feed-job"),
        _last_frame_request=0, _latest_monitor_sample=lambda: sample(),
    )
    panel._readiness._status = {name: ComponentStatus("ready") for name in ("face", "recognition", "uniform")}
    panel._tracking_result = None
    panel._tracking_projections = {}
    panel._tracking_last_offered = None
    panel._tracking_offer_time = 0
    panel._tracking_worker = types.SimpleNamespace(requests=queue.Queue(maxsize=1), results=queue.Queue(maxsize=1),
                                                   offer=MagicMock(), stop=MagicMock(), last_error="", active_task=None)
    panel._live_worker.last_error = ""
    panel._live_worker.active_task = None
    panel.camera_feed.render.return_value = True
    panel.camera_feed.winfo_width.return_value = 640
    panel.camera_feed.winfo_height.return_value = 480
    for name in ("_invalidate_monitor", "_monitor_rows", "_project_rows", "_update_feed", "_clear_alerts", "_halt_camera"):
        setattr(panel, name, getattr(CBVMSDashboard, name).__get__(panel))
    panel._measured_rate = CBVMSDashboard._measured_rate
    return panel


def tick(panel, now=10.1):
    with patch("ui.dashboard.time.monotonic", return_value=now), patch("builtins.print") as output:
        panel._update_feed()
    errors = [args for args in output.call_args_list if "feed error" in str(args)]
    if errors:
        raise AssertionError(errors)


class LiveDashboardTests(unittest.TestCase):
    def test_invalidation_cancels_work_clears_queues_and_retires_cards(self):
        panel = dashboard()
        token = panel._monitor_cancel
        panel._live_worker.requests.put(object())
        panel._live_worker.results.put(object())
        panel._monitor_result = result_for()
        panel._monitor_projections[1] = object()
        panel._preview_times.append(10)
        panel._analysis_times.append(10)
        panel._invalidate_monitor()
        self.assertTrue(token.is_set())
        self.assertFalse(panel._monitor_cancel.is_set())
        self.assertEqual(panel._monitor_generation, 3)
        self.assertIsNone(panel._monitor_result)
        self.assertFalse(panel._monitor_projections)
        self.assertTrue(panel._live_worker.requests.empty())
        self.assertTrue(panel._live_worker.results.empty())
        self.assertFalse(panel._preview_times)
        self.assertFalse(panel._analysis_times)
        panel._alerts_scroll.mark_all_inactive.assert_called_once()

    def test_camera_halt_invalidates_analysis_and_stops_reader(self):
        panel = dashboard()
        token = panel._monitor_cancel
        done = threading.Event()
        panel._stop_camera = MagicMock(return_value=done)
        self.assertIs(panel._halt_camera(), done)
        self.assertEqual(panel._camera_generation, 4)
        self.assertEqual(panel._monitor_generation, 3)
        self.assertTrue(token.is_set())
        panel._stop_camera.assert_called_once()

    def test_navigation_cancels_old_monitor_session_before_showing_next_panel(self):
        panel = dashboard()
        token = panel._monitor_cancel
        panel._views = {"live": MagicMock(), "enrollment": MagicMock()}
        panel._nav_buttons = {}
        panel._center_title = MagicMock()
        panel._enrollment_panel = MagicMock()
        panel._view_host = MagicMock()
        panel._fade_transition = lambda callback: callback()
        CBVMSDashboard._on_nav_select(panel, "enrollment")
        self.assertTrue(token.is_set())
        self.assertEqual(panel._active_nav, "enrollment")
        panel._enrollment_panel.on_show.assert_called_once()
        panel._views["live"].grid_remove.assert_called_once()

    def test_clear_only_changes_display_not_disciplinary_cooldowns(self):
        panel = dashboard()
        panel._processor = types.SimpleNamespace(cooldowns={('S1','wrong_uniform'): 10},
            attendance_cooldowns={'S1': 9}, suspension_cooldowns={'S1': 8}, state=object())
        token = panel._monitor_cancel
        state = panel._processor.state
        panel._clear_alerts()
        panel._alerts_scroll.clear.assert_called_once()
        self.assertEqual(panel._processor.cooldowns, {('S1','wrong_uniform'): 10})
        self.assertEqual(panel._processor.attendance_cooldowns, {'S1': 9})
        self.assertIs(panel._processor.state, state)
        self.assertFalse(token.is_set())

    def test_unique_source_frames_count_once_and_are_never_blocked_by_analysis(self):
        panel = dashboard()
        for sequence in range(1, 5):
            panel._latest_monitor_sample = lambda sequence=sequence: sample(sequence, 10 + sequence / 30)
            tick(panel, 10 + sequence / 30 + .001)
            tick(panel, 10 + sequence / 30 + .002)
        self.assertEqual(panel.camera_feed.render.call_count, 4)
        self.assertEqual(len(panel._preview_times), 4)
        self.assertAlmostEqual(panel._measured_rate(panel._preview_times, 10.2), 30)
        self.assertLessEqual(panel._live_worker.offer.call_count, 1)
        panel.camera_feed.show_placeholder.assert_not_called()

    def test_unrenderable_frame_does_not_inflate_measured_fps(self):
        panel = dashboard()
        panel.camera_feed.render.return_value = False
        tick(panel)
        self.assertEqual(len(panel._preview_times), 0)
        self.assertIsNone(panel._monitor_last_rendered)

    def test_measurement_uses_recent_observations_not_requested_fps(self):
        self.assertEqual(CBVMSDashboard._measured_rate([1, 2], 20), 0)
        self.assertEqual(CBVMSDashboard._measured_rate([10], 10), 0)
        self.assertAlmostEqual(CBVMSDashboard._measured_rate([10, 10.1, 10.2], 10.3), 10)

    def test_model_loading_keeps_preview_live_without_offering_inference(self):
        panel = dashboard()
        panel._readiness._status = {name: ComponentStatus("loading") for name in ("face", "recognition")}
        tick(panel)
        panel.camera_feed.render.assert_called_once()
        panel._live_worker.offer.assert_not_called()
        self.assertIn("Loading recognition", panel._status_models.configure.call_args.kwargs['text'])

    def test_stale_camera_sample_cancels_pending_work_and_shows_reconnect(self):
        panel = dashboard()
        panel._monitor_last_rendered = ('camera', 1)
        token = panel._monitor_cancel
        tick(panel, 12)
        self.assertTrue(token.is_set())
        panel.camera_feed.render.assert_not_called()
        self.assertIn("Reconnecting", panel.camera_feed.show_placeholder.call_args.args[0])

    def test_previous_session_result_is_not_applied_to_new_frame(self):
        panel = dashboard()
        panel._live_worker.results.put(result_for(generation=1))
        tick(panel)
        self.assertIsNone(panel._monitor_result)
        self.assertEqual(len(panel._analysis_times), 0)
        self.assertEqual(panel._alerts_scroll.update_assessments.call_args.args[0], [])

    def test_previous_camera_and_source_results_are_rejected_at_both_ui_gates(self):
        for obsolete in (result_for(camera_generation=2), result_for(frame=sample(source='old-camera'))):
            panel = dashboard()
            panel._live_worker.results.put(obsolete)
            tick(panel)
            self.assertIsNone(panel._monitor_result)
            self.assertEqual(len(panel._analysis_times), 0)
            panel._monitor_result = obsolete
            with patch('ui.dashboard.time.monotonic', return_value=10.2):
                self.assertEqual(panel._monitor_rows(sample()), [])
            self.assertIsNone(panel._monitor_result)

    def test_mirror_and_resize_redraw_cached_frame_without_inflating_fps(self):
        panel = dashboard()
        tick(panel)
        panel._mirror.display_mirror = lambda: True
        tick(panel)
        panel.camera_feed.winfo_width.return_value = 800
        tick(panel)
        self.assertEqual(panel.camera_feed.render.call_count, 3)
        self.assertEqual(len(panel._preview_times), 1)

    def test_expiry_redraws_same_cached_frame_and_retires_compliant_card(self):
        panel = dashboard()
        panel._monitor_result = result_for(sample(at=7.2))
        panel._monitor_projections[1] = types.SimpleNamespace(advance=lambda *args: (50, 40, 90, 90))
        tick(panel, 10.1)
        self.assertEqual(panel._alerts_scroll.update_assessments.call_args.args[0][0]['state'], 'Uniform compliant')
        tick(panel, 10.3)
        self.assertEqual(panel.camera_feed.render.call_count, 2)
        self.assertEqual(panel._alerts_scroll.update_assessments.call_args.args[0], [])
        self.assertEqual(len(panel._preview_times), 1)
        self.assertIsNone(panel._monitor_result)

    def test_failed_open_and_stopped_event_schedule_only_one_retry(self):
        panel = dashboard()
        panel._camera_events = queue.Queue()
        panel._camera_spinner = MagicMock()
        panel._camera_retry_count = 1
        panel._on_camera_opened = CBVMSDashboard._on_camera_opened.__get__(panel)
        panel._camera_events.put(('opened', 3, panel._camera, False))
        panel._camera_events.put(('stopped', 3, panel._camera, False))
        CBVMSDashboard._drain_camera_events(panel)
        panel.after.assert_called_once()
        self.assertEqual(panel.after.call_args.args[0], 2000)

    def test_reconnect_invalidates_status_cache_before_new_active_status(self):
        panel = dashboard()
        tick(panel)
        panel._status_camera.configure.reset_mock()
        panel._invalidate_monitor()
        tick(panel)
        panel._status_camera.configure.assert_called_once()
        self.assertIn('Camera: Active', panel._status_camera.configure.call_args.kwargs['text'])

    def test_analysis_unavailable_is_explicit_and_never_compliant(self):
        panel = dashboard()
        failed = replace(result_for(), assessments=(), detail='Assessment unavailable')
        panel._live_worker.results.put(failed)
        tick(panel)
        status = panel._status_camera.configure.call_args.kwargs['text']
        self.assertIn('Assessment unavailable', status)
        self.assertNotIn('compliant', status)
        self.assertEqual(panel._alerts_scroll.update_assessments.call_args.args[0], [])

    def test_cancelled_notification_does_not_reach_tk(self):
        panel = dashboard()
        panel._notification_out.put(types.SimpleNamespace(valid_if=lambda: False))
        tick(panel)
        panel._on_notification.assert_not_called()
        current = types.SimpleNamespace(valid_if=lambda: True)
        panel._notification_out.put(current)
        tick(panel)
        panel._on_notification.assert_called_once_with(current)

    def test_expired_result_clears_overlay_and_changes_presence_to_historical(self):
        panel = dashboard()
        panel._monitor_result = result_for()
        panel._monitor_projections[1] = MagicMock()
        panel._latest_monitor_sample = lambda: sample(100, 14)
        model = LiveAlertsModel()
        model.update_assessments([dict(track_id=1, presence_id='2:track:1', state='Uniform compliant')])
        panel._alerts_scroll.update_assessments.side_effect = model.update_assessments
        tick(panel, 14.1)
        self.assertIsNone(panel._monitor_result)
        self.assertFalse(panel._monitor_projections)
        self.assertFalse(model.rows['2:track:1'].active)
        self.assertIn("Waiting for analysis", panel._status_camera.configure.call_args.kwargs['text'])

    def test_uncertain_motion_drops_identity_and_uniform_together(self):
        panel = dashboard()
        panel._monitor_result = result_for()
        panel._monitor_projections[1] = types.SimpleNamespace(advance=lambda *args: None)
        with patch('ui.dashboard.time.monotonic', return_value=10.2):
            rows = panel._monitor_rows(sample(2, 10.1))
        self.assertEqual(rows, [])

    def test_projection_moves_face_and_torso_without_mutating_assessment(self):
        panel = dashboard()
        panel._monitor_result = result_for()
        original = panel._monitor_result.assessments[0]
        panel._monitor_projections[1] = types.SimpleNamespace(advance=lambda *args: (60, 45, 100, 95))
        with patch('ui.dashboard.time.monotonic', return_value=10.2):
            rows = panel._monitor_rows(sample(2, 10.1))
        self.assertEqual(rows[0]['face_box'], (60, 45, 100, 95))
        self.assertEqual(rows[0]['torso_box'], (50, 100, 135, 205))
        self.assertEqual(rows[0]['student_id'], 'S1')
        self.assertEqual(rows[0]['observed_at'], panel._monitor_result.task.observed_at)
        self.assertEqual(original.face_box, (50, 40, 90, 90))
        self.assertEqual(original.torso_box, (40, 95, 125, 200))

    def test_shutdown_releases_resources_with_opened_or_unopened_pages(self):
        for opened in (False, True):
            with self.subTest(opened=opened):
                panel = dashboard()
                panel._enrollment_panel = MagicMock() if opened else None
                panel._training_panel = MagicMock() if opened else None
                panel._camera_switch_job = None
                panel._live_worker.stop = MagicMock()
                panel._halt_camera = MagicMock()
                panel.destroy = MagicMock()
                token = panel._monitor_cancel
                CBVMSDashboard._on_close(panel)
                self.assertTrue(panel._closed.is_set())
                self.assertTrue(token.is_set())
                panel._live_worker.stop.assert_called_once()
                panel._halt_camera.assert_called_once()
                panel.camera_feed.cleanup.assert_called_once()
                panel.destroy.assert_called_once()
                if opened:
                    panel._enrollment_panel.on_hide.assert_called_once()
                    panel._training_panel.on_hide.assert_called_once()

    def test_shutdown_tick_does_not_requeue_or_touch_widgets(self):
        panel = dashboard()
        panel._closed.set()
        tick(panel)
        panel.after.assert_not_called()
        panel._drain_camera_events.assert_not_called()
        panel.camera_feed.render.assert_not_called()


if __name__ == '__main__':
    unittest.main()
