"""One pointer-scoped wheel router per Tcl interpreter (Tk 8.6 and Tk 9).

Map observation only installs a leading bindtag; wheel handlers are never global.
Native Text/Listbox/Treeview bindings remain responsible for direct native input.
"""
from __future__ import annotations

import math
import tkinter as tk
import weakref

import customtkinter as ctk


def decode_touchpad(delta):
    value = int(delta)
    x, y = (value >> 16) & 0xffff, value & 0xffff
    return tuple(v - 65536 if v >= 32768 else v for v in (x, y))


class Remainder:
    def __init__(self):
        self.value = 0.0

    def take(self, amount):
        if self.value * amount < 0:
            self.value = 0.0
        self.value += amount
        whole = math.trunc(self.value)
        self.value -= whole
        return whole


def install_scrolling(widget):
    root = widget._root()
    router = getattr(root, '_scroll_router', None)
    if router is None:
        router = root._scroll_router = ScrollRouter(root)
    return router


class ScrollRouter:
    def __init__(self, root):
        self.root = weakref.ref(root)
        self.tag = 'CBVMSScroll' + str(id(self))
        self.system = root.tk.call('tk', 'windowingsystem')
        self.pending = weakref.WeakKeyDictionary()
        self.last = None
        self.sequences = []
        for sequence, kind in (('<MouseWheel>', 'wheel'),
                               ('<Shift-MouseWheel>', 'shift'),
                               ('<TouchpadScroll>', 'touch'),
                               ('<Button-4>', 'up'), ('<Button-5>', 'down'),
                               ('<Shift-Button-4>', 'left'), ('<Shift-Button-5>', 'right')):
            try:
                root.bind_class(self.tag, sequence, lambda e, k=kind: self.handle(e, k))
                self.sequences.append(sequence)
            except tk.TclError:  # Tk 8.6 has no TouchpadScroll; Aqua has no button 4/5.
                pass
        # A single owned observer also sees future dialog and card widgets.
        self.map_id = root.bind_all('<Map>', self._mapped, add='+')
        self.attach_tree(root)

    def attach_tree(self, widget):
        tags = widget.bindtags()
        if self.tag not in tags:
            widget.bindtags((self.tag,) + tags)
        for child in widget.winfo_children():
            self.attach_tree(child)

    def _mapped(self, event):
        # Use raw Tk bindtags; CTk's bind wrappers restrict other operations.
        widget = event.widget
        tags = widget.bindtags()
        if self.tag not in tags:
            widget.bindtags((self.tag,) + tags)

    def deltas(self, event, kind):
        if kind == 'touch':
            x, y = decode_touchpad(event.delta)
            return -x, -y
        if kind in ('up', 'down', 'left', 'right'):
            return {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}[kind]
        delta = -float(event.delta)
        if self.system != 'aqua':
            delta /= 120.0
        return (delta, 0) if kind == 'shift' or event.state & 1 else (0, delta)

    @staticmethod
    def eligible(widget, axis, amount):
        if not amount:
            return False
        cls = widget.winfo_class()
        if cls == 'Canvas':
            # Image/zoom/camera canvases have no scrolling contract.
            if not widget.cget(axis + 'scrollcommand') or not widget.cget('scrollregion'):
                return False
        elif cls not in ('Text', 'Listbox', 'Treeview'):
            return False
        first, last = getattr(widget, axis + 'view')()
        return (first > 1e-9 if amount < 0 else last < 1 - 1e-9)

    def target(self, pointer, dx, dy):
        top = pointer.winfo_toplevel()
        widget = pointer
        while widget is not None:
            if self.eligible(widget, 'x', dx) or self.eligible(widget, 'y', dy):
                return widget
            if widget is top:
                break
            widget = widget.master
        return None

    def handle(self, event, kind):
        root = self.root()
        if root is None:
            return None
        try:
            pointer = root.winfo_containing(event.x_root, event.y_root)
            if pointer is None:
                return None
            # A grab must never redirect a gesture to the dashboard underneath.
            grab = root.grab_current()
            if grab is not None and pointer.winfo_toplevel() is not grab.winfo_toplevel():
                return 'break'
            dx, dy = self.deltas(event, kind)
            target = self.target(pointer, dx, dy)
            if target is None:
                self.last = None
                self.pending.clear()
                # Do not let focus/grab delivery scroll a different native widget.
                if event.widget is not pointer and event.widget.winfo_class() in ("Text", "Listbox", "Treeview"):
                    return "break"
                return None
            if self.last is None or self.last() is not target:
                self.pending.clear()
                self.last = weakref.ref(target)
            # Preserve native acceleration, precise scrolling and widget bindings.
            if target is event.widget and target.winfo_class() != 'Canvas':
                return None
            for axis, amount in (('x', dx), ('y', dy)):
                if not self.eligible(target, axis, amount):
                    continue
                if target.winfo_class() == 'Canvas':
                    # Move in pixels without changing scrollbar/keyboard increments.
                    region = tuple(map(float, target.tk.splitlist(target.cget('scrollregion'))))
                    extent = region[2 if axis == 'x' else 3] - region[0 if axis == 'x' else 1]
                    pixels = amount if kind == 'touch' else amount * (8 if self.system == 'aqua' else 20)
                    accum = self.pending.setdefault(target, {}).setdefault(axis, Remainder())
                    pixels = accum.take(pixels)
                    view = getattr(target, axis + 'view')
                    if extent > 0 and pixels:
                        view('moveto', view()[0] + pixels / extent)
                else:
                    accum = self.pending.setdefault(target, {}).setdefault(axis, Remainder())
                    units = accum.take(amount)
                    if units:
                        getattr(target, axis + 'view_scroll')(units, 'units')
            return 'break'
        except (tk.TclError, KeyError):  # Widget disappeared during a queued event.
            return None


class ScrollableFrame(ctk.CTkScrollableFrame):
    """CTk layout with shared routing instead of per-frame global wheel callbacks."""
    def __init__(self, master, **kwargs):
        install_scrolling(master)
        super().__init__(master, **kwargs)
        # Precise Tk 9 events use pixels. CTk's 8-pixel snapping loses small deltas.
        self._parent_canvas.configure(xscrollincrement=1, yscrollincrement=1)

    def destroy(self):
        if getattr(self, "_scroll_destroying", False):
            return
        self._scroll_destroying = True
        super().destroy()
        # CTk otherwise leaves the outer canvas and its bound-method callbacks alive.
        self._parent_frame.destroy()

    def bind_all(self, sequence=None, func=None, add=None):
        if sequence in ('<MouseWheel>', '<KeyPress-Shift_L>', '<KeyPress-Shift_R>',
                        '<KeyRelease-Shift_L>', '<KeyRelease-Shift_R>'):
            return None
        return super().bind_all(sequence, func, add)
