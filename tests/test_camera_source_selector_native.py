"""Exercise the visible selector in Tk's main loop; requires a desktop session.

Unlike a direct dashboard callback, these checks open the chooser, leave it open
while heartbeat timers run, select/dismiss it and destroy its owning workspace.
No cameras, database, inference, credentials or business records are involved.
"""
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import customtkinter as ctk

from ui.camera_source_selector import CameraSourceSelector
from ui.window_lifecycle import WorkspaceWindow


class CameraSourceSelectorNativeTests(unittest.TestCase):
    def setUp(self):
        self.root = WorkspaceWindow()
        self.root.geometry("500x280")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.variable = tk.StringVar(self.root, value="MacBook Camera")
        self.selections = []
        def selected(value):
            self.selections.append((value, self.selector._popup is None))
        self.selector = CameraSourceSelector(
            self.root, variable=self.variable, values=["MacBook Camera", "front gate"],
            command=selected, width=190, height=30, font=ctk.CTkFont(size=13),
        )
        self.selector.pack(pady=10)
        self.other = ctk.CTkButton(self.root, text="Other control")
        self.other.pack()
        self.pump(200)

    def tearDown(self):
        if not getattr(self.root, "_destroying", False):
            self.root.destroy()
        self.assertEqual(self.errors, [])

    def pump(self, milliseconds):
        self.root.after(milliseconds, self.root.quit)
        self.root.mainloop()

    def test_open_menu_runs_heartbeat_and_closes_without_a_native_modal_loop(self):
        ticks = []
        def heartbeat():
            ticks.append(time.monotonic())
            self.root.after(15, heartbeat)
        self.root.after(15, heartbeat)
        # Catch accidentally routing this selector back through Tk's Menu.post.
        with patch.object(tk.Menu, "post", side_effect=AssertionError("native menu entered")):
            started = time.monotonic()
            self.selector.open()
            self.assertLess(time.monotonic()-started, .05)
            self.pump(350)
            self.assertIsNotNone(self.selector._popup)
            self.assertTrue(self.selector._popup.winfo_ismapped())
            self.assertGreater(self.selector._popup.winfo_height(), 30)
            self.assertIsNone(self.root.grab_current())
            self.assertGreater(len(ticks), 10)
            self.assertLess(max(b-a for a,b in zip(ticks,ticks[1:])), .15)
            # Closing the root while the chooser is still open must return.
            self.root.after(20, self.root.destroy)
            self.root.mainloop()
        self.assertEqual(self.selections, [])

    def test_mouse_and_keyboard_selection_dismiss_before_switch_callback(self):
        self.selector.open()
        self.pump(40)
        box = self.selector._list.bbox(1)
        self.selector._released(SimpleNamespace(y=box[1]+box[3]//2))
        self.assertEqual(self.variable.get(), "front gate")
        self.assertEqual(self.selections, [("front gate", True)])
        self.selector.open()
        self.selector._list.selection_clear(0, "end")
        self.selector._list.selection_set(0)
        self.selector._keyboard_select(None)
        self.assertEqual(self.selections[-1], ("MacBook Camera", True))

    def test_escape_outside_click_resize_and_values_refresh_cancel_without_switch(self):
        self.selector.open()
        self.assertEqual(self.selector._escape(None), "break")
        self.assertIsNone(self.selector._popup)
        self.selector.open()
        self.selector._outside_click(SimpleNamespace(widget=self.other))
        self.assertIsNone(self.selector._popup)
        self.selector.open()
        self.selector.set_values(["MacBook Camera", "front gate", "USB 2"])
        self.assertIsNone(self.selector._popup)
        self.selector.select("removed camera")
        self.selector.open()
        self.root.geometry("520x300")
        self.pump(50)
        self.assertIsNone(self.selector._popup)
        self.assertEqual(self.variable.get(), "MacBook Camera")
        self.assertEqual(self.selections, [])

    def test_arrow_and_return_events_choose_the_highlighted_camera(self):
        self.root.focus_force()
        self.selector.open()
        self.pump(30)
        self.selector._list.event_generate("<Down>")
        self.assertEqual(self.selector._list.curselection(), (1,))
        self.selector._list.event_generate("<Return>")
        self.assertEqual(self.selections, [("front gate", True)])
        self.assertEqual(self.variable.get(), "front gate")

    def test_repeated_open_select_close_and_destroy_leave_no_bindings_or_trace(self):
        for index in range(40):
            self.selector.open()
            self.selector.select("front gate" if index%2 else "MacBook Camera")
        self.assertEqual(len(self.selections), 40)
        self.selector.open()
        bindings = list(self.selector._workspace_bindings.values())
        self.selector.destroy()
        self.variable.set("No selector remains")
        self.assertEqual(self.variable.trace_info(), [])
        for identifier in bindings:
            self.assertFalse(self.root.tk.call("info", "commands", identifier))
        self.assertFalse(any(isinstance(child, tk.Menu) for child in self.root.winfo_children()))


if __name__ == "__main__":
    unittest.main()
