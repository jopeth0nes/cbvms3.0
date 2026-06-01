"""Notifications log panel for CBVMS — live violation/alert history."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from ui.components import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    CORNER_RADIUS,
    PADDING,
    body_font,
    body_small_font,
    heading_font,
    panel_title_font,
)

if TYPE_CHECKING:
    from core.notifier import Notification, Notifier

_REFRESH_MS = 3000


def _format_ts(epoch: float) -> str:
    """Render a timestamp as 'Today 14:32' or 'May 30 09:15'."""
    dt = datetime.fromtimestamp(epoch)
    if dt.date() == datetime.now().date():
        return f"Today {dt.strftime('%H:%M')}"
    return dt.strftime("%b %d %H:%M")


class NotificationsPanel(ctk.CTkFrame):
    """Scrollable list of violation notifications with filters + acknowledge."""

    def __init__(self, master, *, notifier: "Notifier", on_change: Callable[[], None] | None = None,
                 **kwargs) -> None:
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self._notifier = notifier
        self._on_change = on_change or (lambda: None)
        self._filter = "All"
        self._refresh_job: str | None = None
        self._last_sig: tuple | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_filter()

        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=2, column=0, sticky="nsew", padx=PADDING, pady=(0, PADDING))
        self._list.grid_columnconfigure(0, weight=1)

        self._render()
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # Header + filter
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 8))
        header.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            header, text="Notifications", font=panel_title_font(), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")

        self._unread_pill = ctk.CTkLabel(
            header, text="", font=body_small_font(), text_color=COLOR_TEXT,
            fg_color=COLOR_DANGER, corner_radius=999, padx=10, pady=2,
        )
        self._unread_pill.grid(row=0, column=1, sticky="w", padx=(10, 0))

        ctk.CTkButton(
            header, text="Mark all read", height=30, corner_radius=CORNER_RADIUS,
            fg_color="transparent", hover_color=COLOR_BORDER, text_color=COLOR_ACCENT,
            font=body_small_font(), command=self._mark_all_read,
        ).grid(row=0, column=3, sticky="e")

    def _build_filter(self) -> None:
        self._filter_btn = ctk.CTkSegmentedButton(
            self, values=["All", "Unread", "Today"],
            command=self._on_filter_change,
            fg_color=COLOR_SURFACE, selected_color=COLOR_ACCENT,
            selected_hover_color=COLOR_ACCENT_HOVER, unselected_color=COLOR_SURFACE,
            unselected_hover_color=COLOR_BORDER, text_color=COLOR_TEXT,
        )
        self._filter_btn.set("All")
        self._filter_btn.grid(row=1, column=0, sticky="w", padx=PADDING, pady=(0, 10))

    def _on_filter_change(self, value: str) -> None:
        self._filter = value
        self._last_sig = None  # force a rebuild
        self._render()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _mark_all_read(self) -> None:
        self._notifier.mark_all_read()
        self._last_sig = None
        self._render()
        self._on_change()

    def refresh_external(self) -> None:
        """Force a re-render (e.g. after the sidebar bell marks all read)."""
        if not self.winfo_exists():
            return
        self._last_sig = None
        self._render()

    def _acknowledge(self, notif_id: int) -> None:
        self._notifier.acknowledge(notif_id)
        self._last_sig = None
        self._render()
        self._on_change()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _visible_items(self) -> list["Notification"]:
        items = self._notifier.get_log()  # newest-first
        if self._filter == "Unread":
            return [n for n in items if not n.acknowledged]
        if self._filter == "Today":
            today = datetime.now().date()
            return [n for n in items if datetime.fromtimestamp(n.timestamp).date() == today]
        return items

    def _render(self) -> None:
        unread = self._notifier.unread_count()
        self._unread_pill.configure(text=f"{unread} unread")
        if unread == 0:
            self._unread_pill.grid_remove()
        else:
            self._unread_pill.grid()

        for child in self._list.winfo_children():
            child.destroy()

        items = self._visible_items()
        if not items:
            self._render_empty()
            return

        for r, notif in enumerate(items):
            self._render_card(r, notif)

    def _render_empty(self) -> None:
        wrap = ctk.CTkFrame(self._list, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="ew", pady=80)
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            wrap, text="🔔", font=ctk.CTkFont(size=48), text_color=COLOR_TEXT_MUTED,
        ).grid(row=0, column=0)
        ctk.CTkLabel(
            wrap, text="No violations detected yet", font=heading_font(18),
            text_color=COLOR_TEXT_MUTED,
        ).grid(row=1, column=0, pady=(8, 2))
        ctk.CTkLabel(
            wrap, text="Violations will appear here as they are detected.",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).grid(row=2, column=0)

    def _render_card(self, row: int, notif: "Notification") -> None:
        unread = not notif.acknowledged
        card = ctk.CTkFrame(
            self._list, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_DANGER if unread else COLOR_BORDER,
        )
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        card.grid_columnconfigure(1, weight=1)

        # Left accent bar
        ctk.CTkFrame(
            card, width=4, fg_color=COLOR_DANGER if unread else COLOR_BORDER, corner_radius=0,
        ).grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 10), pady=2)

        name = notif.student_name or "Unknown"
        ctk.CTkLabel(
            card, text=name, font=body_font(14), text_color=COLOR_TEXT, anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(10, 0))

        ctk.CTkLabel(
            card, text=_format_ts(notif.timestamp), font=body_small_font(),
            text_color=COLOR_TEXT_MUTED, anchor="e",
        ).grid(row=0, column=2, sticky="e", padx=(0, PADDING), pady=(10, 0))

        ctk.CTkLabel(
            card, text=notif.violation or "—", font=body_small_font(),
            text_color=COLOR_TEXT_MUTED, anchor="w", justify="left", wraplength=360,
        ).grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 10))

        if unread:
            ctk.CTkButton(
                card, text="✓ Acknowledge", height=28, corner_radius=CORNER_RADIUS,
                fg_color="transparent", hover_color=COLOR_BORDER, text_color=COLOR_ACCENT,
                font=body_small_font(), command=lambda i=notif.id: self._acknowledge(i),
            ).grid(row=1, column=2, sticky="e", padx=(0, PADDING), pady=(0, 8))

    # ------------------------------------------------------------------
    # Auto-refresh (flicker-free: rebuild only when the log changes)
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        try:
            if self.winfo_exists():
                self._refresh_job = self.after(_REFRESH_MS, self._refresh)
        except Exception:
            pass

    def _refresh(self) -> None:
        # winfo_exists() itself raises TclError once the app is destroyed, so the
        # check must be inside the guard (a stray after() can fire during teardown).
        try:
            if not self.winfo_exists():
                return
            items = self._notifier.get_log()
            sig = (len(items), self._notifier.unread_count(), self._filter)
            if sig != self._last_sig:
                self._last_sig = sig
                self._render()
        except Exception:
            return
        self._schedule_refresh()

    def destroy(self) -> None:  # type: ignore[override]
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        super().destroy()
