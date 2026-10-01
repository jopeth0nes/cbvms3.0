"""Main CBVMS dashboard with live camera feed."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import date, datetime

import cv2
import customtkinter as ctk
import numpy as np

from core.camera import CAMERA_DEVICE_LOCK, CameraCapture
from core.discipline import local_calendar_day_utc_bounds
from core.notifier import Notifier
from core.person_detector import PersonDetector
from core.recognizer import FaceRecognizer
from dataclasses import asdict, replace
from core.live_state import FrameContext, LiveConfig, LiveState
from core.model_readiness import face_readiness
from core.diagnostics import event
from core.live_pipeline import LiveProcessor, LiveWorker, MonitorTask, MotionProjection, FaceTrackingProcessor
from ui.live_alerts import LiveAlerts
from ui.live_overlay import draw_assessments
from core.uniform_matcher import UniformColorMatcher
from core.trainer import ViolationTrainer
from core.violation_engine import LiveViolationChecker
from database.db_manager import CBVMSDatabase
from ui.camera_feed import CameraFeed
from ui.welcome import welcome_banner
from ui.window_lifecycle import WorkspaceWindow
from ui.enrollment import EnrollmentPanel
from ui.notifications_panel import NotificationsPanel
from ui.settings import SettingsPanel
from ui.training_panel import TrainingPanel
from ui.records_panel import RecordsPanel
from ui.appeals_panel import AppealsPanel
from ui.suspensions_panel import SuspensionsPanel
from ui.violation_log import ViolationLogPanel
from ui.account_manager import AccountManagerPanel
from ui.components import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_SAFE,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    COLOR_WARNING,
    CORNER_RADIUS,
    PADDING,
    PADDING_LG,
    SIDEBAR_LEFT_WIDTH,
    SIDEBAR_RIGHT_WIDTH,
    apply_cbvms_theme,
    body_font,
    body_small_font,
    heading_font,
    make_mirror_button,
    panel_title_font,
    show_toast,
    MirrorController,
)

FEED_FPS = 30
MAX_ALERTS = 50

UNIFORM_VIOLATION_CONF = 0.65  # higher bar before a WRONG-uniform alert is logged (cuts false alarms)
CAMERA_IDLE_RELEASE_SECS = 4.0  # release the camera after this long with no consumer (power saver)
REID_MIN_GAP_SECS = 0.5      # min spacing between recognition offers (recognition takes ~0.5s)


def _put_latest(q: "queue.Queue", item) -> None:
    """Replace whatever is in a maxsize-1 queue with the freshest item (drop-old)."""
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def _drain(q: "queue.Queue"):
    """Return the latest item from a queue, or None if empty (non-blocking)."""
    try:
        return q.get_nowait()
    except queue.Empty:
        return None

ORANGE_BGR = (0, 165, 255)   # torso box color (distinct from green/red/blue face box)


class CBVMSDashboard(WorkspaceWindow):
    def __init__(
        self,
        username: str = "admin",
        *,
        database: "CBVMSDatabase | None" = None,
        recognizer: "FaceRecognizer | None" = None,
        person_detector: "PersonDetector | None" = None,
    ) -> None:
        super().__init__()
        self.withdraw()
        self.username = username
        self._logout_requested = False
        self._active_nav = "live"
        self._suspension_student_id = None
        self._feed_job: str | None = None
        self._clock_job: str | None = None
        self._stats_job: str | None = None
        self._feed_interval_ms = max(16, 1000 // FEED_FPS)

        self._camera: CameraCapture | None = None
        # One worker owns the entire capture lifecycle (open/read/release).  Tk only
        # consumes the newest frame, so a slow RTSP operation can never block the UI.
        self._camera_reader: threading.Thread | None = None
        self._camera_reader_stop: threading.Event | None = None
        self._camera_worker_done = threading.Event()
        self._camera_worker_done.set()
        self._camera_events: queue.Queue = queue.Queue()
        self._camera_generation = 0
        self._camera_switch_job: str | None = None
        self._discovered_usb_sources: list[dict] = []
        self._camera_index_setting = 0
        self._camera_source_url: str | None = None
        self._camera_resolution_setting = (1280, 720)
        self._fps_cap_setting = 30
        self._camera_retry_count = 0
        # Power saver: the camera is released when no consumer (Live view, enroll
        # wizard, or training capture modal) has asked for a frame recently.
        self._last_frame_request = 0.0  # time.monotonic() of the last camera need

        # Reuse pre-built (and already-warming) objects injected from main.py so the
        # heavy models load during the login screen instead of after the dashboard opens.
        self._injected = recognizer is not None
        if database is not None:
            self._database = database
        else:
            self._database = CBVMSDatabase()
            self._database.initialize()

        # Face recognizer (InsightFace buffalo_l: SCRFD + ArcFace) — lazy model load inside
        self._recognizer = recognizer if recognizer is not None else FaceRecognizer(self._database)

        # Real-time notification broker (sound + toast + bell badge + log panel)
        self._notifier = Notifier()
        # YOLOv8 classification trainer (uniform / earring modules)
        self._trainer = ViolationTrainer()
        # Live violation checker (uniform / earring) backed by the trainer models
        self._checker = LiveViolationChecker(self._trainer, notifier=self._notifier)
        # Full-body person detector (YOLOv8n) for reliable torso crops
        if self._injected:
            self._person_detector = person_detector  # may be None if it failed upstream
        else:
            try:
                self._person_detector = PersonDetector()
            except Exception as exc:
                print(f"[CBVMS] PersonDetector init failed: {exc}")
                self._person_detector = None

        # Preserve the trained classifier and inference-matched colour reference.
        self._uniform_matcher = UniformColorMatcher()

        # One analysis owner; camera capture and Tk preview never wait for inference.
        self._monitor_generation = 0
        self._monitor_cancel = threading.Event()
        self._monitor_result = None
        self._monitor_projections = {}
        self._monitor_last_offered = None
        self._monitor_last_rendered = None
        self._monitor_render_key = None
        self._monitor_card_key = None
        self._monitor_offer_time = 0.0
        self._preview_times = deque(maxlen=120)
        self._analysis_times = deque(maxlen=30)
        self._tracking_times = deque(maxlen=30)
        self._metrics_time = 0.0
        self._closed = threading.Event()
        self._readiness = face_readiness(self._recognizer)
        self._tracking_result = None
        self._tracking_projections = {}
        self._tracking_last_offered = None
        self._tracking_offer_time = 0.
        self._notification_out = queue.Queue(maxsize=50)
        self._stats_out = queue.Queue(maxsize=1)
        self._stats_busy = threading.Event()
        self._processor = LiveProcessor(
            self._database, self._recognizer, self._person_detector,
            self._trainer, self._uniform_matcher, self._notifier,
            latest_sample=self._latest_monitor_sample,
            state=LiveState(LiveConfig(violation_min_confidence=UNIFORM_VIOLATION_CONF)),
        )
        self._processor.readiness = self._readiness
        self._live_worker = LiveWorker(self._processor, persistence_worker=True)
        self._tracking_worker = LiveWorker(FaceTrackingProcessor(
            self._recognizer, self._person_detector, self._readiness, self._on_localized))
        self._tracking_worker.start()
        self._live_worker.start()
        try:
            cv2.setNumThreads(2)
        except Exception:
            pass
        threading.Thread(target=self._prewarm_models, daemon=True,
                         name="monitor-model-warmup").start()

        self._enrollment_panel: EnrollmentPanel | None = None
        self._training_panel: TrainingPanel | None = None
        self._suspensions_panel: SuspensionsPanel | None = None
        self._violation_panel: ViolationLogPanel | None = None
        self._settings_panel: SettingsPanel | None = None
        self._notifications_panel: NotificationsPanel | None = None
        self._records_panel: RecordsPanel | None = None
        self._account_manager_panel: AccountManagerPanel | None = None

        self._stat_today_value: ctk.CTkLabel | None = None
        self._stat_unreviewed_value: ctk.CTkLabel | None = None
        self._stat_students_value: ctk.CTkLabel | None = None
        self._stat_last_value: ctk.CTkLabel | None = None

        apply_cbvms_theme()
        self.title("CBVMS — Dashboard")
        self.geometry("1280x800")
        self.minsize(1280, 800)
        self.configure(fg_color=COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._load_camera_preference()

        self._build_ui()
        self._refresh_camera_switcher()   # populate quick source dropdown
        # Listeners may run on recognition workers.  Queue the payload and let the
        # feed loop perform every Tk/CTk operation on the UI thread.
        self._notifier.subscribe(lambda item: _put_latest(self._notification_out, item))
        self._build_menubar()
        self._tick_clock()
        self._schedule_feed_update()
        self.after(80, self._deferred_start_camera)   # start the camera ASAP after the UI maps
        self.after_idle(self.reveal_when_ready)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=220)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=280)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self._welcome_banner = welcome_banner(self, self.username, "Administrator")
        self._welcome_banner.grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=16, pady=(16, 0))

        self._build_left_sidebar()
        self._build_center_panel()
        self._build_right_sidebar()
        self._appeal_counts = queue.Queue(maxsize=1)
        self._appeal_count_busy = threading.Event()
        self._refresh_appeal_counts()

    def _refresh_appeal_counts(self):
        counts = _drain(self._appeal_counts)
        if counts is not None:
            pending, self._appeal_unread = counts
            self._nav_buttons["appeals"].configure(text=f"Appeals ({pending})")
            self._update_bell_badge()
        def read():
            try:
                self._database.process_expired_deadlines()
                counts = (self._database.admin_appeal_pending_count(), self._database.admin_appeal_unread_count())
                if self._appeal_counts.empty():
                    self._appeal_counts.put_nowait(counts)
            except Exception:
                pass
            finally:
                self._appeal_count_busy.clear()
        if not self._appeal_count_busy.is_set():
            self._appeal_count_busy.set()
            threading.Thread(target=read, daemon=True, name="appeal-counts").start()
        self._appeal_count_job = self.after(5000, self._refresh_appeal_counts)

    def _build_left_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self,
            width=SIDEBAR_LEFT_WIDTH,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(PADDING, 0), pady=PADDING)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="CBVMS", font=heading_font(26), text_color="#D0AE68",
        ).pack(anchor="w", padx=PADDING, pady=(PADDING_LG, 0))

        ctk.CTkLabel(
            sidebar, text="CAMPUS OPERATIONS",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).pack(anchor="w", padx=PADDING, pady=(0, PADDING))

        # Notification bell with unread-count badge overlay.
        bell_wrap = ctk.CTkFrame(sidebar, fg_color="transparent", height=44)
        bell_wrap.pack(fill="x", padx=PADDING, pady=(0, PADDING))
        bell_btn = ctk.CTkButton(
            bell_wrap, text="🔔  Alerts", anchor="w", height=40,
            corner_radius=CORNER_RADIUS, fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER,
            text_color=COLOR_TEXT, font=body_small_font(),
            command=self._open_alerts_from_bell,
        )
        bell_btn.pack(fill="x")
        self._bell_badge = ctk.CTkLabel(
            bell_wrap, text="", width=18, height=18, corner_radius=999,
            fg_color=COLOR_DANGER, text_color=COLOR_TEXT, font=body_small_font(),
        )
        # Placed/forgotten dynamically in _update_bell_badge().

        nav_items = [
            ("live",       "📹  Live Monitor"),
            ("enrollment", "👤  Student Management"),
            ("suspensions", "⏸  Suspensions"),
            ("violations", "⚠  Violation Log"),
            ("records",    "🗄  Records"),
            ("appeals",    "Appeals"),
            ("training",   "🎓  Training"),
            ("accounts",   "🔑  Account Manager"),
            ("settings",   "⚙  Settings"),
        ]
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        for key, label in nav_items:
            btn = ctk.CTkButton(
                sidebar, text=label, anchor="w", height=40,
                corner_radius=CORNER_RADIUS,
                fg_color=COLOR_ACCENT if key == "live" else "transparent",
                hover_color=COLOR_ACCENT_HOVER if key == "live" else COLOR_BORDER,
                text_color=COLOR_TEXT, font=body_small_font(),
                command=lambda k=key: self._on_nav_select(k),
            )
            btn.pack(fill="x", padx=PADDING, pady=4)
            self._nav_buttons[key] = btn

            def _enter(_e, b=btn, k=key):
                if self._active_nav != k:
                    b.configure(fg_color=COLOR_BORDER)

            def _leave(_e, b=btn, k=key):
                if self._active_nav != k:
                    b.configure(fg_color="transparent")

            btn.bind("<Enter>", _enter)
            btn.bind("<Leave>", _leave)

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=PADDING, pady=PADDING)

        ctk.CTkLabel(
            footer, text=f"Administrator\n{self.username}",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkButton(
            footer, text="Logout", height=36, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_DANGER, command=self._logout,
        ).pack(fill="x")

    def _build_center_panel(self) -> None:
        center = self._center_panel = ctk.CTkFrame(self, fg_color="transparent")
        center.grid(row=1, column=1, sticky="nsew", padx=PADDING, pady=PADDING)
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(1, weight=1)

        title_row = self._title_row = ctk.CTkFrame(center, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", pady=(0, PADDING))

        self._center_title = ctk.CTkLabel(
            title_row, text="Live Monitor",
            font=panel_title_font(), text_color=COLOR_TEXT,
        )
        self._center_title.pack(side="left")

        self._datetime_label = ctk.CTkLabel(
            title_row, text="", font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        )
        self._datetime_label.pack(side="right")

        # Quick camera source switcher — flip between the MacBook camera and any saved
        # IP camera instantly, without opening Settings. Persists the choice.
        self._cam_sources: dict[str, dict] = {}
        self._cam_source_var = ctk.StringVar(value="MacBook Camera")
        self._cam_switcher = ctk.CTkOptionMenu(
            title_row, variable=self._cam_source_var, values=["MacBook Camera"],
            width=190, height=30, command=self._on_switch_camera,
            fg_color=COLOR_SURFACE, button_color=COLOR_BORDER,
            button_hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
        )
        self._cam_switcher.pack(side="right", padx=(0, 12))
        ctk.CTkLabel(
            title_row, text="📷 Source", font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).pack(side="right", padx=(0, 6))

        self._content_stack = ctk.CTkFrame(center, fg_color="transparent")
        self._content_stack.grid(row=1, column=0, sticky="nsew")
        self._content_stack.grid_columnconfigure(0, weight=1)
        self._content_stack.grid_rowconfigure(0, weight=1)

        self._view_host = ctk.CTkFrame(self._content_stack, fg_color="transparent")
        self._view_host.grid(row=0, column=0, sticky="nsew")
        self._view_host.grid_columnconfigure(0, weight=1)
        self._view_host.grid_rowconfigure(0, weight=1)

        self._live_frame = ctk.CTkFrame(self._view_host, fg_color="transparent")
        self._live_frame.grid_columnconfigure(0, weight=1)
        self._live_frame.grid_rowconfigure(0, weight=0)  # stat cards
        self._live_frame.grid_rowconfigure(1, weight=1)  # camera feed
        self._live_frame.grid_rowconfigure(2, weight=0)  # status bar

        self._stats_row = self._build_stats_row(self._live_frame)
        self._stats_row.grid(row=0, column=0, sticky="ew", pady=(0, PADDING))

        # Camera feed — no fixed width/height; grid(sticky="nsew") controls size
        self.camera_feed = CameraFeed(self._live_frame, bg_color=COLOR_BG)
        self.camera_feed.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")

        # Display-only mirror toggle, overlaid in the feed's top-right corner. Flips only
        # the shown pixels — detection/recognition still run on the un-mirrored frame.
        self._mirror = MirrorController()
        self._mirror_btn = make_mirror_button(self._live_frame, self._mirror)
        self._mirror_btn.place(in_=self.camera_feed, relx=1.0, x=-14, y=14, anchor="ne")
        self._mirror_btn.lift()

        status_bar = ctk.CTkFrame(
            self._live_frame, fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER,
        )
        status_bar.grid(row=2, column=0, sticky="ew")
        status_bar.grid_columnconfigure(0, weight=1)

        self._status_camera = ctk.CTkLabel(
            status_bar, text="Camera: Starting…",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        )
        self._status_camera.grid(row=0, column=0, sticky='w', padx=PADDING, pady=(8,0))

        spinner_host = ctk.CTkFrame(status_bar, fg_color='transparent', width=110, height=24)
        spinner_host.pack_propagate(False)
        spinner_host.grid(row=0, column=1, padx=8)
        self._camera_spinner = ctk.CTkProgressBar(spinner_host, mode="indeterminate", width=100)
        self._camera_spinner.pack(side="right", padx=(0, 10), pady=14)
        self._camera_spinner.stop()
        self._camera_spinner.pack_forget()

        self._retry_model_btn = ctk.CTkButton(status_bar, text="Retry model loading", width=145,
                                              command=self._retry_models)
        self._retry_model_btn.grid(row=0, column=2, rowspan=2, padx=8, pady=8)
        self._status_fps = ctk.CTkLabel(
            status_bar, text="FPS: —",
            font=body_small_font(), text_color=COLOR_ACCENT,
        )
        self._status_fps.grid(row=1, column=0, columnspan=2, sticky='w', padx=PADDING, pady=(0,8))
        status_bar.bind('<Configure>', lambda e: self._status_camera.configure(
            wraplength=max(150,e.width-300)))
        self._status_models = ctk.CTkLabel(
            self._live_frame, text="Loading face detector", anchor="w", justify="left",
            wraplength=1050, font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._status_models.grid(row=3, column=0, sticky="ew", padx=PADDING, pady=(4, 8))
        self._live_frame.bind('<Configure>',lambda e: self._status_models.configure(
            wraplength=max(180,e.width-2*PADDING)),add='+')

        # Build secondary pages only when first opened, then reuse them.
        self._panel_factories = {
            "enrollment": ("_enrollment_panel", lambda: EnrollmentPanel(
            self._view_host,
            database=self._database,
            recognizer=self._recognizer,
            get_frame=self._get_camera_frame,
            get_frame_sample=self._get_camera_sample,
            username=self.username,
            on_open_suspensions=self._open_student_suspensions,
        )),
            "suspensions": ("_suspensions_panel", lambda: SuspensionsPanel(
            self._view_host, database=self._database, username=self.username, on_open_appeals=lambda aid=None: self._open_alert_record("appeals", aid))),
            "violations": ("_violation_panel", lambda: ViolationLogPanel(self._view_host, database=self._database)),
            "training": ("_training_panel", lambda: TrainingPanel(
            self._view_host,
            trainer=self._trainer,
            get_frame=self._get_camera_frame,
        )),
            "settings": ("_settings_panel", lambda: SettingsPanel(
            self._view_host,
            database=self._database,
            recognizer=self._recognizer,
            username=self.username,
            get_detector_loaded=lambda: (
                self._person_detector is not None
                and getattr(self._person_detector, "_model", None) is not None
            ),
            apply_camera_settings=self._apply_camera_settings,
            on_camera_source_connected=self._on_camera_source_connected,
            on_camera_sources_changed=self._on_camera_sources_changed,
            trainer=self._trainer,
            checker=self._checker,
            notifier=self._notifier,
        )),
            "alerts": ("_notifications_panel", lambda: NotificationsPanel(
            self._view_host, notifier=self._notifier, on_change=self._update_bell_badge,
            database=self._database, on_open=self._open_alert_record,
        )),
            "records": ("_records_panel", lambda: RecordsPanel(self._view_host, database=self._database, username=self.username)),
            "appeals": ("_appeals_panel", lambda: AppealsPanel(self._view_host, database=self._database, username=self.username, on_change=self._update_bell_badge)),
            "accounts": ("_account_manager_panel", lambda: AccountManagerPanel(
            self._view_host, database=self._database)),
        }
        self._views = {"live": self._live_frame}
        self._live_frame.grid(row=0, column=0, sticky="nsew")
        self._schedule_stats_refresh()

    def _build_stats_row(self, master: ctk.CTkFrame) -> ctk.CTkFrame:
        row = ctk.CTkFrame(master, fg_color="transparent")
        row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stats")

        def _card(col: int, *, label: str, accent: str) -> ctk.CTkLabel:
            card = ctk.CTkFrame(
                row, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                border_width=1, border_color=COLOR_BORDER, height=80,
            )
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
            card.grid_propagate(False)
            card.grid_columnconfigure(0, weight=1)
            value = ctk.CTkLabel(card, text="—", font=heading_font(22), text_color=accent)
            value.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 0))
            ctk.CTkLabel(
                card, text=label, font=body_font(12), text_color=COLOR_TEXT_MUTED,
            ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))
            return value

        self._stat_today_value    = _card(0, label="Total Violations Today", accent=COLOR_DANGER)
        self._stat_unreviewed_value = _card(1, label="Pending Review",       accent=COLOR_WARNING)
        self._stat_students_value = _card(2, label="Students Enrolled",       accent=COLOR_ACCENT)
        self._stat_last_value     = _card(3, label="Last Violation (local time)",          accent=COLOR_SAFE)
        return row

    def _build_right_sidebar(self) -> None:
        sidebar = self._activity_sidebar = ctk.CTkFrame(
            self, width=SIDEBAR_RIGHT_WIDTH, fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER,
        )
        sidebar.grid(row=1, column=2, sticky="nsew", padx=(0, 10), pady=10)
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            sidebar, text="Activity & alerts", font=heading_font(18), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, PADDING))

        self._alerts_scroll = LiveAlerts(sidebar, max_cards=MAX_ALERTS)
        self._alerts_scroll.grid(row=1, column=0, sticky="nsew", padx=PADDING, pady=(0, PADDING))

        ctk.CTkButton(
            sidebar, text="Clear Alerts", height=36, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER,
            command=self._clear_alerts,
        ).grid(row=2, column=0, sticky="ew", padx=PADDING, pady=(0, PADDING))

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About CBVMS", command=self._open_about_dialog)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _open_about_dialog(self) -> None:
        from ui.components import APP_COLLEGE_NAME, APP_VERSION

        win = ctk.CTkToplevel(self)
        win.title("About CBVMS")
        win.geometry("460x240")
        win.configure(fg_color=COLOR_BG)
        win.resizable(False, False)

        ctk.CTkLabel(
            win, text="Computer Based Vision Monitoring System (CBVMS)",
            font=panel_title_font(), text_color=COLOR_TEXT,
            wraplength=420, justify="center",
        ).pack(padx=PADDING, pady=(PADDING, 10))

        ctk.CTkLabel(
            win, text=f"Version {APP_VERSION}\n{APP_COLLEGE_NAME}",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED, justify="center",
        ).pack(padx=PADDING, pady=(0, 14))

        ctk.CTkButton(
            win, text="Close", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, command=win.destroy,
        ).pack(pady=(0, PADDING))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _open_student_suspensions(self, student_id: str) -> None:
        self._suspension_student_id = student_id
        self._on_nav_select("suspensions")

    def _on_nav_select(self, key: str) -> None:
        if key not in self._views:
            if key not in self._panel_factories:
                return
            attribute, factory = self._panel_factories[key]
            panel = factory()
            panel.grid_remove()
            setattr(self, attribute, panel)
            self._views[key] = panel
        previous_nav = self._active_nav
        if previous_nav != key:
            self._invalidate_monitor()
        self._active_nav = key
        if previous_nav == "appeals" and key != "appeals":
            self._appeals_panel.on_hide()
        if key == "appeals":
            self._activity_sidebar.grid_remove()
            self._title_row.grid_remove()
            self._welcome_banner.grid_remove()
            self.grid_columnconfigure(2, minsize=0)
            self._center_panel.grid_configure(columnspan=2)
        elif previous_nav == "appeals":
            self._activity_sidebar.grid()
            self._title_row.grid()
            self._welcome_banner.grid()
            self.grid_columnconfigure(2, minsize=280)
            self._center_panel.grid_configure(columnspan=1)

        # Power saver: entering Live (re)opens the camera if it was released; leaving Live
        # is handled lazily by the idle watchdog in _update_feed (with a grace window).
        if key == "live":
            # Apply any USB discovery results while handling the navigation button,
            # never asynchronously while the source menu itself may be posted.
            self._refresh_camera_switcher()
            self._acquire_camera()
        elif key == "settings":
            # Camera discovery opens USB devices briefly.  Stop the live owner first so
            # the Settings scan cannot contend for the same AVFoundation device.
            self._halt_camera()

        for nav_key, btn in self._nav_buttons.items():
            btn.configure(
                fg_color=COLOR_ACCENT if nav_key == key else "transparent",
                hover_color=COLOR_ACCENT_HOVER if nav_key == key else COLOR_BORDER,
            )

        if previous_nav == "enrollment" and self._enrollment_panel is not None:
            self._enrollment_panel.on_hide()
        if previous_nav == "training" and self._training_panel is not None:
            self._training_panel.on_hide()
        if previous_nav == "settings" and self._settings_panel is not None:
            self._settings_panel.on_hide()
        if previous_nav == "suspensions":
            self._suspensions_panel.on_hide()

        titles = {
            "live":       "Live Monitor",
            "enrollment": "Student Management",
            "suspensions": "Suspensions",
            "violations": "Violation Log",
            "records":    "Database & Record Management",
            "training":   "Training",
            "accounts":   "Account Manager",
            "settings":   "Settings",
            "alerts":     "Notifications",
        }
        self._center_title.configure(text=titles.get(key, "CBVMS"))

        def _switch_views() -> None:
            for view in self._views.values():
                view.grid_remove()
            self._views[key].grid(row=0, column=0, sticky="nsew")
            self._view_host.tkraise()
            if key == "enrollment" and self._enrollment_panel is not None:
                self._enrollment_panel.on_show()
            if key == "suspensions":
                self._suspensions_panel.on_show(self._suspension_student_id)
                self._suspension_student_id = None
            if key == "training" and self._training_panel is not None:
                self._training_panel.on_show()
            if key == "violations" and self._violation_panel is not None:
                self._violation_panel.refresh()
            if key == "appeals":
                self._appeals_panel.on_show()
            if key == "records" and self._records_panel is not None:
                self._records_panel.on_show()
            if key == "accounts" and self._account_manager_panel is not None:
                self._account_manager_panel.on_show()
            if key == "settings" and self._settings_panel is not None:
                self._settings_panel.on_show()
            if key == "alerts" and self._notifications_panel is not None:
                self._notifications_panel.refresh_external()

        self._fade_transition(_switch_views)

    def _fade_transition(self, on_midpoint) -> None:
        # Switch views immediately, then force window opacity back to fully opaque.
        # The previous animated fade compounded transparency when navigations
        # overlapped (it restored to the mid-fade alpha instead of 1.0), leaving
        # the window progressively see-through. Always reset to 1.0.
        on_midpoint()
        try:
            self.attributes("-alpha", 1.0)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _on_notification(self, notif) -> None:
        """UI-thread: show a toast (if enabled) and refresh the bell badge."""
        if self._notifier.toast_enabled:
            try:
                show_toast(
                    self, f"⚠  {notif.student_name}: {notif.violation}",
                    type="warning", duration=4000,
                )
            except Exception:
                pass
        self._update_bell_badge()

    def _update_bell_badge(self) -> None:
        """Refresh the sidebar bell badge from the notifier's unread count.

        Tolerant of teardown: a notification can be scheduled (after(0, …)) and
        then run after the window is closed, at which point Tk calls raise
        TclError — including winfo_exists() — so the whole body is guarded.
        """
        badge = getattr(self, "_bell_badge", None)
        if badge is None:
            return
        try:
            count = self._notifier.unread_count() + getattr(self, "_appeal_unread", 0)
            if count <= 0:
                badge.place_forget()
                return
            badge.configure(text="9+" if count > 9 else str(count))
            badge.place(relx=1.0, rely=0.0, anchor="ne", x=-2, y=2)
        except Exception:
            pass

    def _mark_all_read(self) -> None:
        self._notifier.mark_all_read()
        self._update_bell_badge()
        if self._notifications_panel is not None:
            self._notifications_panel.refresh_external()

    def _open_alerts_from_bell(self) -> None:
        self._on_nav_select("alerts")

    def _open_alert_record(self, category: str, record_id: int | None) -> None:
        if category == "appeals":
            self._on_nav_select("appeals")
            self._appeals_panel.open_alert(category, record_id)
            return
        self._on_nav_select("records")
        if self._records_panel is not None:
            self._records_panel.open_alert(category, record_id)

    # ------------------------------------------------------------------
    # Camera preferences
    # ------------------------------------------------------------------

    def _load_camera_preference(self) -> None:
        try:
            from api.camera_store import get_camera_preference
            pref = get_camera_preference()
        except Exception:
            pref = None
        if not pref:
            return
        cam_type = str(pref.get("type", "")).lower()
        if cam_type == "usb":
            self._camera_index_setting = int(pref.get("index", 0))
            self._camera_source_url = None
        elif cam_type in ("rj45", "ip"):
            url = str(pref.get("url", "")).strip()
            self._camera_source_url = url or None

    def _on_camera_source_connected(self, pref: dict) -> None:
        cam_type = str(pref.get("type", "")).lower()
        if cam_type == "usb":
            self._camera_index_setting = int(pref.get("index", 0))
            self._camera_source_url = None
        elif cam_type in ("rj45", "ip"):
            self._camera_source_url = str(pref.get("url", "")).strip() or None
        self._camera_retry_count = 0
        self._refresh_camera_switcher()
        if self._active_nav == "live":
            self._deferred_start_camera()
        else:
            # Settings selects metadata only.  The source opens when Live is shown,
            # keeping discovery and live capture mutually exclusive.
            self._halt_camera()
            self._status_camera.configure(
                text="Camera: Source selected", text_color=COLOR_TEXT_MUTED
            )

    def _on_camera_sources_changed(self, cameras: list[dict]) -> None:
        """Merge USB devices discovered in Settings into the quick switcher."""
        self._discovered_usb_sources = [
            dict(cam) for cam in cameras if str(cam.get("type", "")).lower() == "usb"
        ]

    def _refresh_camera_switcher(self) -> None:
        """Repopulate the quick source dropdown from the MacBook cam + saved IP cameras."""
        try:
            from api.camera_store import get_saved_ip_cameras
            saved = get_saved_ip_cameras()
        except Exception:
            saved = []
        sources: dict[str, dict] = {}

        def _add_source(label: str, source: dict) -> None:
            key = label
            if key in sources and sources[key].get("id") != source.get("id"):
                key = f"{label} ({str(source.get('id', ''))[-4:]})"
            sources[key] = source

        usb_by_index: dict[int, dict] = {
            0: {"id": "usb_0", "type": "usb", "index": 0,
                "label": "MacBook Camera", "status": "connected"},
        }
        for cam in self._discovered_usb_sources:
            try:
                index = int(cam.get("index", 0))
            except (TypeError, ValueError):
                continue
            usb_by_index[index] = {
                "id": str(cam.get("id") or f"usb_{index}"),
                "type": "usb",
                "index": index,
                "label": "MacBook Camera" if index == 0 else str(
                    cam.get("label") or f"USB Camera {index}"
                ),
                "status": cam.get("status", "available"),
            }
        # Preserve a selected non-zero USB source even before the next discovery scan.
        if self._camera_source_url is None and self._camera_index_setting not in usb_by_index:
            index = self._camera_index_setting
            usb_by_index[index] = {
                "id": f"usb_{index}", "type": "usb", "index": index,
                "label": f"USB Camera {index}", "status": "connected",
            }
        for index, source in sorted(usb_by_index.items()):
            _add_source(str(source["label"]), source)

        for cam in saved:
            label = str(cam.get("label", "IP Camera"))
            _add_source(
                label,
                {"id": f"ip_{cam['id']}", "type": "rj45",
                 "label": label, "url": cam["url"]},
            )
        self._cam_sources = sources
        values = list(sources.keys())
        try:
            self._cam_switcher.configure(values=values)
        except Exception:
            return
        # Reflect the currently-active source in the dropdown without re-triggering it.
        current = "MacBook Camera"
        if self._camera_source_url:
            for k, v in sources.items():
                if v.get("url") == self._camera_source_url:
                    current = k
                    break
        else:
            for k, v in sources.items():
                if v.get("type") == "usb" and int(v.get("index", 0)) == self._camera_index_setting:
                    current = k
                    break
        self._cam_source_var.set(current)

    def _on_switch_camera(self, choice: str) -> None:
        """Queue a quick switch and return from the native menu callback immediately."""
        pref = self._cam_sources.get(choice)
        if not pref:
            return
        # A different USB index is a real switch.  A failed source may be selected
        # again to force a retry instead of being incorrectly treated as a no-op.
        same_usb = (
            pref["type"] == "usb"
            and self._camera_source_url is None
            and int(pref.get("index", 0)) == self._camera_index_setting
        )
        same_ip = pref["type"] == "rj45" and pref.get("url") == self._camera_source_url
        healthy = self._camera is not None and self._camera.is_open
        if (same_usb or same_ip) and healthy:
            return
        if self._camera_switch_job is not None:
            try:
                self.after_cancel(self._camera_switch_job)
            except Exception:
                pass
        # Reconfiguring CTkOptionMenu or laying out a toast while its native popup is
        # executing can wedge Tk on macOS.  Run all real work after that callback exits.
        self._camera_switch_job = self.after(
            1, lambda: self._commit_camera_switch(choice, dict(pref))
        )

    def _commit_camera_switch(self, choice: str, pref: dict) -> None:
        self._camera_switch_job = None
        try:
            from api.camera_store import save_camera_preference
            save_camera_preference(pref)
        except Exception:
            pass
        show_toast(self, f"Switching to {choice}…", type="info", duration=1500)
        self._on_camera_source_connected(pref)

    def _apply_camera_settings(
        self, camera_index: int, resolution: tuple[int, int], fps_cap: int
    ) -> None:
        if self._camera_source_url is None:
            self._camera_index_setting = int(camera_index)
        self._camera_resolution_setting = (int(resolution[0]), int(resolution[1]))
        self._fps_cap_setting = max(10, min(60, int(fps_cap)))
        self._feed_interval_ms = max(16, 1000 // self._fps_cap_setting)
        self._camera_retry_count = 0
        if self._active_nav == "live":
            self._deferred_start_camera()
            message = "Restarting camera…"
        else:
            message = "Stream settings saved; they will apply on Live Monitor."
        show_toast(self, message, type="info", duration=1800)

    # ------------------------------------------------------------------
    # Camera lifecycle
    # ------------------------------------------------------------------

    def _camera_needed(self) -> bool:
        """True while a consumer needs the camera: Live view, or a recent frame request
        (the enroll wizard / training capture modal pull frames via _get_camera_frame)."""
        return (
            self._active_nav == "live"
            or (time.monotonic() - self._last_frame_request) <= CAMERA_IDLE_RELEASE_SECS
        )

    def _acquire_camera(self) -> None:
        """Start the camera if it's currently off (no-op if open/opening)."""
        if self._camera is None:
            self._camera_retry_count = 0
            self._deferred_start_camera()

    def _deferred_start_camera(self) -> None:
        # Invalidate earlier opens/retries and stop the old owner without waiting on Tk.
        self._camera_generation += 1
        generation = self._camera_generation
        self._last_frame_request = time.monotonic()  # grace window for explicit starts
        if hasattr(self, "_monitor_cancel"):
            self._invalidate_monitor()
        previous_done = self._stop_camera()
        try:
            self._camera_spinner.pack(side="right", padx=(0, 10), pady=14)
            self._camera_spinner.start()
        except Exception:
            pass
        self._status_camera.configure(
            text="Camera: Switching…" if not previous_done.is_set() else "Camera: Starting…",
            text_color=COLOR_TEXT_MUTED,
        )

        def _wait_for_previous() -> None:
            if generation != self._camera_generation:
                return
            if not previous_done.is_set():
                self.after(40, _wait_for_previous)
                return
            self._start_camera_worker(generation)

        self.after(0, _wait_for_previous)

    def _start_camera_worker(self, generation: int) -> None:
        """Start one thread that exclusively owns open/read/release for this source."""
        if generation != self._camera_generation:
            return

        requested_width, requested_height = self._camera_resolution_setting
        requested_fps = self._fps_cap_setting
        if self._camera_source_url:
            cap = CameraCapture(
                source_url=self._camera_source_url,
                width=self._camera_resolution_setting[0],
                height=self._camera_resolution_setting[1],
                fps_cap=self._fps_cap_setting,
            )
        else:
            cap = CameraCapture(
                camera_index=self._camera_index_setting,
                width=self._camera_resolution_setting[0],
                height=self._camera_resolution_setting[1],
                fps_cap=self._fps_cap_setting,
            )
        self._camera = cap
        stop = threading.Event()
        done = threading.Event()
        self._camera_reader_stop = stop
        self._camera_worker_done = done

        def _worker() -> None:
            ok = False
            with CAMERA_DEVICE_LOCK:
                try:
                    # This worker may have waited behind a scan or an older stream.
                    # If it was superseded meanwhile, do not open a stale source at all.
                    if stop.is_set():
                        return
                    ok = cap.open()
                    if ok:
                        # Query the negotiated settings on the device owner thread;
                        # unsupported backend readback must not interrupt the stream.
                        try:
                            settings = cap.get_settings()
                        except Exception:
                            settings = {"reported_settings": "unavailable"}
                        if os.environ.get("CBVMS_CAMERA_DIAGNOSTICS") == "1":
                            print(f"[Dashboard camera] requested {requested_width}x{requested_height} "
                                  f"@ {requested_fps} FPS; reported {settings}")
                    self._camera_events.put(("opened", generation, cap, ok))
                    last_received = time.monotonic()
                    while ok and not stop.is_set():
                        frame = cap.read()
                        if frame is None:
                            if not cap.is_open or time.monotonic()-last_received > 3:
                                event("camera_stalled", generation=generation)
                                break
                            time.sleep(0.005)
                        else:
                            last_received = time.monotonic()
                except Exception as exc:
                    cap.last_error = str(exc)
                    self._camera_events.put(("opened", generation, cap, False))
                finally:
                    # The same worker that opened and read the capture also releases it.
                    # This avoids cross-thread AVFoundation operations on macOS.
                    try:
                        cap.release()
                    except Exception as exc:
                        cap.last_error = f"Camera release failed: {exc}"
                        self._camera_events.put(("release_error", generation, cap, False))
                    finally:
                        # A backend teardown error must never make all future switches
                        # wait forever for this owner to finish.
                        done.set()
                        self._camera_events.put(("stopped", generation, cap, ok))

        worker = threading.Thread(target=_worker, daemon=True, name=f"camera-{generation}")
        self._camera_reader = worker
        worker.start()

    def _drain_camera_events(self) -> None:
        while True:
            try:
                event, generation, cap, ok = self._camera_events.get_nowait()
            except queue.Empty:
                return
            if event == "opened":
                self._on_camera_opened(cap, ok, generation)
            elif event == "stopped" and ok and generation == self._camera_generation and self._camera is cap:
                self._invalidate_monitor()
                self._status_camera.configure(text="Camera: Reconnecting…", text_color=COLOR_WARNING)
                self.after(1500, lambda g=generation: self._retry_camera(g))

    def _on_camera_opened(self, cap: CameraCapture, ok: bool, generation: int) -> None:
        # Stale workers own and release their own capture.  Never call release() here:
        # OpenCV teardown may block and this method always runs on Tk's UI thread.
        if generation != self._camera_generation or self._camera is not cap:
            return

        try:
            self._camera_spinner.stop()
            self._camera_spinner.pack_forget()
        except Exception:
            pass

        if ok:
            self._camera_retry_count = 0
            self._status_camera.configure(text="Camera: Active", text_color=COLOR_SAFE)
        else:
            err = cap.last_error or "Could not open camera"
            self._status_camera.configure(text=f"Camera: {err}", text_color=COLOR_DANGER)
            if self._camera_retry_count == 0:
                show_toast(self, f"Camera error: {err}", type="error", duration=4000)
            # Only retry while a consumer still needs the camera — don't power-cycle a
            # camera nobody's watching.
            if self._camera_retry_count < 8 and self._camera_needed():
                self._camera_retry_count += 1
                self.after(2000, lambda g=generation: self._retry_camera(g))

    def _retry_camera(self, generation: int) -> None:
        if generation == self._camera_generation and self._camera_needed():
            self._deferred_start_camera()

    def _stop_camera(self) -> threading.Event:
        """Signal the camera owner and return immediately with its completion event."""
        stop = self._camera_reader_stop
        done = self._camera_worker_done
        self._camera = None
        self._camera_reader = None
        self._camera_reader_stop = None
        if stop is not None:
            stop.set()
        return done

    def _halt_camera(self) -> threading.Event:
        """Stop intentionally and invalidate every pending start or retry callback."""
        self._camera_generation += 1
        if hasattr(self, "_monitor_cancel"):
            self._invalidate_monitor()
        return self._stop_camera()

    def _get_camera_frame(self):
        # A consumer (enroll wizard / training capture modal) wants a frame — mark the
        # camera as needed and lazily start it if it was released for power saving.
        self._last_frame_request = time.monotonic()
        if self._camera is None:
            self._acquire_camera()
        if self._camera and self._camera.is_open:
            # The reader thread owns cap.read(); calling it here too would mean two
            # concurrent reads on one VideoCapture (unsafe). Use the latest pumped frame.
            return self._camera.get_latest_frame()
        return None

    def _get_camera_sample(self):
        self._last_frame_request = time.monotonic()
        if self._camera is None:
            self._acquire_camera()
        if self._camera and self._camera.is_open:
            return self._camera.get_latest_sample()
        return None

    # ------------------------------------------------------------------
    # Background face detection worker
    # ------------------------------------------------------------------

    def _prewarm_models(self) -> None:
        def load_body():
            if self._person_detector is None:
                raise RuntimeError("Body detector unavailable")
            self._person_detector._load_failed = False
            if self._person_detector._ensure_model() is None:
                raise RuntimeError(self._person_detector.last_error or "Body detector failed")
            # A saved reference is reused, never rebuilt by camera startup.
            self._person_detector.detect_persons(np.zeros((320, 320, 3), np.uint8))
            if self._person_detector.last_error:
                raise RuntimeError(self._person_detector.last_error)
            return True

        def load_uniform():
            if not self._uniform_matcher.is_loaded():
                self._uniform_matcher.load()
            if self._trainer.is_trained("uniform"):
                if self._trainer._get_model("uniform") is None:
                    raise RuntimeError(self._trainer.last_error.get("uniform") or "Uniform classifier failed")
                if self._trainer.predict_proba('uniform', np.zeros((224, 224, 3), np.uint8)) is None:
                    raise RuntimeError("Uniform classifier inference failed; restore compatible weights and retry")
            else:
                raise RuntimeError("Train a uniform classifier in Training; colour reference alone cannot assess garments")
            return True

        def load_earring():
            if not self._trainer.is_trained("earring"):
                raise RuntimeError("No trained earring weights")
            return self._trainer._get_model("earring")

        self._readiness.add("body", load_body)
        self._readiness.add("uniform", load_uniform)
        self._readiness.add("earring", load_earring)
        self._readiness.start()

    def _retry_models(self):
        self._readiness.start(retry=True)

    def _on_localized(self, result):
        """Tracking worker submits its original pixels, landmarks and ownership token."""
        if (not result.task.valid() or not self._readiness.ready('recognition')
                or result.task.context.captured_at-self._monitor_offer_time < REID_MIN_GAP_SECS):
            return
        self._live_worker.offer(replace(result.task, observations=result.observations))
        self._monitor_offer_time = result.task.context.captured_at

    def _latest_monitor_sample(self):
        """Worker-safe read; never starts devices or touches Tk."""
        camera = self._camera
        return camera.get_latest_sample() if camera is not None and camera.is_open else None

    def _invalidate_monitor(self):
        if hasattr(self, "_tracking_worker"):
            _drain(self._tracking_worker.requests)
            _drain(self._tracking_worker.results)
            self._tracking_result = None
            self._tracking_projections.clear()
            self._tracking_last_offered = None
        self._monitor_cancel.set()
        self._monitor_cancel = threading.Event()
        self._monitor_generation += 1
        self._monitor_result = None
        self._monitor_projections.clear()
        self._monitor_last_offered = None
        self._monitor_last_rendered = None
        self._monitor_render_key = None
        self._monitor_card_key = None
        self._monitor_status_text = None
        self._preview_times.clear()
        self._analysis_times.clear()
        self._tracking_times.clear()
        _drain(self._live_worker.requests)
        _drain(self._live_worker.results)
        if getattr(self, "_alerts_scroll", None) is not None:
            self._alerts_scroll.mark_all_inactive()

    def _monitor_rows(self, sample):
        for name, projections in (("_monitor_result", self._monitor_projections),
                                  ("_tracking_result", self._tracking_projections)):
            result = getattr(self, name)
            if result is not None and (not result.task.valid()
                    or result.task.context.generation != self._monitor_generation
                    or result.task.camera_generation != self._camera_generation
                    or result.task.context.frame_id[0] != sample.frame_id[0]):
                event("frame_rejected", stage="display", reason="expired_or_obsolete_session",
                      frame_id=result.task.context.frame_id)
                setattr(self, name, None)
                projections.clear()
        rows = self._project_rows(self._monitor_result, self._monitor_projections, sample)
        tracking = self._project_rows(self._tracking_result, self._tracking_projections, sample)
        if self._tracking_result is not None and self._tracking_result.observations is not None:
            # One presence namespace. A result is attached only to the track that
            # supplied its original pixels, and only while optical flow validates it.
            by_presence={r['presence_id']:r for r in rows}
            combined=[]
            for current in tracking:
                assessed=by_presence.get(current['presence_id'])
                if assessed is not None:
                    a,b=current['face_box'],assessed['face_box']
                    overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
                    area=max(1,(a[2]-a[0])*(a[3]-a[1]))
                    if overlap/area >= .5:
                        # Current geometry, same-person validated original evidence.
                        assessed.update(face_box=current['face_box'],torso_box=current['torso_box'])
                        if current['torso_box'] is None:
                            assessed.update(state='Uniform not assessed',reason=current['reason'],
                                            detail=current['reason'],accepted_categories=())
                        current=assessed
                combined.append(current)
            return combined
        for row in tracking:
            a = row['face_box']
            if not any(max(a[0], b['face_box'][0]) < min(a[2], b['face_box'][2]) and
                       max(a[1], b['face_box'][1]) < min(a[3], b['face_box'][3]) for b in rows):
                # A detection-only presence cannot inherit a delayed student's result.
                row['presence_id'] = "tracking:" + row['presence_id']
                rows.append(row)
        return rows

    def _project_rows(self, result, projections, sample):
        if result is None or not result.task.valid():
            return []
        if (result.task.context.generation != self._monitor_generation
                or result.task.camera_generation != self._camera_generation
                or result.task.context.frame_id[0] != sample.frame_id[0]):
            return []
        gray = None
        rows = []
        for assessment in result.assessments:
            row = asdict(assessment)
            row.update(observed_at=result.task.observed_at, detail=assessment.reason)
            projection = projections.get(assessment.track_id)
            if sample.frame_id == result.task.context.frame_id:
                box = assessment.face_box
            elif projection is not None:
                if gray is None:
                    gray = MotionProjection.gray_frame(sample.frame)
                box = projection.advance(sample.frame, gray)
            else:
                box = None
            if box is None:
                # Never attach an old name or verdict to a newly occupied location.
                if projection is not None and not getattr(projection,'withheld_logged',False):
                    event('overlay_withheld', presence_id=assessment.presence_id,
                          frame_id=result.task.context.frame_id, reason='motion_not_verified')
                    projection.withheld_logged=True
                continue
            offset = getattr(projection,'offset',None) if projection is not None else None
            dx,dy = offset if isinstance(offset,tuple) and len(offset)==2 else (
                box[0]-max(0,assessment.face_box[0]), box[1]-max(0,assessment.face_box[1]))
            row['face_box'] = box
            if assessment.torso_box:
                x1,y1,x2,y2 = assessment.torso_box
                row['torso_box'] = (x1+dx,y1+dy,x2+dx,y2+dy)
            rows.append(row)
        return rows

    @staticmethod
    def _measured_rate(times, now):
        recent = [t for t in times if now-t <= 5.0]
        return (len(recent)-1)/(recent[-1]-recent[0]) if len(recent)>1 else 0.0

    def _schedule_feed_update(self) -> None:
        self._feed_job = self.after(self._feed_interval_ms, self._update_feed)

    def _update_feed(self) -> None:
        if self._closed.is_set():
            return
        started = time.monotonic()
        try:
            self._drain_camera_events()
            notification = _drain(self._notification_out)
            if notification is not None and (notification.valid_if is None or notification.valid_if()):
                self._on_notification(notification)
            stats = _drain(self._stats_out)
            if stats is not None:
                for widget, value in zip((self._stat_today_value, self._stat_unreviewed_value,
                                          self._stat_students_value, self._stat_last_value), stats):
                    if widget is not None:
                        widget.configure(text=value)
            if self._camera is not None and not self._camera_needed():
                self._halt_camera()
            if self._active_nav == "live":
                model_message = self._readiness.message()
                if model_message != getattr(self, '_model_status_text', None):
                    self._status_models.configure(text=model_message)
                    self._model_status_text = model_message
                failed = any(v.state == 'failed' for v in self._readiness.snapshot().values())
                if failed != getattr(self, '_model_retry_enabled', None):
                    self._retry_model_btn.configure(state="normal" if failed else "disabled")
                    self._model_retry_enabled = failed
                sample = self._latest_monitor_sample()
                if sample is None or started-sample.captured_at > 1.0:
                    if self._monitor_last_rendered is not None:
                        self._invalidate_monitor()
                    message = "Camera: Reconnecting…" if self._camera else "Camera: Disconnected"
                    self._status_camera.configure(text=message, text_color=COLOR_WARNING)
                    self._monitor_status_text = message
                    self.camera_feed.show_placeholder("Reconnecting camera…" if self._camera else "No camera connected")
                else:
                    self._last_frame_request = started
                    tracking = _drain(self._tracking_worker.results)
                    if (tracking is not None and tracking.task.valid()
                            and tracking.task.context.generation == self._monitor_generation
                            and tracking.task.camera_generation == self._camera_generation
                            and tracking.task.context.frame_id[0] == sample.frame_id[0]):
                        self._tracking_times.append(started)
                        self._tracking_result = tracking
                        self._tracking_projections = {a.track_id: MotionProjection(tracking.task.frame, a.face_box)
                                                      for a in tracking.assessments}
                    if (self._readiness.ready('face') and sample.frame_id != self._tracking_last_offered
                            and started-self._tracking_offer_time >= .12):
                        self._tracking_worker.offer(MonitorTask(
                            FrameContext(self._monitor_generation, sample.frame_id, sample.captured_at),
                            sample.frame, sample.captured_wall_time or time.time()-(started-sample.captured_at), self._monitor_cancel,
                            uniform_enabled=self._checker.check_uniform, earring_enabled=self._checker.check_earring,
                            camera_generation=self._camera_generation))
                        self._tracking_last_offered = sample.frame_id
                        self._tracking_offer_time = started
                    result = _drain(self._live_worker.results)
                    if (result is not None and result.task.valid()
                            and result.task.context.generation == self._monitor_generation
                            and result.task.camera_generation == self._camera_generation
                            and result.task.context.frame_id[0] == sample.frame_id[0]):
                        if self._monitor_result is None or self._monitor_result.task.context.frame_id != result.task.context.frame_id:
                            self._analysis_times.append(started)
                        self._monitor_result = result
                        self._monitor_projections = {
                            a.track_id: MotionProjection(result.task.frame, a.face_box)
                            for a in result.assessments}
                    render_key = (sample.frame_id, self._mirror.display_mirror(),
                                  self.camera_feed.winfo_width(), self.camera_feed.winfo_height())
                    expired = self._monitor_result is not None and not self._monitor_result.task.valid()
                    rows = self._monitor_rows(sample)
                    if render_key != self._monitor_render_key or result is not None or tracking is not None or expired:
                        card_key = tuple((row['presence_id'], row['state'], row['student_id'],
                                          row['reason'], row['observed_at']) for row in rows)
                        if card_key != self._monitor_card_key:
                            self._alerts_scroll.update_assessments(rows)
                            self._monitor_card_key = card_key
                        self._display_rows = tuple(rows)
                        annotated = draw_assessments(sample.frame, rows, mirror=self._mirror.display_mirror())
                        if self.camera_feed.render(self._mirror.apply_anim(annotated)):
                            if sample.frame_id != self._monitor_last_rendered:
                                self._preview_times.append(started)
                            self._monitor_last_rendered = sample.frame_id
                            self._monitor_render_key = render_key
                    result = self._monitor_result
                    detail = (result.detail if result is not None and result.task.valid() and result.detail else
                              self._tracking_result.detail if self._tracking_result is not None
                              and self._tracking_result.task.valid() else "Waiting for analysis")
                    if rows:
                        detail = ' · '.join(dict.fromkeys(r['state'] for r in rows))
                    if (detail == "No faces detected" and self._tracking_result is not None
                            and self._tracking_result.task.valid() and self._tracking_result.assessments):
                        detail = "Face detected · Identity uncertain"
                    for worker in (self._tracking_worker, self._live_worker):
                        if worker.last_error:
                            detail = f"Analysis failed: {worker.last_error}"
                        elif worker.active_task is not None and started-worker.active_since > 3:
                            detail += " · Slow analysis; stale results rejected"
                    status_text = f"Camera: Active · {detail}"
                    if status_text != getattr(self, '_monitor_status_text', None):
                        self._status_camera.configure(text=status_text, text_color=COLOR_SAFE)
                        self._monitor_status_text = status_text
            if started-self._metrics_time >= 1:
                self._metrics_time = started
                self._status_fps.configure(text=f"Preview {self._measured_rate(self._preview_times, started):.1f} FPS · "
                                          f"Tracking {self._measured_rate(self._tracking_times, started):.1f}/s · "
                                          f"Analysis {self._measured_rate(self._analysis_times, started):.1f}/s")
        except Exception as exc:
            print(f"[CBVMS] feed error: {exc}")
        if not self._closed.is_set():
            delay = max(1, self._feed_interval_ms-int((time.monotonic()-started)*1000))
            self._feed_job = self.after(delay, self._update_feed)

    # ------------------------------------------------------------------
    # Clock & stats
    # ------------------------------------------------------------------

    def _tick_clock(self) -> None:
        self._datetime_label.configure(text=datetime.now().strftime("%A, %d %b %Y  %H:%M:%S"))
        self._clock_job = self.after(1000, self._tick_clock)

    def _schedule_stats_refresh(self) -> None:
        if self._stats_job:
            try:
                self.after_cancel(self._stats_job)
            except Exception:
                pass
            self._stats_job = None
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        if not self._closed.is_set() and not self._stats_busy.is_set():
            self._stats_busy.set()
            threading.Thread(target=self._read_stats, daemon=True, name="monitor-statistics").start()
        if not self._closed.is_set():
            self._stats_job = self.after(10_000, self._refresh_stats)

    def _read_stats(self):
        try:
            self._database.process_expired_deadlines()
            start, end = local_calendar_day_utc_bounds(date.today().isoformat())
            with self._database.connect() as conn:
                today = conn.execute("SELECT COUNT(*) FROM violations WHERE datetime(timestamp)>=datetime(?) AND datetime(timestamp)<datetime(?)", (start,end)).fetchone()[0]
                pending = conn.execute("SELECT COUNT(*) FROM violations WHERE status='pending_review'").fetchone()[0]
                students = conn.execute("SELECT COUNT(*) FROM students WHERE student_status='Enrolled' AND registration_pending=0").fetchone()[0]
                last = conn.execute("SELECT MAX(timestamp) FROM violations").fetchone()[0]
            from core.discipline import parse_db_datetime
            stamp = parse_db_datetime(last).astimezone().strftime("%m-%d %H:%M:%S") if last else "—"
            if not self._closed.is_set():
                _put_latest(self._stats_out, (str(today),str(pending),str(students),stamp))
        except Exception as exc:
            print(f"[CBVMS] statistics unavailable: {exc}")
        finally:
            self._stats_busy.clear()

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _clear_alerts(self) -> None:
        self._alerts_scroll.clear()

    def _logout(self) -> None:
        self._logout_requested = True
        self._on_close()

    def _on_close(self) -> None:
        self._closed.set()
        self._monitor_cancel.set()
        self._tracking_worker.stop()
        self._live_worker.stop()
        if self._enrollment_panel is not None:
            self._enrollment_panel.on_hide()
        if self._training_panel is not None:
            self._training_panel.on_hide()
        if self._camera_switch_job is not None:
            try:
                self.after_cancel(self._camera_switch_job)
            except Exception:
                pass
            self._camera_switch_job = None
        for job_attr in ("_feed_job", "_clock_job", "_stats_job", "_appeal_count_job"):
            job = getattr(self, job_attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_attr, None)
        self._halt_camera()
        self.camera_feed.cleanup()
        self.destroy()


def open_dashboard(
    username: str = "admin",
    *,
    database=None,
    recognizer=None,
    person_detector=None,
) -> bool:
    """Run the dashboard. Returns True if the user logged out (vs. closed the app)."""
    app = CBVMSDashboard(
        username=username,
        database=database,
        recognizer=recognizer,
        person_detector=person_detector,
    )
    app.mainloop()
    # Let cancelled native calls return before Python tears down ONNX/PyTorch
    # types. Otherwise closing the window can emit spurious model-type errors.
    deadline=time.monotonic()+5
    for worker in (app._tracking_worker,app._live_worker):
        worker.done.wait(max(0,deadline-time.monotonic()))
    app._live_worker.writes_done.wait(max(0,deadline-time.monotonic()))
    app._camera_worker_done.wait(max(0,deadline-time.monotonic()))
    app._readiness.wait(max(0,deadline-time.monotonic()))
    return getattr(app, "_logout_requested", False)
