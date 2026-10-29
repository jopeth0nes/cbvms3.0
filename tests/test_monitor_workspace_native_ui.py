"""Native workspace checks with synthetic frames; no devices or database."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import customtkinter as ctk
from ui.monitor_workspace import MonitorWorkspace
from ui.components import MirrorController, make_mirror_button


class WorkspaceNativeTests(unittest.TestCase):
    def test_layout_popup_labels_and_shared_owner(self):
        root = ctk.CTk()
        root.geometry('650x600')
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(0, weight=1)
        usb = dict(type='usb', index=0, label='MacBook')
        gate = dict(type='ip', id='gate', label='Front Gate')
        dashboard = SimpleNamespace(_active_nav='live', _closed=threading.Event(),
            _camera_requested_source=usb, _cam_sources={'MacBook':usb,'Front Gate':gate},
            _camera_state='STREAMING', _observation_only=True)
        with patch('ui.monitor_workspace.PreviewSession') as factory:
            session = factory.return_value
            session.done.is_set.return_value = False
            session.sample.return_value = None
            session.key = ('ip', 'gate')
            session.status = 'Connecting'
            workspace = MonitorWorkspace(root, dashboard)
            workspace.grid(sticky='nsew')
            mirror = make_mirror_button(workspace.primary, MirrorController())
            mirror.place(in_=workspace.primary_feed, relx=1, x=-14, y=14, anchor='ne')
            try:
                root.update()
                for mode in ('Split','Other camera','Focus','Split'):
                    workspace.tabs.set(mode)
                    workspace._layout(mode)
                    workspace.after_cancel(workspace._job)
                    workspace._tick()
                    root.update_idletasks()
                    for tile in (workspace.primary, workspace.secondary):
                        if tile.winfo_ismapped():
                            self.assertLessEqual(tile.winfo_rootx()+tile.winfo_width(),
                                                 workspace.winfo_rootx()+workspace.winfo_width())
                    if workspace.primary.winfo_ismapped():
                        self.assertGreaterEqual(mirror.winfo_rootx(), workspace.primary_feed.winfo_rootx())
                        self.assertLessEqual(mirror.winfo_rootx()+mirror.winfo_width(),
                            workspace.primary_feed.winfo_rootx()+workspace.primary_feed.winfo_width())
                    else:
                        self.assertFalse(mirror.winfo_ismapped())
                workspace.pop_out()
                workspace.after_cancel(workspace._job)
                workspace._tick()
                root.update_idletasks()
                self.assertIn('Front Gate', workspace._popup.title())
                self.assertIn('Preview only', workspace.secondary_label.cget('text'))
                factory.assert_called_once_with(gate)
                workspace.close_popup()
                session.stop.assert_called()  # Focus previously stopped the same owner.
            finally:
                workspace.close()
                root.destroy()
