"""Actual settings switches/buttons with a silent audio backend; requires desktop Tk."""
import threading
import unittest
from unittest.mock import Mock
import customtkinter as ctk

from core.detection_audio import AudioDispatcher, SoundKind
from core.notifier import Notifier
from ui.settings import SettingsPanel


class SoundSettingsNativeTests(unittest.TestCase):
    def test_buttons_and_switches_are_audio_only(self):
        root=ctk.CTk()
        backend=Mock()
        audio=AudioDispatcher(backend)
        panel=SettingsPanel.__new__(SettingsPanel)
        ctk.CTkFrame.__init__(panel,root)
        panel.notifier=Notifier(audio=audio)
        panel.pack(fill='both',expand=True)
        panel._notifications_section(panel,row=0)
        root.update()
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)
        buttons=[w for w in widgets(panel) if isinstance(w,ctk.CTkButton) and w.cget('text')=='Preview']
        switches=[w for w in widgets(panel) if isinstance(w,ctk.CTkSwitch)]
        def drain():
            with audio._cv:
                self.assertTrue(audio._cv.wait_for(lambda:not audio._pending and audio._active is None,timeout=1))
        try:
            self.assertEqual(len(buttons),3)
            for button in buttons: button.invoke(); drain()
            self.assertEqual([c.args[0].stem for c in backend.play.call_args_list],
                             ['positive','violation','suspension'])
            master=next(w for w in switches if w.cget('text')=='Sound alerts')
            master.toggle()
            for button in buttons: button.invoke()
            drain(); self.assertEqual(backend.play.call_count,3)
            master.toggle()
            positive=next(w for w in switches if w.cget('text')=='Correct uniform chime')
            positive.toggle(); buttons[0].invoke(); drain()
            self.assertFalse(audio.kind_enabled(SoundKind.POSITIVE))
            self.assertEqual(backend.play.call_count,3)
            self.assertEqual(panel.notifier.get_log(),[])
            self.assertFalse(audio._cooldowns)
        finally:
            audio.close()
            if audio._thread: audio._thread.join(1)
            root.destroy()
