"""Camera choices in the existing Tk loop, without a native modal menu.

macOS Menu.post() enters a nested Cocoa menu loop. Camera selection must not
keep that loop alive during source cancellation, navigation or root destruction.
This list is a child of the workspace: no Menu, extra window, grab or wait loop.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from ui.components import COLOR_ACCENT, COLOR_BORDER, COLOR_SURFACE, COLOR_TEXT


class CameraSourceSelector(ctk.CTkButton):
    def __init__(self, master, *, variable, values, command, font=None, **kwargs):
        self._selection = variable
        self._values = tuple(values)
        self._on_select = command
        self._popup = None
        self._list = None
        self._return_focus = None
        super().__init__(master, text="", command=self._toggle, font=font,
                         anchor="w", **kwargs)
        self._workspace = self.winfo_toplevel()
        self._variable_trace = variable.trace_add("write", self._update_label)
        self._update_label()
        # Toplevel bindtags receive descendant clicks too; preserve other bindings.
        self._workspace_bindings = {
            "<Button-1>": self._workspace.bind("<Button-1>", self._outside_click, add="+"),
            "<Escape>": self._workspace.bind("<Escape>", self._escape, add="+"),
            "<Configure>": self._workspace.bind("<Configure>", self._workspace_changed, add="+"),
        }
        self.bind("<Unmap>", lambda _event: self.close(), add="+")
        self._canvas.configure(takefocus=True)
        self.bind("<Return>", lambda _event: self.open(), add="+")
        self.bind("<space>", lambda _event: self.open(), add="+")
        self.bind("<Down>", lambda _event: self.open(), add="+")

    def _update_label(self, *_):
        self.configure(text=f"{self._selection.get()}  ▾")

    def set_values(self, values):
        self.close()
        self._values = tuple(dict.fromkeys(values))

    def _toggle(self):
        if self._popup is None:
            self.open()
        else:
            self.close()

    def open(self):
        if self.cget("state") == "disabled" or not self._values or self._popup is not None:
            return
        self._return_focus = self.focus_get()
        popup = self._popup = tk.Frame(self._workspace, bg=COLOR_BORDER, borderwidth=1)
        self._list = tk.Listbox(
            popup, height=min(8, len(self._values)), exportselection=False,
            selectmode="single", activestyle="dotbox", relief="flat", borderwidth=0,
            bg=COLOR_SURFACE, fg=COLOR_TEXT, selectbackground=COLOR_ACCENT,
            selectforeground=COLOR_TEXT, highlightthickness=0, font=self.cget("font"),
        )
        self._list.pack(side="left", fill="both", expand=True)
        if len(self._values) > 8:
            scrollbar = tk.Scrollbar(popup, command=self._list.yview)
            scrollbar.pack(side="right", fill="y")
            self._list.configure(yscrollcommand=scrollbar.set)
        for value in self._values:
            self._list.insert("end", value)
        current = self._selection.get()
        index = self._values.index(current) if current in self._values else 0
        self._list.selection_set(index)
        self._list.activate(index)
        self._list.see(index)
        self._list.bind("<ButtonRelease-1>", self._released)
        self._list.bind("<Return>", self._keyboard_select)
        self._list.bind("<space>", self._keyboard_select)
        self._list.bind("<Down>", lambda _event: self._move(1))
        self._list.bind("<Up>", lambda _event: self._move(-1))
        self._list.bind("<Tab>", self._tab)
        # Geometry uses actual pixel sizes; tk.Frame.place does not rescale them.
        width = min(max(self.winfo_width(), self._list.winfo_reqwidth()), self._workspace.winfo_width())
        height = min(self._list.winfo_reqheight()+2, self._workspace.winfo_height())
        x = max(0, min(self.winfo_rootx()-self._workspace.winfo_rootx(), self._workspace.winfo_width()-width))
        below = self.winfo_rooty()-self._workspace.winfo_rooty()+self.winfo_height()
        y = below if below+height <= self._workspace.winfo_height() else max(0, below-self.winfo_height()-height)
        popup.place(x=x, y=y, width=width, height=height)
        popup.lift()
        self._list.focus_set()

    def _released(self, event):
        index = self._list.nearest(event.y)
        box = self._list.bbox(index)
        if box is not None and box[1] <= event.y < box[1]+box[3]:
            self.select(self._values[index])
        return "break"

    def _keyboard_select(self, _event):
        chosen = self._list.curselection()
        if chosen:
            self.select(self._values[chosen[0]])
        return "break"

    def _move(self, delta):
        chosen = self._list.curselection()
        index = max(0, min(len(self._values)-1, (chosen[0] if chosen else 0)+delta))
        self._list.selection_clear(0, "end")
        self._list.selection_set(index)
        self._list.activate(index)
        self._list.see(index)
        return "break"

    def select(self, value):
        """Close before invoking the ordinary, asynchronous source-switch callback."""
        if value not in self._values or self.cget("state") == "disabled":
            return
        self.close()
        self._selection.set(value)
        self._on_select(value)

    def close(self, *, restore_focus=True):
        popup, self._popup = self._popup, None
        self._list = None
        if popup is not None:
            popup.destroy()
            focus, self._return_focus = self._return_focus, None
            if restore_focus and focus is not None and focus.winfo_exists():
                focus.focus_set()

    def _outside_click(self, event):
        if self._popup is None:
            return
        path = str(event.widget)
        if not any(path == str(widget) or path.startswith(str(widget)+".")
                   for widget in (self, self._popup)):
            self.close(restore_focus=False)

    def _escape(self, _event):
        if self._popup is not None:
            self.close()
            return "break"

    def _tab(self, _event):
        self.close()
        return "break"

    def _workspace_changed(self, event):
        if event.widget is self._workspace:
            self.close(restore_focus=False)

    def destroy(self):
        self.close(restore_focus=False)
        self._selection.trace_remove("write", self._variable_trace)
        for sequence, identifier in self._workspace_bindings.items():
            self._workspace.unbind(sequence, identifier)
        super().destroy()
