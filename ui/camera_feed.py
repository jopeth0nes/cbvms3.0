"""Aspect-preserving camera canvas. All widget operations run on Tk's thread."""

from __future__ import annotations

import tkinter as tk

import cv2
import numpy as np
from PIL import Image, ImageTk


def fitted_frame_rect(frame_width: int, frame_height: int,
                      canvas_width: int, canvas_height: int) -> tuple[int, int, int, int]:
    """Return centered (x, y, width, height), without stretching or cropping."""
    if min(frame_width, frame_height, canvas_width, canvas_height) < 1:
        return (0, 0, 0, 0)
    scale = min(canvas_width / frame_width, canvas_height / frame_height)
    width = min(canvas_width, max(1, round(frame_width * scale)))
    height = min(canvas_height, max(1, round(frame_height * scale)))
    return ((canvas_width - width) // 2, (canvas_height - height) // 2, width, height)


class CameraFeed(tk.Canvas):
    """A persistent image item with letterboxing and a cached text placeholder."""

    def __init__(self, master, bg_color: str = "#0F1117", **kwargs) -> None:
        super().__init__(master, bg=bg_color, highlightthickness=0, **kwargs)
        self._photo: ImageTk.PhotoImage | None = None
        self._item: int | None = None
        self._placeholder_item: int | None = None
        self._placeholder_key: tuple | None = None
        self._geometry_key: tuple | None = None
        self._frame_rect = (0, 0, 0, 0)

    def render(self, frame: np.ndarray) -> bool:
        """Display BGR pixels, returning whether a frame was actually rendered.

        Overlay coordinates stay in source-frame space; the complete annotated image
        is fitted once, so mirrored boxes and pixels undergo exactly the same scaling.
        """
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 or height < 2 or frame is None or frame.size == 0:
            return False
        try:
            key = (frame.shape[1], frame.shape[0], width, height)
            if key != self._geometry_key:
                self._frame_rect = fitted_frame_rect(*key)
                self._geometry_key = key
            x, y, fitted_width, fitted_height = self._frame_rect
            if frame.shape[1] != fitted_width or frame.shape[0] != fitted_height:
                frame = cv2.resize(frame, (fitted_width, fitted_height), interpolation=cv2.INTER_LINEAR)
            photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), master=self)
            self._photo = photo  # Tk does not keep a Python reference.
            if self._item is None:
                self._item = self.create_image(x, y, anchor="nw", image=photo)
            else:
                self.coords(self._item, x, y)
                self.itemconfig(self._item, image=photo, state="normal")
            if self._placeholder_key is not None:
                self.itemconfig(self._placeholder_item, state="hidden")
                self._placeholder_key = None
            return True
        except (tk.TclError, cv2.error, ValueError) as exc:
            print(f"[CameraFeed] render error: {exc}")
            return False

    def show_placeholder(self, text: str = "No camera connected") -> bool:
        """Show loading/reconnection/availability status without creating pixel buffers."""
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 or height < 2:
            return False
        key = (text, width, height)
        if key == self._placeholder_key:
            return False
        if self._item is not None:
            self.itemconfig(self._item, state="hidden")
        options = dict(text=text, fill="#9CA3AF", font=("Helvetica", 15),
                       width=max(1, width - 40), justify="center", state="normal")
        if self._placeholder_item is None:
            self._placeholder_item = self.create_text(width // 2, height // 2, **options)
        else:
            self.coords(self._placeholder_item, width // 2, height // 2)
            self.itemconfig(self._placeholder_item, **options)
        self._placeholder_key = key
        return True

    def cleanup(self) -> None:
        self.delete("all")
        self._photo = None
        self._item = None
        self._placeholder_item = None
        self._placeholder_key = None
        self._geometry_key = None
