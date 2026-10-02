"""SECURE — standalone student portal (light theme).

Entirely self-contained: does NOT import ui/components.py (different theme). A student
sees only data belonging to their own student_id. Launched from auth/login.py.
"""

from __future__ import annotations
from core.evidence_integrity import original_evidence, supporting_evidence

import time
from core.portal_state import PortalRequests, page_snapshot, prepare_photo, PAGE_SIZE, comparable, violation_group
from core.diagnostics import event
import io
import queue
import threading
import tkinter as tk
from datetime import date, datetime

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

import os
from tkinter import filedialog

from core.appeal_analyzer import analyze_appeal
from core.discipline import display_local_datetime
from core.appeal_evidence import MAX_EVIDENCE_BYTES, validate_evidence
from core.student_status import CONTACT_FIELDS, standing_label, suspension_label
from core.discipline import (
    STUDENT_APPEAL_DAYS,
    utc_now,
    parse_db_datetime,
    remaining_time_text,
    violation_display_name,
)
from database.db_manager import CBVMSDatabase
from ui.components import apply_cbvms_theme
from ui.welcome import welcome_banner
from ui.portal_scroll import PortalScrollFrame
from ui.window_lifecycle import WorkspaceWindow

# --- Light-theme palette (all colors live here; nothing hardcoded below) ---
SP_BG = ("#F6F4EE", "#101D29")
SP_SIDEBAR = "#101D29"     # dark navy sidebar
SP_SURFACE = ("#FFFFFF", "#1B303D")
SP_ACCENT = "#167568"      # blue accent
SP_TEXT = ("#192F3B", "#EDF5F7")
SP_MUTED = ("#5E7078", "#B2C4CA")
SP_BORDER = ("#DCDDD5", "#38515D")
SP_SAFE = "#059669"        # green (compliant)
SP_DANGER = "#DC2626"      # red (violation)
SP_WARNING = "#D97706"     # orange (unreviewed)
# Local supporting tints (kept SP_*-prefixed; no equivalents above)
SP_ACCENT_HOVER = "#105E54"
SP_WHITE = "#FFFFFF"
SP_SIDEBAR_ACTIVE = "#284650"   # active/hover nav highlight (lighter navy)
SP_SIDEBAR_HOVER = "#203543"
SP_SIDEBAR_MUTED = "#B2C4CA"
SP_PILL_WARN_BG = ("#FEF3C7", "#49391E")
SP_PILL_OK_BG = ("#D1FAE5", "#163E35")
SP_PLACEHOLDER_BG = ("#EAECE5", "#263D49")
SP_HOVER_LIGHT = ("#E7F1ED", "#284650")
SP_APPEAL = "#32CD32"
SP_APPEAL_HOVER = "#28A428"
SP_DISABLED = "#D1D5DB"

_SIDEBAR_FULL = 280
_SIDEBAR_COMPACT = 200

_APPEAL_DAYS = STUDENT_APPEAL_DAYS

_NAV_ITEMS = [
    ("dashboard", "🏠  Dashboard"),
    ("violations", "⚠️  My Violations"),
    ("notifications", "🔔  Notifications"),
    ("appeals", "📝  My Appeals"),
    ("profile", "👤  My Profile"),
    ("settings", "⚙️  User Settings"),
    ("report", "📋  System Report"),
]
_REPORT_CATEGORIES = ["System Bug", "Account Issue", "Violation Dispute", "Other"]


def _f(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse persisted UTC timestamps and return them in the user's local timezone."""

    parsed = parse_db_datetime(ts)
    return parsed.astimezone() if parsed is not None else None


def _display_ts(ts: str | None, *, fallback: str = "—") -> str:
    return display_local_datetime(ts, fallback)


class StudentPortal(WorkspaceWindow):
    """Light-theme single-window portal scoped to one student_id."""

    def __init__(self, *, student_id: str, display_name: str, database=None) -> None:
        super().__init__()
        self.withdraw()
        apply_cbvms_theme()
        ctk.set_appearance_mode("light")

        if not isinstance(student_id, str) or not student_id.strip():
            raise ValueError("An authenticated student ID is required")
        self.student_id = student_id.strip()
        self.display_name = display_name
        self.logged_out = False
        self._prefs: dict = {"email_notifications": True}
        self._active = "dashboard"
        self._compact = False
        self._violation_filter = "All"
        self._image_refs: list = []  # keep ImageTk refs alive
        self._ai_ui_updates: queue.Queue[int] = queue.Queue()
        self._appeal_buttons: list = []
        self._appeal_eligibility = {}
        self._notification_violations = {}
        self._last_appeal_refresh = 0.

        self.db = database if database is not None else CBVMSDatabase()
        self._prefs.update(self.db.get_portal_preferences(self.student_id))
        self._compact = self._prefs.get("compact_sidebar", False)
        ctk.set_appearance_mode("dark" if self._prefs.get("dark_mode", False) else "light")
        self._refresh = PortalRequests(self.db, self.student_id)
        self._closed = False
        self._page_generation = 0
        self._request = None
        self._action_request = None
        self._action_callback = None
        self._action_error_callback = None
        self._page_state = 'loading'
        self._page_offset = 0
        self._focused_violation = None
        self._focused_appeal_id = None
        self._evidence_windows = []
        self._unread_count = 0
        self._violation_counts = {}
        self._pending_appeals = 0
        self._has_more = False
        self._profile_image = None
        self._render_job = None
        self._ui_jobs = set()
        self._navigation_metrics = []
        self._snapshot_key = None
        self._last_workflow_refresh = 0.
        self._active_suspension = None
        self._student: dict = {}
        self._current_term: dict = {}
        self._strike_summary: list[dict] = []
        self._violations: list[dict] = []
        self._notifications: list[dict] = []
        self._appeals: list[dict] = []
        self._appealed_ids: set[int] = set()
        self._activity_log: list[dict] = [
            {"action": "Logged in", "detail": "Student signed in to SECURE", "ts": datetime.now()},
        ]

        self.title("SECURE — Student Portal")
        self.geometry("1200x750")
        self.minsize(900, 600)
        self.configure(fg_color=SP_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._content = ctk.CTkFrame(self, fg_color=SP_BG)
        welcome_banner(self, self.display_name, "Student").grid(
            row=0, column=1, sticky="ew", padx=24, pady=(18, 0))
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self._content.grid(row=1, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        self._show("dashboard")
        self.bind("<FocusIn>", lambda event: self._reload_workflow_data() if event.widget is self and time.monotonic()-self._last_workflow_refresh >= 4 else None)
        self._poll_job = self.after(50, self._poll_ai_updates)
        self.after_idle(self.reveal_when_ready)

    # ------------------------------------------------------------------
    # Activity log + toast
    # ------------------------------------------------------------------

    def _log_activity(self, action: str, detail: str) -> None:
        self._activity_log.append({"action": action, "detail": detail, "ts": datetime.now()})
        del self._activity_log[:-100]

    def _toast(self, message: str, kind: str = "success") -> None:
        accent = {"success": SP_SAFE, "error": SP_DANGER, "info": SP_ACCENT}.get(kind, SP_ACCENT)
        toast = ctk.CTkFrame(self, fg_color=SP_SURFACE, corner_radius=10,
                             border_width=1, border_color=SP_BORDER)
        toast.place(relx=1.0, rely=1.0, anchor="se", x=-20, y=-20)
        ctk.CTkFrame(toast, fg_color=accent, width=5, corner_radius=10).pack(side="left", fill="y")
        ctk.CTkLabel(toast, text=message, font=_f(13), text_color=SP_TEXT,
                     wraplength=300, justify="left").pack(side="left", padx=14, pady=12)
        self._schedule_ui(3000, lambda: toast.winfo_exists() and toast.destroy())

    def _schedule_ui(self, delay, callback):
        """Keep delayed modal/toast work owned by the portal session."""
        def deliver():
            self._ui_jobs.discard(job)
            if not self._closed:
                callback()
        job = self.after(delay, deliver)
        self._ui_jobs.add(job)
        return job

    def _page_error(self, message):
        self._page_state = 'failed'
        self._page_status.configure(text=message, text_color=SP_DANGER)
        self._retry_button.grid(row=0, column=1, padx=12)

    def _poll_ai_updates(self) -> None:
        if self._closed:
            return
        try:
            self._poll_portal_results()
        except Exception as exc:
            event('portal_delivery_failed', page=self._active, error=str(exc))
            self._page_error(f"Could not display this page: {exc}")
        finally:
            if not self._closed:
                self._poll_job = self.after(50, self._poll_ai_updates)

    def _poll_portal_results(self):
        response = self._refresh.poll(self.student_id)
        while response is not None:
            request, data, error = response
            if request is self._action_request:
                callback = self._action_callback
                error_callback = self._action_error_callback
                self._action_request = self._action_callback = None
                self._action_error_callback = None
                if not request.cancelled.is_set() and request.generation == self._page_generation:
                    if error:
                        self._page_error(f"Could not save: {error}. Retry the action.")
                        if error_callback:
                            error_callback(error)
                        self._refresh_appeal_buttons()
                    else:
                        request.state = 'loaded'
                        self._page_state = 'empty' if self._page_is_empty() else 'loaded'
                        self._page_status.configure(text='Up to date', text_color=SP_MUTED)
                        self._retry_button.grid_remove()
                        callback(data)
                elif not self._closed:
                    # Navigation deferred its load while this operation was active.
                    if error:
                        self._toast(f'The previous operation failed: {error}', 'error')
                    self._reload_workflow_data()
                self._last_workflow_refresh = 0.
            elif request is self._request and request.generation == self._page_generation and not request.cancelled.is_set():
                self._request = None
                if error:
                    request.state = 'failed'
                    self._page_error(f"Could not load records: {error}. Try again.")
                else:
                    ignored = {'_timings', '_profile_image', '_appeal_eligibility'}
                    if self._active != 'notifications':
                        ignored.add('_unread_count')
                    key = comparable({k: v for k, v in data.items() if k not in ignored})
                    changed = key != self._snapshot_key
                    for name, value in data.items():
                        setattr(self, name, value)
                    self._refresh_appeal_buttons()
                    self._update_notif_badge()
                    local_form = self._active in {'settings', 'report'} and self._page_scroll
                    if (changed and not local_form) or not getattr(self, '_page_scroll', None):
                        self._render_page()
                    else:
                        self._page_status.configure(text='Up to date', text_color=SP_MUTED)
                        self._page_state = 'empty' if self._page_is_empty() else 'loaded'
                    if self._page_state != 'failed':
                        self._snapshot_key = key
                    request.state = self._page_state
            response = self._refresh.poll(self.student_id)
        now = time.monotonic()
        if now - self._last_appeal_refresh >= 1:
            self._last_appeal_refresh = now
            self._refresh_appeal_buttons()
        for request in (self._request, self._action_request):
            if request and request.state == 'loading' and now-request.created > request.timeout:
                request.state = 'failed'
                if not request.action:
                    request.cancelled.set()
                self._page_error('Request timed out. The worker is still finishing; Retry when it is available.'
                                 if not request.action else 'Saving is taking longer than expected. Waiting for the outcome; do not submit again.')
        if self._page_state in {'loaded', 'empty'} and now-self._last_workflow_refresh >= 4:
            self._reload_workflow_data()
        refreshed = False
        while True:
            try:
                self._ai_ui_updates.get_nowait()
                refreshed = True
            except queue.Empty:
                break
        if refreshed:
            self._reload_workflow_data()
            self._toast('AI advisory is ready. Your appeal status is shown in My Appeals.', 'info')

    def _refresh_appeal_results(self) -> None:
        """Pick up decisions made in another desktop session without closing forms."""
        self._reload_workflow_data()

    def _reload_workflow_data(self) -> None:
        if self._closed or self._action_request or (self._request and not self._request.cancelled.is_set()):
            return
        page, offset, group, focus = self._active, self._page_offset, self._violation_filter, self._focused_violation
        focused_appeal = self._focused_appeal_id
        appeal_ids = tuple({vid for button, vid in self._appeal_buttons if button.winfo_exists()})
        self._request = self._refresh.request(page, self._page_generation,
            lambda db, sid: page_snapshot(db, sid, page, offset=offset, group=group,
                                         violation_id=focus, appeal_ids=appeal_ids, appeal_id=focused_appeal))
        if self._request:
            self._last_workflow_refresh = time.monotonic()
            self._page_status.configure(text='Refreshing…' if self._page_scroll else 'Loading…', text_color=SP_MUTED)
            self._retry_button.grid_remove()
            if self._page_scroll is None:
                self._page_state = 'loading'

    def _run_action(self, operation, callback, error_callback=None):
        if self._action_request:
            return False
        if self._request:
            self._request.cancelled.set()
            self._request.state = 'cancelled'
            self._request = None
        request = self._refresh.request(self._active, self._page_generation, operation, action=True)
        if request:
            self._action_request, self._action_callback = request, callback
            self._action_error_callback = error_callback
            self._page_status.configure(text='Saving…', text_color=SP_MUTED)
            return True
        self._page_error('Another operation is finishing. Please retry.')
        return False

    def _page_is_empty(self):
        attribute = {'notifications': '_notifications', 'violations': '_violations', 'appeals': '_appeals'}.get(self._active)
        return attribute is not None and not getattr(self, attribute)

    def _render_page(self):
        old = self._page_scroll
        position = old._parent_canvas.yview()[0] if old else 0.
        host = PortalScrollFrame(self._page_body, color=SP_BG)
        self._render_body = host.body
        old_refs, self._image_refs = self._image_refs, []
        started = time.monotonic()
        try:
            self._standing_labels = []
            getattr(self, '_panel_' + self._active)()
            host.grid(row=0, column=0, sticky='nsew')
        except Exception as exc:
            host.destroy()
            self._image_refs = old_refs
            event('portal_render_failed', page=self._active, error=str(exc))
            self._page_error(f"Could not display this page: {exc}")
            return
        if old:
            old.destroy()
        self._page_scroll = host
        if position:
            generation = self._page_generation
            self._render_job = self.after_idle(lambda: self._restore_scroll(host, position, generation))
        self._page_state = 'empty' if self._page_is_empty() else 'loaded'
        self._page_status.configure(text='No records' if self._page_state == 'empty' else 'Up to date', text_color=SP_MUTED)
        self._retry_button.grid_remove()
        metric = dict(page=self._active, render_ms=round((time.monotonic()-started)*1000, 2),
                      content_ms=round((time.monotonic()-self._click_started)*1000, 2),
                      shell_ms=self._shell_ms, **getattr(self, '_timings', {}))
        self._navigation_metrics.append(metric)
        del self._navigation_metrics[:-100]
        event('portal_page_loaded', **metric)

    def _restore_scroll(self, host, position, generation):
        self._render_job = None
        if not self._closed and generation == self._page_generation and host.winfo_exists():
            host._parent_canvas.yview_moveto(position)

    def _current_term_label(self) -> str:
        name = self._current_term.get("semester_name") or "Current Semester"
        school_year = self._current_term.get("school_year") or ""
        return f"{name} · S.Y. {school_year}" if school_year else str(name)

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> None:
        self._sidebar = ctk.CTkFrame(self, width=_SIDEBAR_COMPACT if self._compact else _SIDEBAR_FULL,
                                    fg_color=SP_SIDEBAR, corner_radius=0)
        self._sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        self._sidebar.grid_propagate(False)
        self._sidebar.grid_rowconfigure(2, weight=1)

        # Branding
        brand = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(22, 10))
        top = ctk.CTkFrame(brand, fg_color="transparent")
        top.pack(anchor="w")
        ctk.CTkLabel(top, text="🛡️", font=_f(28)).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(top, text="CBVMS", font=_f(22, "bold"), text_color=SP_WHITE).pack(side="left")
        self._subtitle = ctk.CTkLabel(
            brand,
            text="Student Entrance Camera-based Uniform, Grooming, Accessory "
                 "Recognition and Evaluation",
            font=_f(10), text_color=SP_SIDEBAR_MUTED, justify="left", wraplength=230,
        )
        self._subtitle.pack(anchor="w", pady=(6, 0))
        if self._compact:
            self._subtitle.pack_forget()

        # User
        user = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        user.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 6))
        ctk.CTkLabel(user, text=self.display_name, font=_f(14, "bold"),
                     text_color=SP_WHITE, anchor="w").pack(anchor="w")
        ctk.CTkLabel(user, text="STUDENT WORKSPACE", font=_f(11, "bold"),
                     text_color="#D0AE68", anchor="w").pack(anchor="w")
        ctk.CTkFrame(self._sidebar, height=1, fg_color=SP_SIDEBAR_ACTIVE).grid(
            row=1, column=0, sticky="ew", padx=20, pady=(0, 0))

        # Nav
        nav = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="new", padx=14, pady=(14, 0))
        nav.grid_columnconfigure(0, weight=1)
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        for i, (key, label) in enumerate(_NAV_ITEMS):
            btn = ctk.CTkButton(
                nav, text=label, anchor="w", height=42, corner_radius=8,
                fg_color="transparent", hover_color=SP_SIDEBAR_HOVER,
                text_color=SP_WHITE, font=_f(13),
                command=lambda k=key: self._show(k),
            )
            btn.grid(row=i, column=0, sticky="ew", pady=3)
            self._nav_btns[key] = btn
        self._update_notif_badge()

        # Logout pinned bottom
        logout = ctk.CTkButton(
            self._sidebar, text="🚪  Logout", anchor="w", height=42, corner_radius=8,
            fg_color="transparent", hover_color=SP_DANGER, text_color=SP_WHITE, font=_f(13),
            command=self._logout,
        )
        self._logout_button = logout
        logout.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 18))

    def _set_active_nav(self) -> None:
        for k, btn in self._nav_btns.items():
            btn.configure(fg_color=SP_SIDEBAR_ACTIVE if k == self._active else "transparent")

    def _update_notif_badge(self) -> None:
        count = self._unread_count
        label = f"🔔  Notifications  ({count})" if count > 0 else "🔔  Notifications"
        if "notifications" in self._nav_btns:
            self._nav_btns["notifications"].configure(text=label)

    # ------------------------------------------------------------------
    # Panel dispatch
    # ------------------------------------------------------------------

    def _show(self, key: str, *, reload: bool = True, preserve_page=False) -> None:
        if self._closed:
            return
        for window in self._evidence_windows:
            if window.winfo_exists():
                window.destroy()
        self._evidence_windows.clear()
        self._click_started = time.monotonic()
        if self._request:
            self._request.cancelled.set()
            self._request.state = 'cancelled'
            self._request = None
        self._page_state = 'cancelled'
        self._page_generation += 1
        if self._render_job:
            self.after_cancel(self._render_job)
            self._render_job = None
        if not preserve_page:
            self._page_offset = 0
            self._focused_violation = None
            self._focused_appeal_id = None
        self._active = key
        self._timings = {'database_ms': 0., 'prepare_ms': 0.}
        self._snapshot_key = None
        self._set_active_nav()
        for w in self._content.winfo_children():
            w.destroy()
        self._image_refs.clear()
        self._page_scroll = None
        self._content.grid_rowconfigure(0, weight=0)
        self._content.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self._content, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', padx=30, pady=(18, 0))
        title = dict(_NAV_ITEMS)[key].split('  ', 1)[1]
        ctk.CTkLabel(head, text=title, font=_f(24, 'bold'), text_color=SP_TEXT).pack(anchor='w')
        self._page_subtitle = ctk.CTkLabel(head, text='', font=_f(12), text_color=SP_MUTED,
                                         wraplength=700, justify='left')
        self._page_subtitle.pack(anchor='w')
        status = ctk.CTkFrame(head, fg_color='transparent')
        status.pack(fill='x')
        self._page_status = ctk.CTkLabel(status, text='Loading…', font=_f(12), text_color=SP_MUTED,
                                       wraplength=650, justify='left')
        self._page_status.grid(row=0, column=0, sticky='w')
        self._retry_button = ctk.CTkButton(status, text='Retry', width=85, command=self._retry_page)
        self._page_body = ctk.CTkFrame(self._content, fg_color=SP_BG)
        self._page_body.grid(row=1, column=0, sticky='nsew')
        self._page_body.grid_columnconfigure(0, weight=1)
        self._page_body.grid_rowconfigure(0, weight=1)
        self._shell_ms = round((time.monotonic()-self._click_started)*1000, 2)
        self._page_state = 'loading'
        if key in {'settings', 'report'}:
            self._render_page()
        self._reload_workflow_data()

    def _retry_page(self):
        if self._action_request:
            self._page_status.configure(text='Saving is still in progress. Waiting for the outcome.')
            return
        self._snapshot_key = None
        self._reload_workflow_data()

    def _scroll_host(self, title: str, subtitle: str = ''):
        self._page_subtitle.configure(text=subtitle)
        return self._render_body

    def _pagination(self, parent, row):
        bar = ctk.CTkFrame(parent, fg_color='transparent')
        bar.grid(row=row, column=0, sticky='ew', padx=30, pady=12)
        ctk.CTkLabel(bar, text=f'Page {self._page_offset // PAGE_SIZE + 1}', text_color=SP_MUTED).pack(side='left')
        if self._page_offset:
            ctk.CTkButton(bar, text='Previous', width=100, command=lambda: self._change_page(-1)).pack(side='left', padx=8)
        if self._has_more:
            ctk.CTkButton(bar, text='Next', width=100, command=lambda: self._change_page(1)).pack(side='left', padx=8)
        if self._focused_violation:
            ctk.CTkButton(bar, text='Show all records', command=lambda: self._show('violations')).pack(side='left', padx=8)

    def _change_page(self, direction):
        self._page_offset = max(0, self._page_offset + direction * PAGE_SIZE)
        self._show(self._active, preserve_page=True)

    @staticmethod
    def _card(parent) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=SP_SURFACE, corner_radius=12,
                            border_width=1, border_color=SP_BORDER)

    def _status_pill(self, parent, status: str) -> ctk.CTkLabel:
        normalized = (status or "").lower()
        labels = {
            "pending_review": "Pending violation",
            "appeal_open": "Appeal available — no strike",
            "appeal_pending": "Pending Admin Decision",
            "unreviewed": "Historical record",
            "dismissed": "Resolved — detection dismissed",
            "approved": "Resolved — appeal approved",
            "confirmed": "Confirmed",
            "auto_confirmed": "Auto Confirmed",
            "reviewed": "Confirmed (Legacy)",
        }
        label = labels.get(normalized, normalized.replace("_", " ").title() or "Confirmed")
        return ctk.CTkLabel(
            parent, text=label, font=_f(11, "bold"),
            text_color=SP_WARNING if normalized in ("pending_review", "unreviewed") else SP_SAFE,
            fg_color=SP_PILL_WARN_BG if normalized in ("pending_review", "unreviewed") else SP_PILL_OK_BG,
            corner_radius=999, padx=10, pady=2,
        )

    # ------------------------------------------------------------------
    # Dashboard panel
    # ------------------------------------------------------------------

    def _panel_dashboard(self) -> None:
        v = [v for v in self._violations if violation_group(v) == "Confirmed"]
        total = self._violation_counts.get("confirmed", 0)
        pending = self._violation_counts.get("pending", 0)
        strikes = sum(int(v.get("active_count", 0)) for v in self._strike_summary)
        pending_appeals = self._pending_appeals
        last_dt = _parse_ts(v[0]["timestamp"]) if v else None
        last_str = last_dt.strftime("%b %d, %Y") if last_dt else "None"

        if last_dt:
            streak = max(0, (date.today() - last_dt.date()).days)
        else:
            enr = _parse_ts(self._student.get("enrolled_at", ""))
            streak = max(0, (date.today() - enr.date()).days) if enr else 0

        scroll = self._scroll_host(
            "Dashboard",
            f"Welcome back, {self.display_name}.  Current semester: {self._current_term_label()}.",
        )

        standing = ctk.CTkLabel(scroll,
            text=standing_label(self._student) + " · " + suspension_label(self._active_suspension),
            font=_f(13, "bold"), text_color=SP_WARNING, anchor="w")
        standing.grid(row=3, column=0, sticky="ew", padx=30, pady=(0, 12))
        self._standing_labels.append((standing, "combined"))
        cards = ctk.CTkFrame(scroll, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=30, pady=(14, 8))
        for c in range(4):
            cards.grid_columnconfigure(c, weight=1, uniform="stat")
        specs = [
            ("⚠️", "Confirmed Violations", str(total), SP_DANGER if total > 0 else SP_SAFE),
            ("📋", "Pending Appeals", str(pending_appeals), SP_WARNING if pending_appeals > 0 else SP_SAFE),
            ("🕒", "Pending Detections", str(pending), SP_WARNING),
            ("✅", "Active Strikes", str(strikes), SP_DANGER if strikes else SP_SAFE),
        ]
        for col, (icon, label, value, color) in enumerate(specs):
            card = self._card(cards)
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 12, 0))
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=icon, font=_f(22)).grid(row=0, column=0, rowspan=2, padx=(16, 8), pady=14)
            ctk.CTkLabel(card, text=value, font=_f(22, "bold"), text_color=color,
                         anchor="w").grid(row=0, column=1, sticky="w", pady=(16, 0), padx=(0, 14))
            ctk.CTkLabel(card, text=label, font=_f(11), text_color=SP_MUTED,
                         anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 16), padx=(0, 14))

        body = ctk.CTkFrame(scroll, fg_color="transparent")
        body.grid(row=2, column=0, sticky="ew", padx=30, pady=(8, 26))
        body.grid_columnconfigure(0, weight=3, uniform="b")
        body.grid_columnconfigure(1, weight=2, uniform="b")

        self._dash_recent(body)
        self._dash_breakdown(body)

    def _dash_recent(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Recent Violations", font=_f(15, "bold"),
                     text_color=SP_TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="View All →", width=80, height=26, corner_radius=8,
                      fg_color="transparent", hover_color=SP_HOVER_LIGHT, text_color=SP_ACCENT,
                      font=_f(12), command=lambda: self._show("violations")).grid(row=0, column=1, sticky="e")

        if not self._violations:
            self._empty_state(card, row=1)
            return
        for i, viol in enumerate(self._violations[:5], start=1):
            self._recent_row(card, i, viol)

    def _recent_row(self, parent, row: int, viol: dict) -> None:
        rf = ctk.CTkFrame(parent, fg_color=SP_BG, corner_radius=8)
        rf.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 8))
        rf.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(rf, width=4, fg_color=SP_DANGER, corner_radius=8).grid(
            row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 10), pady=2)
        ctk.CTkLabel(rf, text=viol.get("violation_label") or violation_display_name(
                         viol.get("violation_code"), viol.get("violation_type")),
                     font=_f(13, "bold"),
                     text_color=SP_TEXT, anchor="w").grid(row=0, column=1, sticky="w", pady=(8, 0))
        dt = _parse_ts(viol.get("timestamp", ""))
        ts = dt.strftime("%b %d, %Y %H:%M") if dt else str(viol.get("timestamp", ""))
        ctk.CTkLabel(rf, text=ts, font=_f(11), text_color=SP_MUTED, anchor="w").grid(
            row=1, column=1, sticky="w", pady=(0, 8))
        self._status_pill(rf, viol.get("status", "confirmed")).grid(
            row=0, column=2, rowspan=2, padx=(8, 12))

    def _dash_breakdown(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Current Semester Strikes", font=_f(15, "bold"),
                     text_color=SP_TEXT).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 10))
        ctk.CTkLabel(card, text=self._current_term_label(), font=_f(11),
                     text_color=SP_MUTED).grid(row=1, column=0, sticky="w", padx=16,
                                                pady=(0, 6))

        summaries = sorted(
            self._strike_summary,
            key=lambda item: (-int(item.get("active_count") or 0),
                              str(item.get("violation_label") or "")),
        )
        for i, item in enumerate(summaries, start=2):
            count = int(item.get("active_count") or 0)
            limit = max(1, int(item.get("strike_limit") or 3))
            label = item.get("violation_label") or violation_display_name(
                item.get("violation_code"))
            rowf = ctk.CTkFrame(card, fg_color="transparent")
            rowf.grid(row=i, column=0, sticky="ew", padx=16, pady=4)
            rowf.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(rowf, text=label, font=_f(12, "bold"), text_color=SP_TEXT,
                         anchor="w").grid(
                row=0, column=0, sticky="w")
            ctk.CTkLabel(rowf, text=f"{count} / {limit} strikes", font=_f(12, "bold"),
                         text_color=SP_DANGER if item.get("action_required") else SP_ACCENT).grid(
                row=0, column=1, sticky="e", padx=(8, 0))
            filled = min(count, limit)
            dots = " ".join(["●"] * filled + ["○"] * (limit - filled))
            ctk.CTkLabel(rowf, text=dots, font=_f(14),
                         text_color=SP_DANGER if item.get("action_required") else SP_ACCENT,
                         anchor="w").grid(row=1, column=0, columnspan=2, sticky="w",
                                           pady=(2, 0))
            if item.get("action_required"):
                ctk.CTkLabel(rowf, text="Third Strike Reached — Action Required",
                             font=_f(11, "bold"), text_color=SP_DANGER, anchor="w").grid(
                    row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

    def _empty_state(self, parent, row: int) -> None:
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.grid(row=row, column=0, sticky="ew", padx=16, pady=30)
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(wrap, text="✅", font=_f(40)).grid(row=0, column=0)
        ctk.CTkLabel(wrap, text="No records in this view", font=_f(15, "bold"),
                     text_color=SP_TEXT).grid(row=1, column=0, pady=(6, 2))
        ctk.CTkLabel(wrap, text="New detections appear here after they are saved.", font=_f(12),
                     text_color=SP_MUTED).grid(row=2, column=0)

    # ------------------------------------------------------------------
    # My Violations panel
    # ------------------------------------------------------------------

    def _panel_violations(self) -> None:
        self._log_activity("Violations viewed", "Opened the My Violations page")
        scroll = self._scroll_host(
            "My Violations",
            f"Detections and review history · Current semester: {self._current_term_label()}",
        )

        head = ctk.CTkFrame(scroll, fg_color="transparent")
        head.grid(row=1, column=0, sticky="ew", padx=30, pady=(8, 10))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=f"{self._violation_counts.get('pending', 0)} pending · {self._violation_counts.get('confirmed', 0)} confirmed · {sum(int(v.get('active_count', 0)) for v in self._strike_summary)} active strikes", font=_f(12, "bold"),
                     text_color=SP_WHITE, fg_color=SP_ACCENT, corner_radius=999,
                     padx=12, pady=3).grid(row=0, column=0, sticky="w")
        seg = ctk.CTkSegmentedButton(
            head, values=["All", "Pending", "Confirmed", "Resolved"], command=self._on_violation_filter,
            fg_color=SP_BORDER, selected_color=SP_ACCENT, selected_hover_color=SP_ACCENT_HOVER,
            unselected_color=SP_SURFACE, unselected_hover_color=SP_HOVER_LIGHT,
            text_color=SP_TEXT,
        )
        seg.set(self._violation_filter)
        seg.grid(row=0, column=1, sticky="e")

        self._viol_list = ctk.CTkFrame(scroll, fg_color="transparent")
        self._viol_list.grid(row=2, column=0, sticky="ew", padx=30, pady=(0, 26))
        self._viol_list.grid_columnconfigure(0, weight=1)
        self._render_violation_list()
        self._pagination(scroll, 3)

    def _on_violation_filter(self, value: str) -> None:
        self._violation_filter = value
        self._show('violations')

    def _render_violation_list(self) -> None:
        for w in self._viol_list.winfo_children():
            w.destroy()
        items = [v for v in self._violations if self._violation_filter == "All" or violation_group(v) == self._violation_filter]

        if not items:
            self._empty_state(self._viol_list, row=0)
            return
        for i, viol in enumerate(items):
            self._violation_card(i, viol)

    def _violation_card(self, row: int, viol: dict) -> None:
        viol_id = viol.get("id")
        strike_active = bool(int(viol.get("strike_active") or 0))
        appeal_status = (viol.get("appeal_status") or "not_submitted").lower()
        violation_label = viol.get("violation_label") or violation_display_name(
            viol.get("violation_code"), viol.get("violation_type"))
        card = self._card(self._viol_list)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(card, width=4, fg_color=SP_DANGER if strike_active else SP_SAFE,
                     corner_radius=8).grid(row=0, column=0, rowspan=8, sticky="nsw",
                                           padx=(0, 12), pady=2)
        ctk.CTkLabel(card, text=f"Violation #{viol_id} · {violation_label}", font=_f(14, "bold"),
                     text_color=SP_TEXT, anchor="w").grid(row=0, column=1, sticky="w", pady=(12, 0))
        ctk.CTkLabel(card, text=_display_ts(viol.get("timestamp")), font=_f(11),
                     text_color=SP_MUTED, anchor="e").grid(
            row=0, column=2, sticky="e", padx=(0, 16), pady=(12, 0))
        self._status_pill(card, "approved" if appeal_status == "approved" else "appeal_pending" if appeal_status == "pending" else "appeal_open" if viol.get("can_appeal") else viol.get("status", "confirmed")).grid(
            row=1, column=1, sticky="w", pady=(4, 0))

        term_name = viol.get("semester_name") or "Legacy / Unassigned"
        school_year = viol.get("school_year") or ""
        term_text = f"{term_name} · S.Y. {school_year}" if school_year else str(term_name)
        ctk.CTkLabel(card, text=term_text, font=_f(11), text_color=SP_MUTED,
                     anchor="e").grid(row=1, column=2, sticky="e", padx=(0, 16), pady=(4, 0))

        timing_text = (
            f"Detected: {_display_ts(viol.get('timestamp'))}  ·  "
            f"Published: {_display_ts(viol.get('appeal_opened_at'))}"
        )
        ctk.CTkLabel(card, text=timing_text, font=_f(11), text_color=SP_MUTED,
                     anchor="w", justify="left", wraplength=720).grid(
            row=2, column=1, columnspan=2, sticky="w", padx=(0, 16), pady=(6, 0))

        removal_reason = (viol.get("strike_removal_reason") or "").replace("_", " ").title()
        if strike_active and appeal_status == "pending":
            strike_text, strike_color = "Historical strike conflict — administrator reconciliation required", SP_WARNING
        elif viol.get("can_appeal") or appeal_status == "pending":
            strike_text, strike_color = "No strike while appeal is available/pending", SP_WARNING
        elif strike_active:
            strike_text, strike_color = "Strike Status: Active", SP_DANGER
        elif removal_reason:
            strike_text, strike_color = f"Strike Status: Removed — {removal_reason}", SP_SAFE
        else:
            strike_text, strike_color = "No active strike", SP_MUTED
        ctk.CTkLabel(card, text=strike_text, font=_f(11, "bold"), text_color=strike_color,
                     anchor="w").grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 0))

        deadline = viol.get("appeal_deadline")
        deadline_text = ("Resolved — no appeal required" if viol.get("status") == "dismissed" else
                         f"Appeal deadline: {_display_ts(deadline)}")
        if viol.get("can_appeal"):
            deadline_text += f"  ·  {remaining_time_text(deadline)}"
        deadline_label = ctk.CTkLabel(card, text=deadline_text, font=_f(11), text_color=SP_MUTED,
                                     anchor="w")
        deadline_label.grid(row=4, column=1, columnspan=2, sticky="w", pady=(3, 0))

        outcomes = {
            "pending": ("Pending Admin Decision — No strike while appeal is pending", SP_WARNING),
            "approved": ("Appeal Approved — No active strike", SP_SAFE),
            "rejected": ("Appeal Rejected — One finalized strike", SP_DANGER),
        }
        if strike_active and appeal_status == "pending":
            outcomes["pending"] = ("Pending Admin Decision — historical strike requires reconciliation", SP_WARNING)
        if viol.get("status") == "dismissed":
            outcome_text, outcome_color = "Resolved — detection dismissed", SP_SAFE
        elif appeal_status in outcomes:
            outcome_text, outcome_color = outcomes[appeal_status]
        elif viol.get("appeal_eligibility_reason") == "deadline_expired":
            outcome_text, outcome_color = "Appeal Period Expired", SP_MUTED
        elif not viol.get("can_appeal"):
            outcome_text, outcome_color = self._appeal_ineligible_message(viol.get("appeal_eligibility_reason")), SP_MUTED
        else:
            outcome_text, outcome_color = "Student Appeal Window Open", SP_WARNING
        outcome_label = ctk.CTkLabel(card, text=outcome_text, font=_f(11, "bold"),
                                     text_color=outcome_color, anchor="w")
        outcome_label.grid(row=5, column=1, columnspan=2, sticky="w", pady=(3, 0))

        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.grid(row=6, column=1, columnspan=2, sticky="w", pady=(8, 0))
        if viol.get("has_snapshot") or viol.get("snapshot"):
            ctk.CTkButton(action_row, text="View Detection Evidence", width=120, height=30, corner_radius=8,
                          fg_color=SP_ACCENT, hover_color=SP_ACCENT_HOVER, text_color=SP_WHITE,
                          font=_f(12), command=lambda x=viol: self._open_snapshot(x)).pack(
                side="left", padx=(0, 8))

        if appeal_status != "not_submitted":
            ctk.CTkButton(action_row, text="View Appeal" if appeal_status == "pending" else "View Decision",
                command=lambda aid=viol.get("appeal_id"): self._view_appeal(aid)).pack(side="left")
        elif viol_id and viol.get("status") not in ("dismissed", "resolved"):
            self._make_appeal_button(action_row, viol, outcome_label=outcome_label,
                                     deadline_label=deadline_label).pack(side="left")

        ctk.CTkFrame(card, fg_color="transparent", height=10).grid(row=7, column=1,
                                                                    pady=(0, 4))

    def _appeal_status_pill(self, parent, status: str) -> ctk.CTkLabel:
        colors = {
            "pending": (SP_WARNING, SP_PILL_WARN_BG),
            "approved": (SP_SAFE, SP_PILL_OK_BG),
            "rejected": (SP_DANGER, "#FEE2E2"),
        }
        fg, bg = colors.get(status, (SP_MUTED, SP_BORDER))
        return ctk.CTkLabel(
            parent, text=f"Appeal: {status.title()}", font=_f(11, "bold"),
            text_color=fg, fg_color=bg, corner_radius=999, padx=10, pady=2,
        )

    @staticmethod
    def _appeal_ineligible_message(reason: str) -> str:
        return {
            "already_submitted": "You have already submitted an appeal for this violation.",
            "deadline_expired": "The five-day appeal period has expired.",
            "not_owner": "This violation does not belong to your account.",
            "resolved": "This violation is resolved or historical and cannot receive a new appeal.",
            "no_active_strike": "This violation no longer has an active strike to appeal.",
            "no_deadline": "No appeal deadline is available for this historical record.",
            "not_found": "The violation could not be found.",
        }.get(reason, "This violation is not currently eligible for appeal.")

    def _make_appeal_button(self, parent, viol: dict, *, outcome_label=None, deadline_label=None):
        button = ctk.CTkButton(
            parent, width=235, height=30, corner_radius=8, font=_f(12),
            command=lambda selected=dict(viol): self._open_appeal_form(selected),
        )
        button._appeal_outcome_label = outcome_label
        button._appeal_deadline_label = deadline_label
        self._appeal_eligibility.setdefault(viol["id"], {
            "eligible": bool(viol.get("can_appeal")),
            "reason": viol.get("appeal_eligibility_reason"),
            "deadline": viol.get("appeal_deadline"),
        })
        self._appeal_buttons.append((button, viol["id"]))
        self._refresh_appeal_buttons()
        return button

    def _refresh_appeal_buttons(self) -> None:
        """Use worker snapshots and the clock; never query SQLite on Tk's thread."""
        alive = []
        for button, violation_id in self._appeal_buttons:
            if not button.winfo_exists():
                continue
            eligibility = self._appeal_eligibility.get(violation_id, {})
            enabled = bool(eligibility.get("eligible"))
            reason = eligibility.get("reason")
            deadline = parse_db_datetime(eligibility.get("deadline"))
            if enabled and deadline is not None and utc_now() > deadline:
                enabled, reason = False, "deadline_expired"
            enabled = enabled and not bool(getattr(self, "_action_request", None))
            text = ("Submit Appeal" if enabled
                    else "Appeal Period Expired" if reason == "deadline_expired"
                    else "Appeal Submitted" if reason == "already_submitted"
                    else "Appeal Unavailable")
            button.configure(
                text=text, state="normal" if enabled else "disabled",
                fg_color=SP_APPEAL if enabled else SP_DISABLED,
                hover_color=SP_APPEAL_HOVER, text_color=SP_TEXT,
                text_color_disabled=SP_MUTED,
            )
            outcome = getattr(button, "_appeal_outcome_label", None)
            if outcome is not None and outcome.winfo_exists():
                outcome.configure(text="Student Appeal Window Open" if enabled else
                                  self._appeal_ineligible_message(reason) if reason in ("no_deadline", "resolved") else text,
                                  text_color=SP_WARNING if enabled else SP_MUTED)
            deadline_label = getattr(button, "_appeal_deadline_label", None)
            if deadline_label is not None and deadline_label.winfo_exists():
                deadline_text = f"Appeal deadline: {_display_ts(eligibility.get('deadline'))}"
                if enabled:
                    deadline_text += f"  ·  {remaining_time_text(eligibility.get('deadline'))}"
                deadline_label.configure(text=deadline_text)
            alive.append((button, violation_id))
        self._appeal_buttons = alive

    def _open_appeal_form(self, viol: dict) -> None:
        viol = dict(viol)  # This form owns one selection even if its card is refreshed.
        viol_id = viol.get("id")
        if not viol_id:
            return
        if not viol.get("can_appeal"):
            self._reload_workflow_data()
            self._toast(self._appeal_ineligible_message(
                str(viol.get("appeal_eligibility_reason") or "")), "info")
            return

        modal = ctk.CTkToplevel(self)
        self._evidence_windows.append(modal)
        modal.title(f"Submit Appeal — Violation #{viol_id}")
        modal._violation_id = viol_id
        modal.configure(fg_color=SP_BG)
        modal.geometry("520x660")
        modal.resizable(False, True)
        modal.transient(self)
        self._schedule_ui(120, lambda: modal.winfo_exists() and modal.lift())
        self._schedule_ui(200, lambda: modal.winfo_exists() and modal.grab_set())

        scroll = PortalScrollFrame(modal, color=SP_BG)
        scroll.pack(fill="both", expand=True, padx=24, pady=20)
        inner = scroll.body
        inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(inner, text="Submit Appeal", font=_f(20, "bold"),
                     text_color=SP_TEXT).grid(row=0, column=0, sticky="w", pady=(0, 4))
        metadata = ctk.CTkLabel(inner, text=self._appeal_selection_text(viol), font=_f(12),
            text_color=SP_MUTED, justify='left', wraplength=440)
        metadata.grid(row=1, column=0, sticky='w', pady=(0,16))

        original = ctk.CTkFrame(inner, fg_color=SP_SURFACE)
        original.grid(row=2, column=0, sticky="ew", pady=6)
        original_photo = ctk.CTkLabel(original, text="Loading original detection evidence…",
                                      text_color=SP_MUTED)
        original_photo.pack(pady=4)
        ctk.CTkButton(original, text="View Detection Evidence",
            command=lambda: self._open_snapshot(viol)).pack(pady=4)
        self._load_form_detection(viol_id, original_photo, metadata)
        ctk.CTkLabel(
            inner,
            text=(f"Appeals must be submitted within {_APPEAL_DAYS} days of publication in My Violations. "
                  "You may only submit one appeal per violation. "
                  "Attach one picture and explain your appeal below."),
            font=_f(11), text_color=SP_MUTED, wraplength=440, justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(2, 14))

        ctk.CTkLabel(inner, text="Your Explanation (required)",
                     font=_f(13, "bold"), text_color=SP_TEXT).grid(row=4, column=0, sticky="w")
        reason_box = ctk.CTkTextbox(inner, height=160, corner_radius=8, fg_color=SP_SURFACE,
                                    border_color=SP_BORDER, border_width=1, text_color=SP_TEXT)
        reason_box.grid(row=5, column=0, sticky="ew", pady=(4, 4))
        char_lbl = ctk.CTkLabel(inner, text="0 / 1000 characters", font=_f(10),
                                text_color=SP_MUTED, anchor="e")
        char_lbl.grid(row=6, column=0, sticky="e")

        def _on_key(_event=None) -> None:
            text = reason_box.get("1.0", "end-1c")
            char_lbl.configure(text=f"{len(text)} / 1000 characters")

        reason_box.bind("<KeyRelease>", _on_key)

        # Evidence file upload
        ctk.CTkLabel(inner, text="Attach Evidence Picture (required)",
                     font=_f(13, "bold"), text_color=SP_TEXT).grid(
            row=7, column=0, sticky="w", pady=(14, 0))
        ctk.CTkLabel(inner,
                     text="One JPG, PNG, or BMP picture, up to 10 MB. No PDF or TXT files.",
                     font=_f(11), text_color=SP_MUTED).grid(
            row=8, column=0, sticky="w", pady=(2, 6))

        evidence_state: dict = {"path": None, "data": None, "name": None}
        ev_row = ctk.CTkFrame(inner, fg_color="transparent")
        ev_row.grid(row=9, column=0, sticky="ew")
        ev_lbl = ctk.CTkLabel(ev_row, text="No file selected", font=_f(11),
                              text_color=SP_MUTED, anchor="w")
        ev_lbl.pack(side="left", fill="x", expand=True)

        def _pick_file() -> None:
            path = filedialog.askopenfilename(
                title="Select Evidence File",
                filetypes=[
                    ("Images", "*.jpg *.jpeg *.png *.bmp"),
                ],
            )
            if not path:
                return
            evidence_state['path'] = path
            ev_lbl.configure(text=f"📎 {os.path.basename(path)}", text_color=SP_ACCENT)

        ctk.CTkButton(ev_row, text="Browse…", width=90, height=28, corner_radius=8,
                      fg_color=SP_SURFACE, hover_color=SP_HOVER_LIGHT,
                      text_color=SP_ACCENT, border_width=1, border_color=SP_BORDER,
                      font=_f(11), command=_pick_file).pack(side="right")

        # Row 10: validation error (was incorrectly sharing row=7 with evidence label)
        err = ctk.CTkLabel(inner, text="", font=_f(11), text_color=SP_DANGER,
                          wraplength=430, justify="left")
        err.grid(row=10, column=0, sticky="w", pady=(4, 0))

        def _submit() -> None:
            reason = reason_box.get("1.0", "end-1c").strip()
            if len(reason) < 20:
                err.configure(text="Please provide at least 20 characters of reasoning.")
                return
            if len(reason) > 1000:
                err.configure(text="Reasoning must not exceed 1000 characters.")
                return
            evidence_path = evidence_state["path"]
            if not evidence_path:
                err.configure(text="Attach a valid picture before submitting your appeal.")
                return
            display_name, updates = self.display_name, self._ai_ui_updates
            def save(db, sid):
                with open(evidence_path, 'rb') as source:
                    evidence = (os.path.basename(evidence_path), 'image', source.read(MAX_EVIDENCE_BYTES + 1))
                validate_evidence(*evidence)
                new_id = db.insert_appeal(viol_id, sid, reason, evidence=evidence)
                if new_id is None:
                    latest = db.get_appeal_eligibility(viol_id, sid)
                    raise ValueError('Could not save the appeal and evidence. Please try again.'
                        if latest.get('eligible') else StudentPortal._appeal_ineligible_message(str(latest.get('reason') or '')))
                # Start advisory work even if navigation discards the UI callback.
                analysis_db = CBVMSDatabase(db.db_path, timeout=.75)
                def on_ai_done(recommendation, confidence, analysis):
                    analysis_db.update_appeal_ai_analysis(new_id, recommendation, confidence, analysis)
                    updates.put(new_id)
                try:
                    analyze_appeal(
                    violation_type=viol.get('violation_code') or 'unknown_violation',
                    violation_timestamp=_display_ts(viol.get('timestamp')),
                    student_name=display_name, student_id=sid,
                    reason=reason, on_complete=on_ai_done)
                except Exception:
                    pass  # advisory availability never changes the committed submission
                return new_id

            def saved(result):
                if modal.winfo_exists():
                    modal.destroy()
                self._log_activity('Appeal submitted', f'Appeal for violation #{viol_id}')
                self._show('appeals')
                self._toast('Appeal submitted. AI analysis is running…', 'info')

            if self._run_action(save, saved,
                lambda message: err.configure(text=message) if err.winfo_exists() else None):
                submit_button.configure(state="disabled", text="Submitting…")

        # Row 11: Submit / Cancel buttons (was incorrectly sharing row=8 with the description label)
        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.grid(row=11, column=0, sticky="ew", pady=(16, 8))
        submit_button = ctk.CTkButton(btns, text="Submit Appeal", height=42, corner_radius=8,
                       font=_f(13, "bold"), command=_submit)
        submit_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._appeal_buttons.append((submit_button, viol_id))
        self._refresh_appeal_buttons()
        ctk.CTkButton(btns, text="Cancel", width=110, height=42, corner_radius=8,
                      fg_color=SP_SURFACE, hover_color=SP_HOVER_LIGHT, text_color=SP_TEXT,
                      border_width=1, border_color=SP_BORDER, command=modal.destroy).pack(side="right")

    @staticmethod
    def _appeal_selection_text(violation):
        label = violation.get('violation_label') or violation_display_name(
            violation.get('violation_code'), violation.get('violation_type'))
        return (f"You are appealing violation #{violation['id']}, detected on {_display_ts(violation.get('timestamp'))}.\n"
                f"Type: {label}\nPublished: {_display_ts(violation.get('appeal_opened_at'), fallback='Not recorded (legacy)')}\n"
                f"Appeal deadline: {_display_ts(violation.get('appeal_deadline'))}")

    def _load_form_detection(self, violation_id, label, metadata=None):
        """Each form has a fixed record ID, owner, and independent response channel."""
        owner, database = self.student_id, CBVMSDatabase(self.db.db_path, timeout=.75)
        label._violation_id = violation_id
        results = queue.Queue(maxsize=1)
        def read():
            try:
                row = database.get_student_original_evidence(violation_id, owner)
                results.put((original_evidence(row, size=(260, 120)), row, None))
            except Exception as exc:
                results.put((None, None, str(exc)))
        def deliver():
            if self._closed or not label.winfo_exists() or owner != self.student_id or label._violation_id != violation_id:
                return
            try:
                source, row, error = results.get_nowait()
            except queue.Empty:
                self._schedule_ui(50, deliver)
                return
            if source:
                if metadata is not None and metadata.winfo_exists():
                    metadata.configure(text=self._appeal_selection_text(row))
                label._evidence_key = source['key']
                label._evidence_image = source['image']
                picture = source['image']
                if picture:
                    label._ref = ctk.CTkImage(picture, size=picture.size)
                    label.configure(text=source['label'], image=label._ref, compound='top', wraplength=420)
                    return
            label.configure(image=None, text=(source['warning'] if source else error) or 'Original evidence unavailable', wraplength=420)
        threading.Thread(target=read, daemon=True, name='appeal-detection-preview').start()
        self._schedule_ui(50, deliver)

    def _open_snapshot(self, viol: dict) -> None:
        violation_id = int(viol['id'])
        modal = ctk.CTkToplevel(self)
        self._evidence_windows.append(modal)
        modal.title(f"Original detection evidence — Violation #{violation_id}")
        modal.configure(fg_color=SP_BG)
        modal.geometry('850x700')
        modal.transient(self)
        label = ctk.CTkLabel(modal, text='Loading original detection evidence…', wraplength=760)
        label.pack(fill='both', expand=True, padx=20, pady=20)
        def load(db, sid):
            return original_evidence(db.get_student_original_evidence(violation_id, sid), size=(780, 520))
        def display(source):
            if not modal.winfo_exists() or source['key'][:2] != ('violation', violation_id):
                return
            modal._evidence_key = label._evidence_key = source['key']
            modal._evidence_image = source['image']
            if source['image']:
                label._ref = ctk.CTkImage(source['image'], size=source['image'].size)
                label.configure(image=label._ref, text=source['label'], compound='top')
            else:
                label.configure(image=None, text=source['warning'])
        def failed(message):
            if label.winfo_exists():
                label.configure(image=None, text=f'Original evidence unavailable: {message}')
        def retry():
            label.configure(image=None, text='Loading original detection evidence…')
            self._run_action(load, display, failed)
        ctk.CTkButton(modal, text='Reload image', command=retry).pack(pady=4)
        ctk.CTkButton(modal, text='Close', command=modal.destroy).pack(pady=8)
        retry()

    # ------------------------------------------------------------------
    # Notifications panel
    # ------------------------------------------------------------------

    def _panel_notifications(self) -> None:
        self._log_activity("Notifications viewed", "Opened the Notifications page")
        # The background snapshot supplies notifications and the badge together.
        unread = self._unread_count

        scroll = self._scroll_host(
            "Notifications",
            "Alerts about detected violations and system updates for your account.",
        )

        # Toolbar
        bar = ctk.CTkFrame(scroll, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=30, pady=(8, 10))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text=f"{len(self._notifications)} on this page · {unread} unread",
                     font=_f(12, "bold"), text_color=SP_WHITE, fg_color=SP_ACCENT,
                     corner_radius=999, padx=12, pady=3).grid(row=0, column=0, sticky="w")
        if unread:
            ctk.CTkButton(bar, text="Mark all as read", width=140, height=32, corner_radius=8,
                          fg_color=SP_SURFACE, hover_color=SP_HOVER_LIGHT,
                          text_color=SP_ACCENT, border_width=1, border_color=SP_BORDER,
                          font=_f(12), command=self._mark_all_read).grid(row=0, column=1, sticky="e")

        list_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        list_frame.grid(row=2, column=0, sticky="ew", padx=30, pady=(0, 26))
        list_frame.grid_columnconfigure(0, weight=1)

        if not self._notifications:
            self._empty_notif_state(list_frame)
            return
        for i, notif in enumerate(self._notifications):
            self._notification_row(list_frame, i, notif)
        self._pagination(scroll, 3)

    def _empty_notif_state(self, parent) -> None:
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="ew", pady=30)
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(wrap, text="🔔", font=_f(40)).grid(row=0, column=0)
        ctk.CTkLabel(wrap, text="No notifications", font=_f(15, "bold"),
                     text_color=SP_TEXT).grid(row=1, column=0, pady=(6, 2))
        ctk.CTkLabel(wrap, text="You have no notifications yet.", font=_f(12),
                     text_color=SP_MUTED).grid(row=2, column=0)

    def _notification_row(self, parent, row: int, notif: dict) -> None:
        is_read = bool(notif.get("is_read"))
        bg = SP_SURFACE if is_read else SP_HOVER_LIGHT
        card = ctk.CTkFrame(parent, fg_color=bg, corner_radius=10,
                            border_width=1, border_color=SP_BORDER)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        card.grid_columnconfigure(1, weight=1)

        # Unread indicator dot
        dot_color = SP_ACCENT if not is_read else "transparent"
        ctk.CTkFrame(card, width=10, height=10, fg_color=dot_color,
                     corner_radius=999).grid(row=0, column=0, rowspan=2,
                                             padx=(14, 10), pady=14, sticky="n")

        ctk.CTkLabel(card, text=notif.get("title", "—"), font=_f(13, "bold"),
                     text_color=SP_TEXT, anchor="w").grid(row=0, column=1, sticky="w",
                                                          pady=(12, 0))
        dt = _parse_ts(notif.get("created_at", ""))
        ts = dt.strftime("%b %d, %Y · %H:%M") if dt else str(notif.get("created_at", ""))
        ctk.CTkLabel(card, text=ts, font=_f(10), text_color=SP_MUTED, anchor="e").grid(
            row=0, column=2, sticky="e", padx=(0, 14), pady=(12, 0))
        ctk.CTkLabel(card, text=notif.get("message", ""), font=_f(12),
                     text_color=SP_MUTED, anchor="w", wraplength=700,
                     justify="left").grid(row=1, column=1, columnspan=2, sticky="w",
                                          padx=(0, 14), pady=(2, 12))

        if not is_read:
            ctk.CTkButton(card, text="Mark read", width=90, height=26, corner_radius=8,
                          fg_color="transparent", hover_color=SP_HOVER_LIGHT,
                          text_color=SP_ACCENT, font=_f(11),
                          command=lambda nid=notif['id']: self._mark_read(nid)).grid(row=0, column=3, padx=14, pady=12)
        if notif.get('violation_id'):
            ctk.CTkButton(card, text='View related record', width=140, height=26,
                          command=lambda vid=notif['violation_id']: self._open_related(vid)).grid(row=2, column=1, sticky='w', pady=(0,12))
        violation = self._notification_violations.get(notif.get('violation_id'))
        if violation is not None and not violation.get('appeal_id'):
            self._make_appeal_button(card, violation).grid(
                row=3, column=1, columnspan=2, sticky='w', pady=(0, 12))

    def _open_related(self, violation_id):
        self._focused_violation = int(violation_id)
        self._page_offset = 0
        self._violation_filter = 'All'
        self._show('violations', preserve_page=True)

    def _mark_read(self, notification_id):
        self._run_action(lambda db, sid: db.mark_notification_read(notification_id, student_id=sid),
                         lambda _: self._reload_workflow_data())

    def _mark_all_read(self) -> None:
        self._run_action(lambda db, sid: db.mark_all_notifications_read(sid),
                         lambda _: self._reload_workflow_data())

    # ------------------------------------------------------------------
    # My Appeals panel
    # ------------------------------------------------------------------

    def _panel_appeals(self) -> None:
        self._log_activity("Appeals viewed", "Opened the My Appeals page")

        scroll = self._scroll_host(
            "My Appeals",
            f"Track the status of your submitted appeals. "
            f"Appeals must be submitted within {_APPEAL_DAYS} days of publication in My Violations. "
            "AI recommendations are advisory; the administrator makes the final decision.",
        )

        bar = ctk.CTkFrame(scroll, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=30, pady=(8, 10))
        ctk.CTkLabel(bar, text=f"{len(self._appeals)} appeal(s) on this page",
                     font=_f(12, "bold"), text_color=SP_WHITE, fg_color=SP_ACCENT,
                     corner_radius=999, padx=12, pady=3).pack(side="left")

        list_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        list_frame.grid(row=2, column=0, sticky="ew", padx=30, pady=(0, 26))
        list_frame.grid_columnconfigure(0, weight=1)

        if not self._appeals:
            self._empty_appeals_state(list_frame)
            return
        if self._focused_appeal_id:
            ctk.CTkButton(bar, text="All My Appeals", command=lambda: self._show('appeals')).pack(side="right")
        rows = [a for a in self._appeals if not self._focused_appeal_id or a['id'] == self._focused_appeal_id]
        for i, appeal in enumerate(rows):
            self._appeal_card(list_frame, i, appeal)
        self._pagination(scroll, 3)

    def _empty_appeals_state(self, parent) -> None:
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="ew", pady=30)
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(wrap, text="📝", font=_f(40)).grid(row=0, column=0)
        ctk.CTkLabel(wrap, text="No appeals submitted", font=_f(15, "bold"),
                     text_color=SP_TEXT).grid(row=1, column=0, pady=(6, 2))
        ctk.CTkLabel(
            wrap,
            text=f"If you believe a violation was recorded in error, go to My Violations "
                 f"and click 'Submit Appeal' within {_APPEAL_DAYS} days of publication in My Violations.",
            font=_f(12), text_color=SP_MUTED, wraplength=500, justify="center",
        ).grid(row=2, column=0)
        ctk.CTkButton(wrap, text="Go to My Violations", width=160, height=36, corner_radius=8,
                      fg_color=SP_ACCENT, hover_color=SP_ACCENT_HOVER, text_color=SP_WHITE,
                      font=_f(13), command=lambda: self._show("violations")).grid(row=3, column=0,
                                                                                   pady=(14, 0))

    def _view_appeal(self, appeal_id):
        # Exact owner-scoped lookup survives inserts, filtering, and pagination changes.
        self._show('appeals')
        self._focused_appeal_id = appeal_id
        self._page_offset = 0
        if self._request:
            self._request.cancelled.set()
            self._request = None
        self._reload_workflow_data()

    def _open_appeal_evidence(self, appeal_id):
        modal = ctk.CTkToplevel(self)
        self._evidence_windows.append(modal)
        modal.title(f"Supporting Image — Appeal #{appeal_id}")
        modal.geometry("700x600")
        label = ctk.CTkLabel(modal, text="Loading supporting image…")
        label.pack(fill="both", expand=True, padx=16, pady=16)
        def load(db, sid):
            rows = db.get_student_appeal_evidence(appeal_id, sid)
            if not rows:
                raise ValueError("Supporting image is unavailable.")
            return supporting_evidence(rows[0], size=(650, 520))
        def show(source):
            if modal.winfo_exists() and source['key'][:2] == ('appeal', appeal_id):
                modal._evidence_key = source['key']
                modal._evidence_image = source['image']
                if source['image']:
                    label._ref = ctk.CTkImage(source['image'], size=source['image'].size)
                    label.configure(text=source['label'], image=label._ref, compound='top', wraplength=620)
                else:
                    label.configure(text=source['warning'], image=None, wraplength=620)
        self._run_action(load, show,
            lambda error: label.configure(text=error) if label.winfo_exists() else None)

    def _appeal_card(self, parent, row: int, appeal: dict) -> None:
        status = (appeal.get("status") or "pending").lower()
        status_colors = {
            "pending": SP_WARNING,
            "approved": SP_SAFE,
            "rejected": SP_DANGER,
        }
        bar_color = status_colors.get(status, SP_MUTED)

        card = self._card(parent)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkFrame(card, width=4, fg_color=bar_color,
                     corner_radius=8).grid(row=0, column=0, rowspan=6,
                                           sticky="nsw", padx=(0, 14), pady=2)

        vtype = violation_display_name(
            appeal.get("violation_code"), appeal.get("violation_type"))
        ctk.CTkLabel(card, text=f"Appeal #{appeal['id']} · Violation #{appeal['violation_id']} · {vtype}", font=_f(14, "bold"),
                     text_color=SP_TEXT, anchor="w").grid(row=0, column=1, sticky="w",
                                                          pady=(12, 0))

        self._appeal_status_pill(card, status).grid(row=0, column=2, sticky="e",
                                                     padx=(0, 16), pady=(12, 0))

        meta = (
            f"Detected: {_display_ts(appeal.get('violation_ts'))}  ·  "
            f"Published: {_display_ts(appeal.get('appeal_opened_at'))}\n"
            f"Appeal deadline: {_display_ts(appeal.get('appeal_deadline'))}  ·  "
            f"Submitted: {_display_ts(appeal.get('submitted_at'))}"
        )
        ctk.CTkLabel(card, text=meta, font=_f(11), text_color=SP_MUTED,
                     anchor="w", justify="left", wraplength=720).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(0, 16), pady=(2, 0))

        outcomes = {
            "pending": ("Pending Admin Decision — No strike while appeal is pending", SP_WARNING, SP_PILL_WARN_BG),
            "approved": ("Appeal Approved — No active strike", SP_SAFE, SP_PILL_OK_BG),
            "rejected": ("Appeal Rejected — One finalized strike", SP_DANGER, "#FEE2E2"),
        }
        if appeal.get("strike_active") and status == "pending":
            outcomes["pending"] = ("Pending decision — historical strike requires reconciliation", SP_WARNING, SP_PILL_WARN_BG)
        outcome_text, outcome_color, outcome_bg = outcomes.get(
            status, (f"Appeal {status.title()}", SP_MUTED, SP_BORDER))
        ctk.CTkLabel(card, text=outcome_text, font=_f(12, "bold"),
                     text_color=outcome_color, fg_color=outcome_bg, corner_radius=8,
                     anchor="w", padx=10, pady=5).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=(8, 0))

        # Reason box (read-only display)
        reason_frame = ctk.CTkFrame(card, fg_color=SP_BG, corner_radius=8)
        reason_frame.grid(row=3, column=1, columnspan=2, sticky="ew",
                          padx=(0, 16), pady=(8, 0))
        ctk.CTkLabel(reason_frame, text="Your reasoning:", font=_f(11, "bold"),
                     text_color=SP_MUTED, anchor="w").pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(reason_frame, text=appeal.get("reason", "—"), font=_f(12),
                     text_color=SP_TEXT, anchor="w", wraplength=680,
                     justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        ctk.CTkButton(reason_frame, text="View Supporting Image",
            command=lambda aid=appeal["id"]: self._open_appeal_evidence(aid)).pack(anchor="w", padx=12, pady=6)

        ctk.CTkButton(reason_frame, text="View Original Detection Evidence",
            command=lambda vid=appeal['violation_id']: self._open_snapshot({'id': vid})).pack(anchor='w', padx=12, pady=6)

        # AI recommendation section
        ai_rec = (appeal.get("ai_recommendation") or "").strip()
        ai_conf = (appeal.get("ai_confidence") or "").strip()
        ai_text = (appeal.get("ai_analysis") or "").strip()
        ai_analyzed_at = (appeal.get("ai_analyzed_at") or "").strip()

        ai_frame = ctk.CTkFrame(card, fg_color=SP_BG, corner_radius=8)
        ai_frame.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=(8, 0))

        ai_header = ctk.CTkFrame(ai_frame, fg_color="transparent")
        ai_header.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(ai_header, text="🤖  AI Recommendation (Advisory Only)",
                     font=_f(12, "bold"),
                     text_color=SP_TEXT, anchor="w").pack(side="left")

        if ai_rec:
            rec_color = SP_SAFE if "Valid" in ai_rec else SP_DANGER if "Invalid" in ai_rec else SP_MUTED
            rec_bg = SP_PILL_OK_BG if "Valid" in ai_rec else "#FEE2E2" if "Invalid" in ai_rec else SP_BORDER
            pill_text = f"{ai_rec}  ·  {ai_conf}" if ai_conf and ai_conf != "—" else ai_rec
            ctk.CTkLabel(ai_header, text=pill_text, font=_f(11, "bold"),
                         text_color=rec_color, fg_color=rec_bg,
                         corner_radius=999, padx=10, pady=2).pack(side="right")
            if ai_text:
                ctk.CTkLabel(ai_frame, text=ai_text, font=_f(13), text_color=SP_TEXT,
                             anchor="w", wraplength=660,
                             justify="left").pack(anchor="w", padx=12, pady=(4, 6))
            if ai_analyzed_at:
                ctk.CTkLabel(ai_frame, text=f"Analyzed: {_display_ts(ai_analyzed_at)}",
                             font=_f(11),
                             text_color=SP_MUTED, anchor="w").pack(anchor="w", padx=12, pady=(0, 6))
        else:
            ctk.CTkLabel(ai_frame, text="Advisory analysis pending…", font=_f(12),
                         text_color=SP_TEXT, anchor="w").pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkFrame(ai_frame, fg_color="transparent", height=4).pack()

        # Admin notes (if any)
        notes = (appeal.get("admin_notes") or "").strip()
        if notes:
            notes_frame = ctk.CTkFrame(card, fg_color=SP_PILL_OK_BG if status == "approved"
                                       else "#FEE2E2" if status == "rejected" else SP_PILL_WARN_BG,
                                       corner_radius=8)
            notes_frame.grid(row=5, column=1, columnspan=2, sticky="ew",
                             padx=(0, 16), pady=(6, 12))
            ctk.CTkLabel(notes_frame, text=f"Admin response · {appeal.get('decided_by') or 'Administrator'} · {_display_ts(appeal.get('decided_at'))}:", font=_f(11, "bold"),
                         text_color=SP_MUTED, anchor="w").pack(anchor="w", padx=12, pady=(8, 2))
            ctk.CTkLabel(notes_frame, text=notes, font=_f(12), text_color=SP_TEXT,
                         anchor="w", wraplength=680, justify="left").pack(anchor="w",
                                                                           padx=12, pady=(0, 8))
        else:
            ctk.CTkFrame(card, fg_color="transparent", height=10).grid(row=5, column=1,
                                                                         pady=(0, 4))

    # ------------------------------------------------------------------
    # My Profile panel
    # ------------------------------------------------------------------

    def _panel_profile(self) -> None:
        scroll = self._scroll_host("User Profile", "View and update your account information.")

        card = self._card(scroll)
        card.grid(row=1, column=0, sticky="ew", padx=30, pady=(14, 12))
        card.grid_columnconfigure(1, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(18, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Profile Information", font=_f(17, "bold"),
                     text_color=SP_TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="Update Profile", width=130, height=34, corner_radius=8,
                      fg_color=SP_ACCENT, hover_color=SP_ACCENT_HOVER, text_color=SP_WHITE,
                      font=_f(13), command=self._open_update_profile).grid(row=0, column=1, sticky="e")

        # Photo
        photo_column = ctk.CTkFrame(card, fg_color="transparent")
        photo_column.grid(row=1, column=0, sticky="nw", padx=20, pady=(0, 16))
        photo_box = ctk.CTkFrame(photo_column, fg_color=SP_PLACEHOLDER_BG, corner_radius=12,
                                 width=200, height=200)
        photo_box.pack()
        photo_box.grid_propagate(False)
        photo = ctk.CTkImage(light_image=self._profile_image, dark_image=self._profile_image,
                            size=self._profile_image.size) if self._profile_image is not None else None
        if photo is not None:
            self._image_refs.append(photo)
        if photo is not None:
            lbl = ctk.CTkLabel(photo_box, image=photo, text="", fg_color=SP_PLACEHOLDER_BG)
            lbl._img_ref = photo
            lbl.place(relx=0.5, rely=0.5, anchor="center")
        else:
            ph = ctk.CTkFrame(photo_box, fg_color="transparent")
            ph.place(relx=0.5, rely=0.5, anchor="center")
            ctk.CTkLabel(ph, text="👤", font=_f(40), text_color=SP_MUTED).pack()
            ctk.CTkLabel(ph, text="No photo uploaded", font=_f(11), text_color=SP_MUTED).pack()

        ctk.CTkButton(photo_column, text="Change Photo", width=160, height=34,
                      corner_radius=8, fg_color=SP_ACCENT, hover_color=SP_ACCENT_HOVER,
                      text_color=SP_WHITE, font=_f(13),
                      command=self._change_profile_photo).pack(pady=(12, 0))

        # Fields
        fields = ctk.CTkFrame(card, fg_color="transparent")
        fields.grid(row=1, column=1, sticky="new", padx=(10, 20), pady=(0, 16))
        fields.grid_columnconfigure(0, weight=1)
        s = self._student
        enr = _parse_ts(s.get("enrolled_at", ""))
        rows = [
            ("STUDENT ID", s.get("student_id", self.student_id)),
            ("FULL NAME", s.get("name", "—")),
            ("COURSE", s.get("course") or "—"),
            ("YEAR & SECTION", s.get("year_and_section") or "—"),
            ("GENDER", s.get("gender") or "—"),
            ("ACADEMIC STATUS", standing_label(s)),
            ("SUSPENSION", suspension_label(self._active_suspension)),
            *((label.upper(), s.get(key) or "—") for label, key in CONTACT_FIELDS),
            ("ENROLLED ON", enr.strftime("%b %d, %Y") if enr else (s.get("enrolled_at") or "—")),
        ]
        for i, (label, value) in enumerate(rows):
            ctk.CTkLabel(fields, text=label, font=_f(10, "bold"), text_color=SP_MUTED,
                         anchor="w").grid(row=i * 2, column=0, sticky="w", pady=(8 if i else 0, 0))
            value_label = ctk.CTkLabel(fields, text=str(value), font=_f(14, "bold"), text_color=SP_TEXT,
                         anchor="w", wraplength=500, justify="left")
            value_label.grid(row=i * 2 + 1, column=0, sticky="w")
            if label in ("ACADEMIC STATUS", "SUSPENSION"):
                self._standing_labels.append((value_label, "status" if label == "ACADEMIC STATUS" else "suspension"))

        # Change Password section
        pwd = ctk.CTkFrame(card, fg_color="transparent")
        pwd.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 20))
        ctk.CTkFrame(pwd, height=1, fg_color=SP_BORDER).pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(pwd, text="Change Password", font=_f(15, "bold"),
                     text_color=SP_TEXT).pack(anchor="w")
        ctk.CTkLabel(pwd, text="Enter your current password then choose a new one.",
                     font=_f(12), text_color=SP_MUTED).pack(anchor="w", pady=(2, 12))

        pw_form = ctk.CTkFrame(pwd, fg_color="transparent")
        pw_form.pack(fill="x")
        pw_form.grid_columnconfigure((0, 1, 2), weight=1, uniform="pwcol")

        def _pw_col(col, label, show="•"):
            ctk.CTkLabel(pw_form, text=label, font=_f(11), text_color=SP_MUTED,
                         anchor="w").grid(row=0, column=col, sticky="w",
                                          padx=(0 if col == 0 else 8, 8), pady=(0, 4))
            e = ctk.CTkEntry(pw_form, show=show, height=38, corner_radius=8,
                             fg_color=SP_SURFACE, border_color=SP_BORDER,
                             text_color=SP_TEXT, border_width=1)
            e.grid(row=1, column=col, sticky="ew",
                   padx=(0 if col == 0 else 8, 8), pady=(0, 6))
            return e

        e_cur  = _pw_col(0, "Current Password")
        e_new  = _pw_col(1, "New Password")
        e_conf = _pw_col(2, "Confirm New Password")

        pw_msg = ctk.CTkLabel(pwd, text="", font=_f(11), text_color=SP_DANGER, anchor="w")
        pw_msg.pack(anchor="w", pady=(0, 6))

        def _save_pw() -> None:
            cur  = e_cur.get()
            new  = e_new.get()
            conf = e_conf.get()
            if not cur or not new or not conf:
                pw_msg.configure(text="Please fill in all three fields.", text_color=SP_DANGER)
                return
            if len(new) < 6:
                pw_msg.configure(text="New password must be at least 6 characters.",
                                 text_color=SP_DANGER)
                return
            if new != conf:
                pw_msg.configure(text="New password and confirmation do not match.",
                                 text_color=SP_DANGER)
                return
            if cur == new:
                pw_msg.configure(text="New password must be different from current.",
                                 text_color=SP_DANGER)
                return
            def save(db, sid):
                with db.connect() as conn:
                    row = conn.execute('SELECT username FROM student_accounts WHERE student_id = ?', (sid,)).fetchone()
                if row is None or db.verify_student_account(row[0], cur) is None:
                    raise ValueError('Current password is incorrect.')
                if not db.reset_student_password(sid, new):
                    raise ValueError('Failed to save new password. Please try again.')
            def saved(_):
                for entry in (e_cur, e_new, e_conf):
                    entry.delete(0, 'end')
                pw_msg.configure(text='✓  Password changed successfully.', text_color=SP_SAFE)
                self._page_status.configure(text='Up to date', text_color=SP_MUTED)
                self._log_activity('Password changed', 'Student updated their account password')
            self._run_action(save, saved, lambda message: pw_msg.configure(text=message))

        btn_row = ctk.CTkFrame(pwd, fg_color="transparent")
        btn_row.pack(anchor="e")
        ctk.CTkButton(btn_row, text="Save Password", height=38, width=160,
                      corner_radius=8, fg_color=SP_ACCENT, hover_color=SP_ACCENT_HOVER,
                      text_color=SP_WHITE, font=_f(13, "bold"),
                      command=_save_pw).pack(side="right")

        self._activity_log_card(scroll, row=2)

    def _change_profile_photo(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Choose Profile Photo",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp")],
        )
        if not path:
            return
        def save(db, sid):
            if os.path.getsize(path) > 10 * 1024 * 1024:
                raise ValueError('Please choose an image smaller than 10 MB.')
            with Image.open(path) as source:
                if source.width * source.height > 20_000_000:
                    raise ValueError('Please choose an image under 20 megapixels.')
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((800, 800), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=90)
            if not db.update_student_profile_photo(sid, output.getvalue()):
                raise ValueError('Could not save your profile photo.')
        def saved(_):
            self._log_activity('Profile photo changed', 'Student updated their profile photo')
            self._show('profile')
            self._toast('Profile photo updated.')
        self._run_action(save, saved)

    def _open_update_profile(self) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Update Profile")
        modal.configure(fg_color=SP_BG)
        modal.geometry("420x320")
        modal.resizable(False, False)
        modal.transient(self)
        self._schedule_ui(120, lambda: modal.winfo_exists() and modal.lift())
        self._schedule_ui(200, lambda: modal.winfo_exists() and modal.grab_set())

        ctk.CTkLabel(modal, text="Update Profile", font=_f(18, "bold"),
                     text_color=SP_TEXT).pack(anchor="w", padx=20, pady=(18, 10))
        body = ctk.CTkFrame(modal, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20)

        ctk.CTkLabel(body, text="FULL NAME", font=_f(10, "bold"), text_color=SP_MUTED).pack(anchor="w")
        name_entry = ctk.CTkEntry(body, height=38, corner_radius=8, fg_color=SP_SURFACE,
                                  border_color=SP_BORDER, text_color=SP_TEXT)
        name_entry.insert(0, self._student.get("name", ""))
        name_entry.pack(fill="x", pady=(4, 12))

        for label, value in (("STUDENT ID", self.student_id),
                             ("COURSE", self._student.get("course") or "—"),
                             ("YEAR & SECTION", self._student.get("year_and_section") or "—")):
            ctk.CTkLabel(body, text=f"{label}: {value}  (read-only)", font=_f(11),
                         text_color=SP_MUTED).pack(anchor="w", pady=1)

        err = ctk.CTkLabel(body, text="", font=_f(11), text_color=SP_DANGER)
        err.pack(anchor="w", pady=(6, 0))

        def _save() -> None:
            new = name_entry.get().strip()
            if not new:
                err.configure(text="Full name cannot be empty.")
                return
            def save(db, sid):
                if not db.update_student_name(sid, new):
                    raise ValueError('Could not update profile.')
            def saved(_):
                self._log_activity('Profile updated', 'Updated full name')
                if modal.winfo_exists():
                    modal.destroy()
                self._toast('Profile updated successfully.')
                self._show('profile')
            self._run_action(save, saved,
                lambda message: err.configure(text=message) if err.winfo_exists() else None)

        btns = ctk.CTkFrame(modal, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(8, 16))
        ctk.CTkButton(btns, text="Save", height=38, corner_radius=8, fg_color=SP_ACCENT,
                      hover_color=SP_ACCENT_HOVER, text_color=SP_WHITE, command=_save).pack(
            side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(btns, text="Cancel", width=110, height=38, corner_radius=8,
                      fg_color=SP_SURFACE, hover_color=SP_HOVER_LIGHT, text_color=SP_TEXT,
                      border_width=1, border_color=SP_BORDER, command=modal.destroy).pack(side="right")

    def _activity_log_card(self, parent, row: int) -> None:
        card = self._card(parent)
        card.grid(row=row, column=0, sticky="ew", padx=30, pady=(0, 26))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="System Activity Log", font=_f(17, "bold"),
                     text_color=SP_TEXT).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(card, text="Your sign-ins, profile changes, report submissions, and other "
                                "actions on SECURE (view only).",
                     font=_f(12), text_color=SP_MUTED, justify="left", wraplength=720).grid(
            row=1, column=0, sticky="w", padx=20, pady=(0, 10))
        for i, evt in enumerate(reversed(self._activity_log), start=2):
            rf = ctk.CTkFrame(card, fg_color=SP_BG, corner_radius=8)
            rf.grid(row=i, column=0, sticky="ew", padx=20, pady=(0, 8))
            rf.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(rf, text=evt["action"], font=_f(13, "bold"), text_color=SP_TEXT,
                         anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
            ts = evt["ts"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(evt["ts"], datetime) else str(evt["ts"])
            ctk.CTkLabel(rf, text=ts, font=_f(11), text_color=SP_MUTED, anchor="e").grid(
                row=0, column=1, sticky="e", padx=12, pady=(8, 0))
            ctk.CTkLabel(rf, text=evt["detail"], font=_f(11), text_color=SP_MUTED,
                         anchor="w").grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
        ctk.CTkFrame(card, fg_color="transparent", height=4).grid(row=len(self._activity_log) + 2, column=0)

    # ------------------------------------------------------------------
    # User Settings panel
    # ------------------------------------------------------------------

    def _panel_settings(self) -> None:
        scroll = self._scroll_host("User Settings",
                                   "Customize how the SECURE portal looks and behaves for your account.")

        appearance = self._card(scroll)
        appearance.grid(row=1, column=0, sticky="ew", padx=30, pady=(14, 12))
        appearance.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(appearance, text="Appearance", font=_f(17, "bold"),
                     text_color=SP_TEXT).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))
        self._setting_toggle(appearance, 1, "Dark mode",
                             "Use a darker color scheme across the portal", self._toggle_dark,
                             self._prefs.get("dark_mode", False))
        self._setting_toggle(appearance, 2, "Compact sidebar",
                             "Reduce sidebar spacing for more workspace", self._toggle_compact, self._compact)

        notif = self._card(scroll)
        notif.grid(row=2, column=0, sticky="ew", padx=30, pady=(0, 26))
        notif.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(notif, text="Notifications", font=_f(17, "bold"),
                     text_color=SP_TEXT).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))
        self._setting_toggle(notif, 1, "Email notifications",
                             "Receive email alerts for violations and system updates",
                             self._toggle_email, self._prefs.get("email_notifications", True))

    def _setting_toggle(self, parent, row, title, subtitle, command, initial) -> None:
        rowf = ctk.CTkFrame(parent, fg_color=SP_BG, corner_radius=8)
        rowf.grid(row=row, column=0, sticky="ew", padx=20, pady=(0, 10))
        rowf.grid_columnconfigure(0, weight=1)
        text = ctk.CTkFrame(rowf, fg_color="transparent")
        text.grid(row=0, column=0, sticky="w", padx=14, pady=12)
        ctk.CTkLabel(text, text=title, font=_f(13, "bold"), text_color=SP_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(text, text=subtitle, font=_f(11), text_color=SP_MUTED, anchor="w").pack(anchor="w")
        var = ctk.BooleanVar(value=bool(initial))
        chk = ctk.CTkCheckBox(rowf, text="", variable=var, width=24, onvalue=True, offvalue=False,
                              fg_color=SP_ACCENT, hover_color=SP_ACCENT_HOVER,
                              command=lambda: command(var.get()))
        chk.grid(row=0, column=1, sticky="e", padx=14)

    def _toggle_dark(self, on: bool) -> None:
        self._prefs["dark_mode"] = bool(on)
        self.db.set_portal_preferences(self.student_id, {"dark_mode": bool(on)})
        ctk.set_appearance_mode("dark" if on else "light")

    def _toggle_compact(self, on: bool) -> None:
        self._compact = on
        self.db.set_portal_preferences(self.student_id, {"compact_sidebar": bool(on)})
        self._sidebar.configure(width=_SIDEBAR_COMPACT if on else _SIDEBAR_FULL)
        if on:
            self._subtitle.pack_forget()
        else:
            self._subtitle.pack(anchor="w", pady=(6, 0))

    def _toggle_email(self, on: bool) -> None:
        self._prefs["email_notifications"] = on
        self.db.set_portal_preferences(self.student_id, {"email_notifications": bool(on)})

    # ------------------------------------------------------------------
    # System Report panel
    # ------------------------------------------------------------------

    def _panel_report(self) -> None:
        scroll = self._scroll_host(
            "System Report",
            "Submit an issue report about the SECURE system to the administrator. "
            "Use this for bugs, account problems, security concerns, or other platform issues.",
        )
        card = self._card(scroll)
        card.grid(row=1, column=0, sticky="ew", padx=30, pady=(14, 26))
        card.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)
        inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            inner, text=f"Reporting as: {self.display_name} ({self.student_id})",
            font=_f(12), text_color=SP_MUTED,
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        ctk.CTkLabel(inner, text="Report title", font=_f(13, "bold"),
                     text_color=SP_TEXT).grid(row=1, column=0, sticky="w")
        title_entry = ctk.CTkEntry(inner, height=40, corner_radius=8, fg_color=SP_SURFACE,
                                   border_color=SP_BORDER, text_color=SP_TEXT,
                                   placeholder_text="Brief summary of the issue")
        title_entry.grid(row=2, column=0, sticky="ew", pady=(4, 14))

        ctk.CTkLabel(inner, text="Category", font=_f(13, "bold"),
                     text_color=SP_TEXT).grid(row=3, column=0, sticky="w")
        category = ctk.CTkOptionMenu(inner, values=_REPORT_CATEGORIES, height=40, corner_radius=8,
                                     fg_color=SP_SURFACE, button_color=SP_BORDER,
                                     button_hover_color=SP_HOVER_LIGHT, text_color=SP_TEXT,
                                     dropdown_fg_color=SP_SURFACE, dropdown_text_color=SP_TEXT,
                                     dropdown_hover_color=SP_HOVER_LIGHT)
        category.set(_REPORT_CATEGORIES[0])
        category.grid(row=4, column=0, sticky="ew", pady=(4, 14))

        ctk.CTkLabel(inner, text="Description", font=_f(13, "bold"),
                     text_color=SP_TEXT).grid(row=5, column=0, sticky="w")
        desc = ctk.CTkTextbox(inner, height=120, corner_radius=8, fg_color=SP_SURFACE,
                              border_color=SP_BORDER, border_width=1, text_color=SP_TEXT)
        desc.grid(row=6, column=0, sticky="ew", pady=(4, 6))

        err = ctk.CTkLabel(inner, text="", font=_f(11), text_color=SP_DANGER)
        err.grid(row=7, column=0, sticky="w", pady=(0, 6))

        def _submit() -> None:
            title = title_entry.get().strip()
            description = desc.get("1.0", "end").strip()
            if not title or not description:
                err.configure(text="Please provide both a title and a description.")
                return
            report_category, display_name = category.get(), self.display_name
            def save(db, sid):
                if not db.insert_system_report(sid, display_name, report_category, title, description):
                    raise ValueError('Could not submit the report. Try again.')
            def saved(_):
                self._log_activity('System report submitted', f'Report: {title}')
                title_entry.delete(0, 'end')
                desc.delete('1.0', 'end')
                err.configure(text='')
                self._page_status.configure(text='Up to date', text_color=SP_MUTED)
                self._toast('Report submitted successfully.')
            self._run_action(save, saved, lambda message: err.configure(text=message))

        ctk.CTkButton(inner, text="Send Report to Admin", height=42, corner_radius=8,
                      fg_color=SP_SIDEBAR, hover_color=SP_ACCENT, text_color=SP_WHITE,
                      font=_f(13, "bold"), command=_submit).grid(row=8, column=0, sticky="w", pady=(6, 0))

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def _logout(self) -> None:
        self._log_activity("Logged out", "Signed out of SECURE")
        self.logged_out = True
        self._stop_refresh()
        self.destroy()

    def _on_close(self) -> None:
        self.logged_out = False
        self._stop_refresh()
        self.destroy()

    def _stop_refresh(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._page_state = 'cancelled'
        if self._request:
            self._request.state = 'cancelled'
        self._refresh.close()
        self._action_callback = self._action_error_callback = None
        for job in self._ui_jobs:
            self.after_cancel(job)
        self._ui_jobs.clear()
        if self._render_job:
            self.after_cancel(self._render_job)
            self._render_job = None
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None

    def destroy(self):
        self._stop_refresh()
        super().destroy()
