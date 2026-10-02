"""Portal scroll host without nested Tk event loops or global wheel bindings."""
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk


class PortalScrollFrame(ctk.CTkFrame):
    def __init__(self, master, *, color):
        super().__init__(master, fg_color=color, corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._parent_canvas = tk.Canvas(self, bg=self._apply_appearance_mode(color), highlightthickness=0,
                                       width=1, height=1, yscrollincrement=16)
        self._parent_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._parent_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._parent_canvas.configure(yscrollcommand=scrollbar.set)
        self.body = ctk.CTkFrame(self._parent_canvas, fg_color=color, corner_radius=0)
        self._window = self._parent_canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.grid_columnconfigure(0, weight=1)
        self._layout_job = None
        self._parent_canvas.bind("<Configure>", self._resize)
        self.body.bind("<Configure>", self._schedule_region)
        self._wheel_root = self.winfo_toplevel()
        self._wheel_binding = self._wheel_root.bind("<MouseWheel>", self._wheel, add="+")

    def _resize(self, event):
        if int(float(self._parent_canvas.itemcget(self._window, "width"))) != event.width:
            self._parent_canvas.itemconfigure(self._window, width=event.width)

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "_parent_canvas"):
            self._parent_canvas.configure(bg=self._apply_appearance_mode(self.cget("fg_color")))

    def _schedule_region(self, _event=None):
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._update_region)

    def _update_region(self):
        self._layout_job = None
        region = self._parent_canvas.bbox(self._window)
        if region:
            self._parent_canvas.configure(scrollregion=region)

    def _wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self:
                delta = event.delta
                units = -int(delta / 120) if abs(delta) >= 120 else -int(delta)
                self._parent_canvas.yview_scroll(units, "units")
                return "break"
            widget = getattr(widget, "master", None)

    def destroy(self):
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        self._wheel_root.unbind("<MouseWheel>", self._wheel_binding)
        super().destroy()
