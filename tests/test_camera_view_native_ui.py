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

    def test_real_uncheck_front_gate_only(self):
        import tkinter as tk
        self.pref.return_value={'sources':[list(USB),list(IP)]}
        self.ws.open_view_dialog()
        dialog=self.ws._dialog
        control=next(w for w in dialog._cards[USB].winfo_children() if isinstance(w,tk.Checkbutton))
        control.invoke()
        self.assertEqual(dialog.getvar(dialog._vars[USB]),'0')
        self.assertEqual(dialog.selection(),[IP])

    def test_real_checkboxes_drive_selection_and_reconfigured_window(self):
        import tkinter as tk
        from ui.components import COLOR_ACCENT, COLOR_BORDER
        popup = None
        # Each chooser starts with both checked, reproducing uncheck via Tcl.
        self.pref.return_value={'sources':[list(USB),list(IP)], 'destination':'Separate window'}
        for wanted in ([USB,IP], [IP], [USB], [USB,IP], []):
            self.ws.open_view_dialog()
            dialog=self.ws._dialog
            controls={key:next(w for w in card.winfo_children() if isinstance(w,tk.Checkbutton))
                      for key,card in dialog._cards.items()}
            for key in (USB,IP):
                if key not in wanted: controls[key].invoke()
            # Toggle the other camera on and back off from a single selection.
            if len(wanted)==1:
                other=IP if wanted==[USB] else USB
                controls[other].invoke()
                controls[other].invoke()
            self.assertEqual(dialog.selection(),wanted)
            for key,card in dialog._cards.items():
                self.assertEqual(card.cget('highlightbackground'),COLOR_ACCENT if key in wanted else COLOR_BORDER)
            self.assertEqual(str(dialog.open_button.cget('state')),'normal' if wanted else 'disabled')
            self.assertIn('Side-by-side' if len(wanted)==2 else 'Focus' if wanted else 'Select',dialog.preview.cget('text'))
            previous_calls=self.save.call_count
            dialog.submit()
            if not wanted:
                dialog.focus_force()
                dialog.event_generate('<Return>')
                self.assertEqual(self.save.call_count,previous_calls)
                self.assertEqual(self.ws._popup_keys,(USB,IP))
                dialog.cancel()
                continue
            self.save.assert_called_with(wanted,'Separate window')
            self.assertEqual(self.ws._popup_keys,tuple(wanted))
            if popup is not None: self.assertIs(self.ws._popup,popup)
            popup=self.ws._popup
            self.assertIsNone(self.ws._popup_primary_feed._photo)
            self.assertIsNone(self.ws._popup_feed._photo)
            # Geometry manager state must agree BEFORE the next polling tick.
            self.assertEqual(bool(self.ws._popup_primary_tile.winfo_manager()),USB in wanted)
            self.assertEqual(bool(self.ws._popup_secondary_tile.winfo_manager()),IP in wanted)
            self.pump()
            self.assertEqual(self.ws._popup_primary_feed in self.ws.primary_targets(),USB in wanted)
            self.assertEqual(bool(self.ws._popup_feed.winfo_ismapped()),IP in wanted)
        active_keys=self.ws._popup_keys
        self.ws.open_view_dialog()
        dialog=self.ws._dialog
        control=next(w for w in dialog._cards[USB].winfo_children() if isinstance(w,tk.Checkbutton))
        control.invoke()
        dialog.cancel()
        self.assertEqual(self.ws._popup_keys,active_keys)
        self.assertIs(self.ws._popup,popup)
        # Explicit IDs survive role reversal; no old pixels remain on reassignment.
        self.ws.accept_view([USB],'Separate window')
        self.dashboard._camera_requested_source=SOURCES[IP]
        self.ws.primary_targets()
        self.assertEqual(self.ws._popup_keys,(USB,))
        self.assertFalse(self.ws._popup_primary_tile.winfo_manager())
        self.assertTrue(self.ws._popup_secondary_tile.winfo_manager())
        self.assertIsNone(self.ws._popup_feed._photo)

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
