"""Native workspace checks with synthetic frames; no devices or database."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import customtkinter as ctk
from ui.monitor_workspace import MonitorWorkspace
from ui.components import MirrorController, make_mirror_button


class WorkspaceNativeTests(unittest.TestCase):
    def test_closed_popup_leaves_no_tk_font_finalizer_for_capture_thread(self):
        import gc
        from tkinter.font import Font
        root = ctk.CTk()
        dashboard = SimpleNamespace()
        workspace = MonitorWorkspace(root, dashboard)
        workspace.after_cancel(workspace._job)
        workspace._job = None
        gc.collect()
        was_enabled = gc.isenabled()
        gc.disable()
        deferred = []
        original = Font.__del__
        def finalizer(font):
            if threading.current_thread() is not threading.main_thread():
                # Record the unsafe call, but avoid actually deadlocking this test
                # in Tcl while the main thread waits for the collector.
                deferred.append(font)
            else:
                original(font)
        try:
            with patch.object(Font, '__del__', finalizer):
                workspace.pop_out()
                workspace.close_popup()
                collector = threading.Thread(target=gc.collect)
                collector.start()
                collector.join(2)
                self.assertFalse(collector.is_alive())
                self.assertEqual(deferred, [], 'Destroyed pop-out font reached worker GC')
        finally:
            for font in deferred:
                original(font)
            workspace.close()
            root.destroy()
            if was_enabled:
                gc.enable()

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
                import numpy as np
                session.sample.return_value = SimpleNamespace(frame_id=1,
                    frame=np.zeros((120, 160, 3), dtype=np.uint8))
                root.after(150, root.quit)
                root.mainloop()
                feed = workspace._popup_feed
                self.assertIsNotNone(feed._photo)
                workspace.close_popup()
                self.assertIsNone(feed._photo)
                self.assertIsNone(workspace._popup_feed)
                workspace.pop_out()
                workspace._popup.destroy()
                self.assertIsNone(workspace._popup)
                self.assertIsNone(workspace._popup_feed)
                root.after(100, root.quit)
                root.mainloop()
                self.assertIs(workspace.session, session)
                session.stop.assert_called()  # Focus previously stopped the same owner.
            finally:
                workspace.close()
                root.destroy()
