import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from ui.monitor_workspace import MonitorWorkspace


def workspace():
    gate = dict(type='ip', id='gate', label='Front Gate')
    usb = dict(type='usb', index=0, id='usb_0', label='MacBook')
    p = SimpleNamespace(_closed=False, _job=None, session=None, _popup=None,
        _popup_feed=None, _last={}, _failed_key=None, _choice_sources={},
        selector=MagicMock(), tabs=MagicMock(), primary_label=MagicMock(),
        secondary_label=MagicMock(), secondary_feed=MagicMock(), after=MagicMock(),
        close_popup=MagicMock(), _tick=MagicMock())
    p.selector.get.return_value = 'Front Gate'
    p.tabs.get.return_value = 'Split'
    p.dashboard = SimpleNamespace(_active_nav='live', _closed=threading.Event(),
        _camera_requested_source=usb, _cam_sources={'MacBook':usb, 'Front Gate':gate},
        _camera_state='STREAMING', _observation_only=True)
    return p


class WorkspaceTests(unittest.TestCase):
    @patch('ui.monitor_workspace.PreviewSession')
    def test_split_starts_only_other_camera(self, factory):
        p = workspace()
        factory.return_value.sample.return_value = None
        factory.return_value.done.is_set.return_value = False
        factory.return_value.key = ('ip', 'gate')
        MonitorWorkspace._tick(p)
        MonitorWorkspace._tick(p)
        factory.assert_called_once_with(p.dashboard._cam_sources['Front Gate'])
        factory.return_value.start.assert_called_once()

    @patch('ui.monitor_workspace.PreviewSession')
    def test_focus_does_not_open_another_camera(self, factory):
        p = workspace()
        p.tabs.get.return_value = 'Focus'
        MonitorWorkspace._tick(p)
        factory.assert_not_called()

    @patch('ui.monitor_workspace.PreviewSession')
    def test_switching_does_not_open_companion(self, factory):
        p = workspace()
        p.dashboard._camera_state = 'CONNECTING'
        MonitorWorkspace._tick(p)
        factory.assert_not_called()

    def test_navigation_stops_preview_and_closes_popup(self):
        p = workspace()
        p.dashboard._active_nav = 'settings'
        p._popup = MagicMock()
        p.session = MagicMock()
        p.session.done.is_set.return_value = False
        MonitorWorkspace._tick(p)
        p.session.stop.assert_called_once()
        p.session.sample.assert_not_called()
        p.close_popup.assert_called_once()

    @patch('ui.monitor_workspace.PreviewSession')
    def test_detached_view_reuses_existing_session(self, factory):
        p = workspace()
        p.tabs.get.return_value = 'Focus'
        p._popup = MagicMock()
        p._popup_feed = MagicMock()
        p.session = MagicMock(key=('ip', 'gate'))
        p.session.done.is_set.return_value = False
        p.session.sample.return_value = None
        MonitorWorkspace._tick(p)
        factory.assert_not_called()
        p.session.sample.assert_called_once()

    def test_old_frame_not_shown_after_source_change(self):
        p = workspace()
        p.dashboard._camera_state = 'CONNECTING'
        p.session = MagicMock(key=('ip', 'old'))
        p.session.done.is_set.return_value = False
        MonitorWorkspace._tick(p)
        p.session.stop.assert_called_once()
        p.secondary_feed.render.assert_not_called()

if __name__ == '__main__':
    unittest.main()
