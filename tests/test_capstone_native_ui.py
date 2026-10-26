"""Desktop checks using synthetic students and temporary databases only."""
import time
import unittest
from unittest.mock import patch

import customtkinter as ctk

from core.trainer import ViolationTrainer
from tests.test_capstone_features import InPersonAppealTests
from ui.appeals_panel import AppealsPanel
from ui.training_panel import TrainingPanel


def descendants(parent):
    for widget in parent.winfo_children():
        yield widget
        yield from descendants(widget)


class CapstoneNativeTests(unittest.TestCase):
    def setUp(self):
        InPersonAppealTests.setUp(self)
        self.root = ctk.CTk()
        self.root.geometry('1280x850')
        self.addCleanup(self.close_root)

    def close_root(self):
        for job in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.tk.call('after', 'cancel', job)
        self.root.destroy()

    def pump(self, condition):
        until = time.monotonic() + 10
        while not condition() and time.monotonic() < until:
            self.root.update()
            time.sleep(.02)
        self.assertTrue(condition())

    def test_training_reference_tab_can_be_opened(self):
        with patch('ui.training_panel.list_references', return_value=[]):
            panel = TrainingPanel(self.root, trainer=ViolationTrainer(), get_frame=lambda: None)
        panel.pack(fill='both', expand=True)
        panel._tabview.set('DYCI Logo & Digital Shirts')
        self.root.update()
        self.assertIn('Digital shirt: 0 saved', panel._reference_status.cget('text'))

    def test_face_to_face_form_saves_and_opens_pending_case(self):
        panel = AppealsPanel(self.root, database=self.db, username='reviewer')
        panel.pack(fill='both', expand=True)
        panel._in_person_form()
        window = next(w for w in panel.winfo_children() if isinstance(w, ctk.CTkToplevel))
        entries = [w for w in descendants(window) if isinstance(w, ctk.CTkEntry)]
        entries[0].insert(0, 'S1')
        entries[1].insert(0, str(self.vid))
        boxes = [w for w in descendants(window) if isinstance(w, ctk.CTkTextbox)]
        boxes[0].insert('1.0', 'Please review the uniform detection with my office explanation.')
        boxes[1].insert('1.0', 'Met the student and reviewed the original detection image.')
        button = next(w for w in descendants(window) if isinstance(w, ctk.CTkButton)
                      and w.cget('text') == 'Record pending appeal')
        button.invoke()
        self.pump(lambda: bool(panel.case.get('in_person')))
        self.assertEqual(panel.case['status'], 'pending')
        self.assertTrue(any(isinstance(w, ctk.CTkLabel) and 'Met the student' in str(w.cget('text'))
                            for w in descendants(panel)))


if __name__ == '__main__':
    unittest.main()
