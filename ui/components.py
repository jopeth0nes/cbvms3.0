"""Reusable UI theme and widgets for CBVMS."""

import time

import customtkinter as ctk
import cv2
import numpy as np
import tkinter as tk

COLOR_BG = "#0F1117"
COLOR_SURFACE = "#1A1D27"
COLOR_BORDER = "#2A2F3D"
COLOR_ACCENT = "#3B82F6"
COLOR_ACCENT_HOVER = "#2563EB"
COLOR_SAFE = "#10B981"
COLOR_DANGER = "#EF4444"
COLOR_WARNING = "#F59E0B"
COLOR_TEXT = "#F9FAFB"
COLOR_TEXT_MUTED = "#9CA3AF"

ROW_STRIPE_ODD = "#1A1F2E"
ROW_STRIPE_EVEN = "#151921"

APP_VERSION = "1.0.0"
APP_COLLEGE_NAME = "Your College Name"

FONT_FAMILY = "SF Pro Display"
try:
    ctk.CTkFont(family=FONT_FAMILY)
except Exception:
    FONT_FAMILY = "Segoe UI"

CORNER_RADIUS = 12
CORNER_RADIUS_LG = 16
PADDING = 16
PADDING_LG = 24
SIDEBAR_LEFT_WIDTH = 220
SIDEBAR_RIGHT_WIDTH = 280


def apply_cbvms_theme() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def heading_font(size: int = 22, weight: str = "bold") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def body_font(size: int = 14) -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size)


def panel_title_font() -> ctk.CTkFont:
    return heading_font(20, "bold")


def section_title_font() -> ctk.CTkFont:
    # CustomTkinter supports weight "normal"/"bold"; use normal for a lighter heading.
    return heading_font(14, "normal")


def body_small_font() -> ctk.CTkFont:
    return body_font(12)


class CBVMSCard(ctk.CTkFrame):
    """Rounded card container for dashboard panels."""

    def __init__(self, master, **kwargs) -> None:
        kwargs.setdefault("fg_color", COLOR_SURFACE)
        kwargs.setdefault("corner_radius", CORNER_RADIUS_LG)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", COLOR_BORDER)
        super().__init__(master, **kwargs)


def show_toast(master, message: str, type: str = "info", duration: int = 3000) -> None:
    """
    Floating bottom-right toast notifications that can stack.

    - master: any widget; toasts are attached to master.winfo_toplevel()
    - type: info|success|error|warning
    - duration: milliseconds before auto-destroy
    """

    root = master.winfo_toplevel()
    if not isinstance(root, (ctk.CTk, tk.Tk, tk.Toplevel)):
        return

    color_map = {
        "info": COLOR_ACCENT,
        "success": COLOR_SAFE,
        "error": COLOR_DANGER,
        "warning": COLOR_WARNING,
    }
    accent = color_map.get(type, COLOR_ACCENT)

    if not hasattr(root, "_cbvms_toasts"):
        root._cbvms_toasts = []  # type: ignore[attr-defined]

    toasts: list[ctk.CTkFrame] = root._cbvms_toasts  # type: ignore[attr-defined]

    toast = ctk.CTkFrame(
        root,
        fg_color=COLOR_SURFACE,
        corner_radius=CORNER_RADIUS,
        border_width=1,
        border_color=COLOR_BORDER,
    )
    toast.place(relx=1.0, rely=1.0, anchor="se", x=-18, y=-18)

    bar = ctk.CTkFrame(toast, fg_color=accent, corner_radius=CORNER_RADIUS, width=6)
    bar.place(x=0, y=0, relheight=1.0)

    msg = ctk.CTkLabel(
        toast,
        text=message,
        font=body_small_font(),
        text_color=COLOR_TEXT,
        justify="left",
        wraplength=320,
    )
    msg.pack(side="left", padx=(16, 12), pady=10)

    close_btn = ctk.CTkButton(
        toast,
        text="✕",
        width=28,
        height=28,
        corner_radius=CORNER_RADIUS,
        fg_color="transparent",
        hover_color=COLOR_BORDER,
        text_color=COLOR_TEXT_MUTED,
        command=lambda: _destroy_toast(root, toast),
    )
    close_btn.pack(side="right", padx=(0, 10), pady=10)

    toasts.append(toast)
    _reposition_toasts(root)

    def _auto_close() -> None:
        _destroy_toast(root, toast)

    try:
        toast.after(max(300, int(duration)), _auto_close)
    except Exception:
        _auto_close()


def _destroy_toast(root, toast: ctk.CTkFrame) -> None:
    try:
        toasts: list[ctk.CTkFrame] = getattr(root, "_cbvms_toasts", [])
        if toast in toasts:
            toasts.remove(toast)
    except Exception:
        pass
    try:
        toast.destroy()
    except Exception:
        pass
    _reposition_toasts(root)


def _reposition_toasts(root) -> None:
    try:
        toasts: list[ctk.CTkFrame] = getattr(root, "_cbvms_toasts", [])
    except Exception:
        return

    y = -18
    for toast in reversed(toasts):
        try:
            toast.update_idletasks()
            h = max(1, int(toast.winfo_height() or 0))
            toast.place_configure(relx=1.0, rely=1.0, anchor="se", x=-18, y=y)
            y -= h + 10
        except Exception:
            continue


# ----------------------------------------------------------------------
# Camera mirror toggle (display-only horizontal flip + card-flip animation)
# ----------------------------------------------------------------------


class MirrorController:
    """Display-only horizontal-mirror toggle with a quick card-flip animation.

    Mirroring is purely a viewing transform: a caller draws its overlays in screen space
    using ``display_mirror()`` to pick the orientation, then passes the finished frame
    through ``apply_anim()`` so the toggle animates. The frames fed to detection/recognition
    or saved during capture are never touched — only what reaches the screen.
    """

    _DURATION = 0.22  # animation length in seconds

    def __init__(self) -> None:
        self.enabled = False
        self._anim_start: float | None = None

    def toggle(self) -> None:
        self.enabled = not self.enabled
        self._anim_start = time.monotonic()

    def _progress(self) -> float | None:
        """Animation progress in [0, 1), or None when idle (auto-clears when finished)."""
        if self._anim_start is None:
            return None
        p = (time.monotonic() - self._anim_start) / self._DURATION
        if p >= 1.0:
            self._anim_start = None
            return None
        return p

    def is_animating(self) -> bool:
        return self._anim_start is not None

    def display_mirror(self) -> bool:
        """Orientation to DRAW this tick. During the flip, hold the old orientation until the
        midpoint (when the squish reaches 0 width), then reveal the new one — a seamless swap."""
        p = self._progress()
        if p is None:
            return self.enabled
        return (not self.enabled) if p < 0.5 else self.enabled

    def apply_anim(self, frame_bgr: "np.ndarray") -> "np.ndarray":
        """Squish the frame horizontally toward 0 width at the flip midpoint (card-flip).
        No-op when idle, so the steady-state render path pays nothing."""
        p = self._progress()
        if p is None or frame_bgr is None:
            return frame_bgr
        h, w = frame_bgr.shape[:2]
        factor = abs(1.0 - 2.0 * p)            # 1 → 0 → 1 across the animation
        new_w = max(1, int(round(w * factor)))
        squished = cv2.resize(frame_bgr, (new_w, h), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((h, w, 3), dtype=frame_bgr.dtype)
        x0 = (w - new_w) // 2
        canvas[:, x0:x0 + new_w] = squished
        return canvas


def make_mirror_button(master, controller: MirrorController, on_toggle=None) -> ctk.CTkButton:
    """A compact mirror-toggle pill meant to overlay a camera view (place it top-right).

    Reflects + drives ``controller`` state: muted when off, accent when on, with a brief
    flash on click for tactile feedback. The caller positions it with ``.place(...)``.
    """
    btn = ctk.CTkButton(
        master, text="⇄  Mirror", width=104, height=30, corner_radius=CORNER_RADIUS,
        fg_color=COLOR_SURFACE, hover_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED,
        border_width=1, border_color=COLOR_BORDER, font=body_small_font(),
    )

    def _styled() -> None:
        if controller.enabled:
            btn.configure(fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                          text_color=COLOR_TEXT, border_color=COLOR_ACCENT, text="⇄  Mirror On")
        else:
            btn.configure(fg_color=COLOR_SURFACE, hover_color=COLOR_BORDER,
                          text_color=COLOR_TEXT_MUTED, border_color=COLOR_BORDER, text="⇄  Mirror")

    def _click() -> None:
        controller.toggle()
        btn.configure(fg_color=COLOR_SAFE if controller.enabled else COLOR_BORDER)
        btn.after(120, _styled)
        if on_toggle is not None:
            on_toggle()

    btn.configure(command=_click)
    _styled()
    return btn
