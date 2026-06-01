"""Camera feed canvas widget for CBVMS Live Monitor."""

from __future__ import annotations

import tkinter as tk

import cv2
import numpy as np
from PIL import Image, ImageTk


class CameraFeed(tk.Canvas):
    """Canvas that displays camera frames using the itemconfig pattern.

    Sizing is dynamic — do NOT pass width/height in the constructor.
    Let the geometry manager (grid sticky="nsew") control the size.
    Call render(frame) directly from the UI thread each time a new frame
    arrives; call show_placeholder() when the camera is not open.
    """

    def __init__(self, master, bg_color: str = "#0F1117", **kwargs) -> None:
        super().__init__(master, bg=bg_color, highlightthickness=0, **kwargs)
        self._photo: ImageTk.PhotoImage | None = None
        self._item: int | None = None           # canvas image item id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self, frame: np.ndarray) -> None:
        """Display a BGR camera frame. Called from the UI thread."""
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 2 or h < 2:
            return
        try:
            if frame.shape[1] != w or frame.shape[0] != h:
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            photo = ImageTk.PhotoImage(image=pil, master=self)
            self._photo = photo          # keep strong reference — prevents GC
            if self._item is None:
                self._item = self.create_image(0, 0, anchor="nw", image=photo)
            else:
                self.itemconfig(self._item, image=photo)
        except Exception as exc:
            print(f"[CameraFeed] render error: {exc}")

    def show_placeholder(self) -> None:
        """Display a 'No Camera' placeholder. Called when camera is not open."""
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 2 or h < 2:
            return
        try:
            img = np.full((h, w, 3), (15, 17, 23), dtype=np.uint8)
            text = "No Camera"
            font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 1.2, 2
            (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
            cv2.putText(
                img, text,
                ((w - tw) // 2, (h + th) // 2),
                font, scale, (80, 80, 90), thick, cv2.LINE_AA,
            )
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            photo = ImageTk.PhotoImage(image=pil, master=self)
            self._photo = photo
            if self._item is None:
                self._item = self.create_image(0, 0, anchor="nw", image=photo)
            else:
                self.itemconfig(self._item, image=photo)
        except Exception as exc:
            print(f"[CameraFeed] placeholder error: {exc}")

    def cleanup(self) -> None:
        """Release resources on window close."""
        self.delete("all")
        self._photo = None
        self._item = None
