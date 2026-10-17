"""Source activation must follow usable pixels, never menu selection."""
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from ui.dashboard import CBVMSDashboard
from api import camera_store


class ActivationTests(unittest.TestCase):
    def test_menu_commit_does_not_persist_unverified_source_or_create_toast(self):
        panel = types.SimpleNamespace(_camera_switch_job='queued',
                                      _on_camera_source_connected=MagicMock())
        source = dict(id='ip_gate', type='rj45', label='front gate', url='rtsp://host/live')
        with patch.object(camera_store, 'save_camera_preference') as save, \
             patch('ui.dashboard.show_toast') as toast:
            CBVMSDashboard._commit_camera_switch(panel, 'front gate', source)
        save.assert_not_called()
        toast.assert_not_called()
        panel._on_camera_source_connected.assert_called_once_with(source)

    def test_saved_keychain_source_is_resolved_only_when_requested(self):
        source = dict(id='ip_gate', type='rj45', url='rtsp://host/live')
        entry = dict(id='gate', label='front gate', url=source['url'], secret_ref='test-ref')
        with patch.object(camera_store, 'get_saved_ip_cameras', return_value=[entry]), \
             patch('core.camera_secrets.get_secret', return_value='rtsp://test:secret@host/live') as secret:
            resolved = camera_store.resolve_camera_source(source, cancel_event=threading.Event())
        self.assertEqual(resolved['url'], 'rtsp://test:secret@host/live')
        self.assertEqual(source['url'], 'rtsp://host/live')
        secret.assert_called_once()


class LifecycleTests(unittest.TestCase):
    def panel(self):
        from tests.test_camera_switching import CameraHarness
        panel = CameraHarness(_camera_generation=1, _camera_events=__import__('queue').Queue(),
            _camera_resolution_setting=(1280, 720), _fps_cap_setting=30,
            _invalidate_monitor=MagicMock(), _camera_needed=lambda: True,
            after=MagicMock(return_value='scheduled'), _camera=MagicMock())
        for name in ('_on_camera_opened', '_retry_camera', '_deferred_start_camera', '_stop_camera', '_halt_camera'):
            setattr(panel, name, types.MethodType(getattr(CBVMSDashboard, name), panel))
        return panel

    def test_preferences_only_follow_a_current_usable_frame(self):
        panel = self.panel()
        cap = panel._camera
        with patch.object(camera_store, 'save_camera_preference') as save:
            cap.get_latest_sample.return_value = None
            panel._on_camera_opened(cap, True, 1)
            save.assert_not_called()
            self.assertEqual(panel._camera_state, 'ERROR')
            cap.get_latest_sample.return_value = object()
            panel._on_camera_opened(cap, True, 0)
            save.assert_not_called()
            panel._on_camera_opened(cap, True, 1)
            save.assert_called_once_with(panel._camera_requested_source)
            self.assertEqual(panel._camera_state, 'STREAMING')

    def test_established_stream_has_three_retries_and_switch_cancels_timer(self):
        panel = self.panel()
        panel._camera_established = True
        for delay in (1000, 2000, 4000):
            panel._schedule_camera_reconnect(1)
            self.assertEqual(panel.after.call_args.args[0], delay)
        panel.after.reset_mock()
        panel._schedule_camera_reconnect(1)
        panel.after.assert_not_called()
        self.assertEqual(panel._camera_state, 'ERROR')
        panel._halt_camera()
        self.assertIsNone(panel._camera_retry_job)
        self.assertEqual(panel._camera_state, 'CANCELLED')

    def test_stalled_owner_times_out_without_duplicate_capture(self):
        panel = self.panel()
        panel._start_camera_worker = MagicMock()
        with patch('ui.dashboard.time.monotonic', return_value=1):
            panel._deferred_start_camera()
        poll = panel.after.call_args.args[1]
        with patch('ui.dashboard.time.monotonic', return_value=5):
            poll()
        panel._start_camera_worker.assert_not_called()
        self.assertEqual(panel._camera_state, 'ERROR')
        self.assertIn('still stopping', panel._camera_message)

    def test_open_deadline_rejects_late_capture_and_does_not_release_on_tk(self):
        panel = self.panel()
        cap = panel._camera
        panel._camera_reader_stop = threading.Event()
        cancelled = panel._camera_reader_stop
        panel._camera_open_deadline = 1
        panel._camera_state = 'WAITING_FOR_FIRST_FRAME'
        panel._camera_events.put(('opened', 1, cap, True))
        with patch('ui.dashboard.time.monotonic', return_value=2), patch.object(camera_store, 'save_camera_preference') as save:
            CBVMSDashboard._drain_camera_events(panel)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(panel._camera_state, 'ERROR')
        cap.release.assert_not_called()
        save.assert_not_called()

    def test_network_owner_does_not_wait_for_usb_scan_lock(self):
        import time
        from core.camera import CAMERA_DEVICE_LOCK
        panel = self.panel()
        panel._camera_requested_source = dict(id='ip_gate',type='rj45',label='Gate',url='rtsp://host/live')
        cap = MagicMock()
        cap.open.return_value = False
        with CAMERA_DEVICE_LOCK, patch('ui.dashboard.CameraCapture', return_value=cap), \
             patch.object(camera_store, 'resolve_camera_source', return_value=panel._camera_requested_source):
            started = time.monotonic()
            CBVMSDashboard._start_camera_worker(panel, 1)
            self.assertTrue(panel._camera_worker_done.wait(.5))
            self.assertLess(time.monotonic()-started, .5)
        cap.open.assert_called_once()
        cap.release.assert_called_once()

    def test_rapid_return_to_current_source_cancels_queued_other_source(self):
        from tests.test_camera_switching import DashboardSwitchRegressionTests
        panel = DashboardSwitchRegressionTests._switch_harness()
        CBVMSDashboard._on_switch_camera(panel, 'USB Camera 1')
        CBVMSDashboard._on_switch_camera(panel, 'USB Camera 0')
        self.assertEqual(panel.cancelled, ['job-1'])
        self.assertEqual(len(panel.scheduled), 2)
        panel.scheduled[-1][1]()
        self.assertEqual(panel.connected[-1]['index'], 0)


class SecretAndScanTests(unittest.TestCase):
    def test_keychain_secret_stays_out_of_api_metadata_and_preference(self):
        from api.camera_manager import CameraManager
        secret = 'rtsp://operator:sentinel-private-value@host/live'
        public = dict(id='ip_gate',type='rj45',label='Gate',url='rtsp://host/live')
        with patch.object(camera_store, 'resolve_camera_source', return_value=dict(public,url=secret)), \
             patch('api.camera_manager.CameraCapture') as capture, \
             patch('api.camera_manager.save_camera_preference') as save:
            capture.return_value.open.return_value = True
            manager = CameraManager()
            result = manager.select(public)
        self.assertNotIn('sentinel-private-value', str(result))
        self.assertNotIn('sentinel-private-value', str(manager.active_camera))
        self.assertNotIn('sentinel-private-value', str(save.call_args))
        capture.assert_called_once_with(source_url=secret)

    def test_preference_file_excludes_resolved_url(self):
        with patch.object(camera_store, '_write_json') as write:
            camera_store.save_camera_preference(dict(id='ip_gate',type='rj45',label='Gate',
                url='rtsp://u:sentinel@host/live',secret_ref='reference'))
        self.assertEqual(write.call_args.args[1],dict(id='ip_gate',type='rj45',label='Gate'))

    def test_scan_keeps_unavailable_credential_entry_and_later_cameras(self):
        import queue
        from ui.camera_configuration import CameraConfigurationSection
        panel = types.SimpleNamespace(_scanning=False, _scan_cancel=threading.Event(),
            _scan_results=queue.Queue(), _scan_btn=MagicMock(), after=MagicMock(), _poll_scan_results=MagicMock())
        cameras = [dict(id=str(i),label=str(i),url='rtsp://host/live') for i in range(2)]
        with patch('ui.camera_configuration.scan_usb_cameras', return_value=[]), \
             patch('ui.camera_configuration.get_saved_ip_cameras', return_value=cameras), \
             patch('ui.camera_configuration.resolve_camera_source', side_effect=[RuntimeError('locked'),cameras[1]]), \
             patch('ui.camera_configuration.test_ip_camera', return_value=True):
            CameraConfigurationSection._run_scan(panel)
            rows, error, cancelled = panel._scan_results.get(timeout=1)
        self.assertIsNone(error)
        self.assertFalse(cancelled)
        self.assertEqual([r['status'] for r in rows], ['unreachable','available'])


if __name__ == '__main__':
    unittest.main()
