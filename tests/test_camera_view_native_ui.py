"""Native non-modal chooser / shared-window tests, synthetic pixels only."""
import gc
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import customtkinter as ctk
import numpy as np
from ui.monitor_workspace import MonitorWorkspace
from tests.test_camera_view import USB, IP, SOURCES


class CameraViewNativeTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.geometry('1000x700')
        self.dashboard = SimpleNamespace(_active_nav='live', _closed=threading.Event(),
            _camera_requested_source=SOURCES[USB], _cam_sources={s['label']:s for s in SOURCES.values()},
            _camera_state='STREAMING', _primary_video_fresh=True, _observation_only=True)
        self.factory = patch('ui.monitor_workspace.PreviewSession').start()
        self.session = self.factory.return_value
        self.session.key=IP
        self.session.source=SOURCES[IP]
        self.session.done.is_set.return_value=False
        self.session.stop_event.is_set.return_value=False
        self.session.status='Preview only · no analysis or logs'
        self.frame=np.zeros((120,160,3),np.uint8)
        self.session.sample.return_value=SimpleNamespace(frame_id=1, frame=self.frame)
        self.pref = patch('api.camera_store.get_camera_view_preference', return_value={}).start()
        self.save = patch('api.camera_store.save_camera_view_preference').start()
        self.ws=MonitorWorkspace(self.root,self.dashboard)
        self.ws.pack(fill='both',expand=True)
        self.pump()

    def tearDown(self):
        self.ws.close()
        self.root.destroy()
        patch.stopall()
        ctk.set_widget_scaling(1)

    def pump(self, ms=110):
        self.root.after(ms,self.root.quit)
        self.root.mainloop()

    def test_dialog_cancel_keyboard_scaling_and_single_instance(self):
        for scale in (1,1.25):
            ctk.set_widget_scaling(scale)
            self.ws.open_view_dialog()
            dialog=self.ws._dialog
            self.pump()
            self.ws.open_view_dialog()
            self.assertIs(self.ws._dialog,dialog)
            self.assertLessEqual(dialog.open_button.winfo_rooty()+dialog.open_button.winfo_height(),
                                 dialog.winfo_rooty()+dialog.winfo_height())
            for var in dialog._vars.values(): dialog.setvar(var,0)
            dialog.refresh()
            self.assertEqual(str(dialog.open_button.cget('state')),'disabled')
            dialog.submit()
            self.assertIs(self.ws._dialog,dialog)
            dialog.cancel()
            self.assertIsNone(self.ws._dialog)
            self.assertIsNone(self.ws._main_keys)
            self.assertIsNone(self.ws.session)
            self.save.assert_not_called()

    def test_accept_reconfigure_shared_capture_and_restore(self):
        self.ws.accept_view([IP], 'Live Monitor')
        self.pump()
        self.assertEqual(self.ws.tabs.get(),'Other camera')
        self.assertFalse(self.ws.primary_feed.winfo_ismapped())
        self.ws.accept_view([USB,IP],'Separate window')
        self.pump()
        popup=self.ws._popup
        self.ws.render_primary(self.frame)
        self.assertIsNotNone(self.ws._popup_primary_feed._photo)
        self.assertIsNotNone(self.ws._popup_feed._photo)
        self.factory.assert_called_once_with(SOURCES[IP])
        self.assertIn('Analysis active',self.ws._popup_labels['primary'].cget('text'))
        self.assertIn('Preview only',self.ws._popup_labels['secondary'].cget('text'))
        self.ws.accept_view([USB],'Separate window')
        self.pump()
        self.assertIs(self.ws._popup,popup)
        self.assertFalse(self.ws._popup_feed.winfo_ismapped())
        self.session.stop.assert_not_called()
        popup.withdraw()
        self.pump()
        self.assertEqual(self.ws.primary_targets(),[])
        popup.deiconify()
        self.pump()
        self.assertEqual(len(self.ws.primary_targets()),1)
        self.ws.close_popup()
        self.pump()
        self.session.stop.assert_not_called()
        self.assertIsNone(self.ws._popup_primary_feed)
        self.ws.accept_view([USB],'Live Monitor')
        self.pump()
        self.session.stop.assert_called()

    def test_escape_cancels_and_enter_accepts(self):
        self.ws.open_view_dialog()
        dialog=self.ws._dialog
        self.pump()
        dialog.focus_force()
        dialog.event_generate('<Escape>')
        self.pump()
        self.assertIsNone(self.ws._dialog)
        self.save.assert_not_called()
        self.ws.open_view_dialog()
        dialog=self.ws._dialog
        dialog.setvar(dialog.destination,'Separate window')
        self.pump()
        dialog.focus_force()
        dialog.event_generate('<Return>')
        self.pump()
        self.assertIsNone(self.ws._dialog)
        self.assertIsNotNone(self.ws._popup)
        self.save.assert_called_once_with([USB],'Separate window')

    def test_missing_source_and_offline_retry(self):
        self.ws.open_view_dialog()
        dialog=self.ws._dialog
        for key,var in dialog._vars.items(): dialog.setvar(var,int(key==IP))
        dialog.refresh()
        self.dashboard._cam_sources={'MacBook':SOURCES[USB]}
        self.pump()
        self.assertEqual(str(dialog.open_button.cget('state')),'disabled')
        self.assertIn('removed',dialog.explanation.cget('text'))
        dialog.cancel()
        self.dashboard._cam_sources={s['label']:s for s in SOURCES.values()}
        self.ws.accept_view([USB,IP],'Live Monitor')
        self.pump()
        self.session.done.is_set.return_value=True
        self.pump()
        self.assertIsNone(self.ws.session)
        self.assertIsNotNone(self.ws.secondary_feed._placeholder_key)
        self.assertIn('Unavailable',self.ws.secondary_label.cget('text'))
        self.assertTrue(self.ws.primary_feed.winfo_ismapped())
        self.session.done.is_set.return_value=False
        self.ws.retry()
        self.pump()
        self.assertEqual(self.factory.call_count,2)
        self.assertIs(self.ws.session,self.session)

    def test_dialog_destruction_does_not_leave_font_finalizers(self):
        from tkinter.font import Font
        gc.collect()
        gc.disable()
        deferred=[]
        original=Font.__del__
        def finalizer(font):
            if threading.current_thread() is not threading.main_thread(): deferred.append(font)
            else: original(font)
        try:
            with patch.object(Font,'__del__',finalizer):
                self.ws.open_view_dialog()
                self.ws._dialog.cancel()
                worker=threading.Thread(target=gc.collect)
                worker.start()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(deferred,[])
        finally:
            for font in deferred: original(font)
            gc.enable()

if __name__=='__main__': unittest.main()
