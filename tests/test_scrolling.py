"""Disposable native widgets; no database, camera or production records."""
import gc
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
import weakref

from ui.scrolling import Remainder, ScrollableFrame, decode_touchpad, install_scrolling
from ui.window_lifecycle import WorkspaceWindow


def packed(x, y):
    value = ((x & 65535) << 16) | (y & 65535)
    return value - 2**32 if value >= 2**31 else value


class DeltaTests(unittest.TestCase):
    def test_signed_axes(self):
        for pair in ((1, -1), (-1, 1), (-32768, 32767), (0, 0)):
            self.assertEqual(decode_touchpad(packed(*pair)), pair)

    def test_small_deltas_and_reversal(self):
        r = Remainder()
        self.assertEqual([r.take(v) for v in (.4, .4, -.4, -.4, -.4)], [0, 0, 0, 0, -1])
        self.assertEqual(r.take(.5), 0)
        self.assertEqual(r.take(.5), 1)


class NativeScrollTests(unittest.TestCase):
    def setUp(self):
        self.root = WorkspaceWindow()
        self.root.geometry('700x550+40+40')
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.router = install_scrolling(self.root)
        self.pump()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])
        gc.collect()

    def pump(self):
        self.root.update_idletasks()
        self.root.update()

    def canvas(self, parent, height=160, region=1000):
        c = tk.Canvas(parent, width=300, height=height, highlightthickness=0,
                      scrollregion=(0, 0, 1000, region), yscrollincrement=1,
                      xscrollincrement=1, yscrollcommand=lambda *a: None,
                      xscrollcommand=lambda *a: None)
        c.pack()
        self.pump()
        return c

    def event(self, widget, delta=-1, state=0):
        return SimpleNamespace(widget=widget, delta=delta, state=state,
                               x_root=widget.winfo_rootx()+5, y_root=widget.winfo_rooty()+5)

    def test_pointer_child_nested_handoff_and_no_overflow(self):
        outer = self.canvas(self.root, 350)
        body = tk.Frame(outer)
        outer.create_window(0, 0, anchor='nw', window=body)
        inner = self.canvas(body)
        label = tk.Label(inner, text='card')
        inner.create_window(0, 0, anchor='nw', window=label)
        self.pump()
        self.assertIs(self.router.target(label, 0, 1), inner)
        self.router.handle(self.event(label, packed(0, -1)), 'touch')
        self.assertGreater(inner.yview()[0], 0)
        self.assertEqual(outer.yview()[0], 0)
        inner.yview_moveto(1)
        self.assertIs(self.router.target(label, 0, 1), outer)
        inner.configure(scrollregion=(0, 0, 300, 50))
        self.assertIs(self.router.target(label, 0, 1), outer)
        outer.configure(scrollregion=(0, 0, 300, 50))
        self.assertIsNone(self.router.target(label, 0, 1))

    def test_real_tk_touchpad_binding_and_horizontal(self):
        c = self.canvas(self.root)
        for x, y in ((0, -1), (-1, 0)):
            c.event_generate('<TouchpadScroll>', delta=packed(x, y),
                             rootx=c.winfo_rootx()+10, rooty=c.winfo_rooty()+10)
            self.pump()
        self.assertGreater(c.yview()[0], 0)
        self.assertGreater(c.xview()[0], 0)
        self.router.handle(self.event(c, -1, 1), 'shift')
        self.assertGreater(c.xview()[0], .001)

    def test_native_handler_runs_once(self):
        text = tk.Text(self.root, height=5)
        text.pack()
        text.insert('end', '\n'.join(str(i) for i in range(200)))
        self.pump()
        calls = []
        text.bind('<MouseWheel>', lambda e: calls.append(e.delta), add='+')
        before = text.yview()
        event = self.event(text, -120)
        self.assertIsNone(self.router.handle(event, 'wheel'))
        self.assertEqual(before, text.yview())
        text.event_generate('<MouseWheel>', delta=-120, rootx=event.x_root, rooty=event.y_root)
        self.pump()
        self.assertEqual(calls, [-120])
        self.assertGreater(text.yview()[0], before[0])

    def test_dialog_isolation_and_grab(self):
        behind = self.canvas(self.root)
        dialog = tk.Toplevel(self.root)
        dialog.geometry('400x300+800+40')
        label = tk.Label(dialog, text='no overflow')
        label.pack()
        self.pump()
        self.assertIsNone(self.router.target(label, 0, 1))
        dialog.grab_set()
        self.assertEqual(self.router.handle(self.event(behind), 'wheel'), 'break')
        self.assertEqual(behind.yview()[0], 0)
        dialog.destroy()

    def test_dynamic_ctk_children_cleanup_and_no_global_wheel(self):
        baseline = self.root.bind_all('<MouseWheel>')
        maps = self.root.bind_all('<Map>')
        refs = []
        for _ in range(5):
            frame = ScrollableFrame(self.root, height=100)
            frame.pack()
            label = tk.Label(frame, text='card')
            label.pack()
            self.pump()
            self.assertEqual(label.bindtags().count(self.router.tag), 1)
            refs.append(weakref.ref(frame))
            frame.destroy()
            del frame, label
        gc.collect()
        self.assertTrue(all(r() is None for r in refs))
        self.assertEqual(baseline, self.root.bind_all('<MouseWheel>'))
        self.assertEqual(maps, self.root.bind_all('<Map>'))

    def test_small_canvas_deltas_reversal_and_pointer_not_focus(self):
        c = self.canvas(self.root)
        c.yview_moveto(.5)
        before = c.yview()[0]
        self.router.system = 'win32'
        for delta in (-2, -2):  # 1/3 pixel each, must accumulate.
            self.router.handle(self.event(c, delta), 'wheel')
        self.assertEqual(c.yview()[0], before)
        self.router.handle(self.event(c, 2), 'wheel')  # Reverse: discard stale remainder.
        self.assertEqual(c.yview()[0], before)
        for _ in range(3):
            event = self.event(c, 2)
            event.widget = self.root  # Delivery/focus differs from the pointer target.
            self.router.handle(event, 'wheel')
        self.assertLess(c.yview()[0], before)

    def test_native_table_boundary_hands_off_once(self):
        outer = self.canvas(self.root, 350)
        tree = ttk.Treeview(outer, height=4)
        outer.create_window(0, 0, anchor='nw', window=tree)
        for i in range(50):
            tree.insert('', 'end', text=str(i))
        self.pump()
        tree.yview_moveto(1)
        before = tree.yview()
        self.router.handle(self.event(tree, packed(0, -3)), 'touch')
        self.assertEqual(tree.yview(), before)
        self.assertGreater(outer.yview()[0], 0)

    def test_overflowing_ctk_cards_and_dialog(self):
        from ui.live_alerts import LiveAlerts
        frame = LiveAlerts(self.root, height=100)
        frame.pack()
        for i in range(25):
            tk.Label(frame, text='Synthetic activity ' + str(i)).grid(row=i+1, column=0)
        self.pump()
        canvas = frame._parent_canvas
        self.assertLess(canvas.yview()[1], 1)
        canvas.event_generate('<TouchpadScroll>', delta=packed(0, -2),
                              rootx=canvas.winfo_rootx()+5, rooty=canvas.winfo_rooty()+5)
        self.pump()
        self.assertGreater(canvas.yview()[0], 0)
        dialog = tk.Toplevel(self.root)
        dialog.geometry('400x300+800+40')
        inner = self.canvas(dialog)
        before = canvas.yview()
        self.router.handle(self.event(inner, packed(0, -2)), 'touch')
        self.assertGreater(inner.yview()[0], 0)
        self.assertEqual(canvas.yview(), before)
        dialog.destroy()

    def test_camera_canvas_inert_and_windows_linux_normalization(self):
        camera = tk.Canvas(self.root)
        camera.pack()
        self.pump()
        self.assertIsNone(self.router.target(camera, 1, 1))
        for system in ('win32', 'x11'):
            self.router.system = system
            self.assertEqual(self.router.deltas(self.event(camera, 30), 'wheel'), (0, -.25))
        self.assertEqual(self.router.deltas(self.event(camera), 'up'), (0, -1))
