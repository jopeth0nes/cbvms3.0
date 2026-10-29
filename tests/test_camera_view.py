"""Presentation contracts; no camera, UI or production persistence."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from ui.camera_view_dialog import validated_selection
from ui.monitor_workspace import MonitorWorkspace
from api import camera_store

USB = ('usb', 0)
IP = ('ip', 'gate')
SOURCES = {USB: dict(type='usb', index=0, label='MacBook'), IP: dict(type='ip', id='gate', label='Front Gate')}


class ViewSelectionTests(unittest.TestCase):
    def test_checkbox_tcl_boolean_values(self):
        import tkinter as tk
        from ui.camera_view_dialog import CameraViewDialog
        interp = tk.Tcl()
        for value, expected in [('0', []), ('1', [USB]), (0, []), (1, [USB])]:
            with self.subTest(value=value):
                interp.setvar('selected', value)
                dialog = SimpleNamespace(_vars={USB:'selected'}, tk=interp.tk, getvar=interp.getvar)
                self.assertEqual(CameraViewDialog.selection(dialog), expected)

    def test_missing_or_malformed_preference_falls_back(self):
        for preference in (None, {'sources': [['ip', 'deleted']]}, {'sources': 'bad'}, {'sources': [['usb', []]]}):
            self.assertEqual(validated_selection(preference, SOURCES, USB), ([USB], 'Live Monitor'))

    def test_remembered_selection_uses_stable_keys(self):
        self.assertEqual(validated_selection({'sources': [list(IP), list(IP), list(USB)],
            'destination': 'Separate window'}, SOURCES, USB), ([IP, USB], 'Separate window'))

    def test_preferences_separate_from_analysis_and_contain_no_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'view.json'
            with patch.object(camera_store, '_VIEW_PREFERENCE_PATH', path), patch.object(camera_store, '_ensure_data_dir'):
                camera_store.save_camera_view_preference([USB, IP, ('ip', 'rtsp://secret')], 'Separate window')
                self.assertEqual(camera_store.get_camera_view_preference(),
                    {'sources': [list(USB), list(IP)], 'destination': 'Separate window'})

    def test_accept_does_not_change_primary_or_start_capture(self):
        p = SimpleNamespace(_closed=False, sources=lambda: SOURCES, _last={}, pop_out=MagicMock())
        with patch('api.camera_store.save_camera_view_preference') as save:
            self.assertTrue(MonitorWorkspace.accept_view(p, [IP], 'Live Monitor'))
            self.assertEqual(p._main_keys, (IP,))
            p.pop_out.assert_not_called()
            self.assertTrue(MonitorWorkspace.accept_view(p, [USB, IP], 'Separate window'))
            p.pop_out.assert_called_once_with([USB, IP])
            self.assertFalse(MonitorWorkspace.accept_view(p, [('ip', 'removed')], 'Live Monitor'))
            self.assertEqual(save.call_count, 2)

    def test_analysis_label_requires_fresh_primary(self):
        d = SimpleNamespace(_camera_requested_source=SOURCES[USB], _camera_state='STREAMING',
                            _primary_video_fresh=False, _observation_only=True)
        p = SimpleNamespace(sources=lambda:SOURCES, dashboard=d, session=None, _failed_key=None)
        self.assertIn('Connecting', MonitorWorkspace.source_status(p, USB))
        d._primary_video_fresh=True
        self.assertIn('Analysis active', MonitorWorkspace.source_status(p, USB))
        self.assertNotIn('Analysis', MonitorWorkspace.source_status(p, IP))
        d._camera_state='ERROR'
        self.assertIn('Offline', MonitorWorkspace.source_status(p, USB))

    def test_fanout_uses_same_pixels_no_inference(self):
        feeds = [MagicMock(), MagicMock()]
        pixels = object()
        p = SimpleNamespace(primary_targets=lambda:feeds)
        self.assertTrue(MonitorWorkspace.render_primary(p, pixels))
        for feed in feeds:
            feed.render.assert_called_once_with(pixels)
        p.primary_targets=lambda:[]
        self.assertFalse(MonitorWorkspace.render_primary(p, pixels))

if __name__ == '__main__':
    unittest.main()
