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
        close_popup=MagicMock(), after_cancel=MagicMock(), _tick=MagicMock())
    p.selector.get.return_value = 'Front Gate'
    p.tabs.get.return_value = 'Split'
    p.dashboard = SimpleNamespace(_active_nav='live', _closed=threading.Event(),
        _camera_requested_source=usb, _cam_sources={'MacBook':usb, 'Front Gate':gate},
        _camera_state='STREAMING', _observation_only=True)
    return p


class WorkspaceTests(unittest.TestCase):
    def test_stale_popup_callbacks_cannot_affect_replacement(self):
        p = workspace()
        old = MagicMock()
        current = p._popup = MagicMock()
        MonitorWorkspace._close_popup_instance(p, old)
        MonitorWorkspace._fullscreen_popup_instance(p, old, True)
        MonitorWorkspace._popup_destroyed(p, SimpleNamespace(widget=old))
        p.close_popup.assert_not_called()
        current.attributes.assert_not_called()
        self.assertIs(p._popup, current)

    def test_minimized_focus_popup_does_not_copy_or_render_frames(self):
        p = workspace()
        p.tabs.get.return_value = 'Focus'
        p._popup = MagicMock()
        p._popup.state.return_value = 'iconic'
        p._popup_feed = MagicMock()
        p.session = MagicMock(key=('ip', 'gate'))
        p.session.done.is_set.return_value = False
        MonitorWorkspace._tick(p)
        p.session.sample.assert_not_called()
        p.session.stop.assert_not_called()
        p._popup_feed.render.assert_not_called()

    def test_unmapped_tile_does_not_render(self):
        p = workspace()
        p.secondary_feed.winfo_ismapped.return_value = False
        p.session = MagicMock(key=('ip', 'gate'))
        p.session.done.is_set.return_value = False
        MonitorWorkspace._tick(p)
        p.session.sample.assert_not_called()
        p.secondary_feed.render.assert_not_called()

    def test_minimized_popup_preserves_visible_tile_and_restores_latest(self):
        p = workspace()
        p._popup = MagicMock()
        p._popup.state.return_value = 'iconic'
        p._popup_feed = MagicMock()
        p.session = MagicMock(key=('ip', 'gate'))
        p.session.done.is_set.return_value = False
        p.session.sample.return_value = SimpleNamespace(frame_id=1, frame=object())
        MonitorWorkspace._tick(p)
        p.secondary_feed.render.assert_called_once()
        p._popup_feed.render.assert_not_called()
        p.session.stop.assert_not_called()
        p._popup.state.return_value = 'normal'
        p.session.sample.return_value = SimpleNamespace(frame_id=2, frame=object())
        MonitorWorkspace._tick(p)
        p._popup_feed.render.assert_called_once_with(p.session.sample.return_value.frame)

    def test_direct_popup_destroy_clears_references(self):
        p = workspace()
        popup = p._popup = MagicMock()
        p._popup_feed = MagicMock()
        p._last = {'window': 1, 'tile': 2}
        MonitorWorkspace._popup_destroyed(p, SimpleNamespace(widget=popup))
        self.assertIsNone(p._popup)
        self.assertIsNone(p._popup_feed)
        self.assertEqual(p._last, {'tile': 2})

    def test_closed_tick_does_not_reschedule(self):
        p = workspace()
        p._closed = True
        p._job = 'pending'
        MonitorWorkspace._tick(p)
        p.after_cancel.assert_called_once_with('pending')
        p.after.assert_not_called()

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
