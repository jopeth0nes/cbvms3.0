"""Main CBVMS dashboard with live camera feed."""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime

import cv2
import customtkinter as ctk
import numpy as np

from core.camera import CAMERA_DEVICE_LOCK, CameraCapture
from core.notifier import Notifier
from core.person_detector import PersonDetector, skin_fraction
from core.recognizer import FaceRecognizer
from core.tracker import FaceTracker
from core.uniform_matcher import UniformColorMatcher, fuse_uniform_prob
from core.uniform_embedder import UniformEmbedMatcher
from core.trainer import ViolationTrainer
from core.violation_engine import LiveViolationChecker
from database.db_manager import CBVMSDatabase
from ui.camera_feed import CameraFeed
from ui.enrollment import EnrollmentPanel
from ui.notifications_panel import NotificationsPanel
from ui.settings import SettingsPanel
from ui.training_panel import TrainingPanel
from ui.records_panel import RecordsPanel
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
PRESENCE_TIMEOUT_SECS = 30   # seconds absent from frame before re-appearance triggers UI alert
DB_LOG_COOLDOWN_SECS   = 300 # 5-minute minimum between DB log entries per person

UNIFORM_MIN_CONF = 0.60      # below this the uniform verdict is treated as uncertain (no OK/violation)
UNIFORM_VIOLATION_CONF = 0.65  # higher bar before a WRONG-uniform alert is logged (cuts false alarms)
UNIFORM_EMA_ALPHA = 0.5      # smoothing weight for the per-person uniform probability (anti-flicker)
UNIFORM_SKIN_ABSTAIN = 0.40  # after clamping below the chin, a crop still >40% bare skin → abstain
CAMERA_IDLE_RELEASE_SECS = 4.0  # release the camera after this long with no consumer (power saver)
DETECT_EVERY_N_FRAMES = 3    # offer every Nth feed frame to the fast detect worker (~10 FPS)
REID_INTERVAL_SECS = 2.0     # periodic identity refresh even when all tracks are identified
REID_MIN_GAP_SECS = 0.5      # min spacing between recognition offers (recognition takes ~0.5s)


def _put_latest(q: "queue.Queue", item) -> None:
    """Replace whatever is in a maxsize-1 queue with the freshest item (drop-old)."""
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


class CBVMSDashboard(ctk.CTk):
    def __init__(
        self,
        username: str = "admin",
        *,
        database: "CBVMSDatabase | None" = None,
        recognizer: "FaceRecognizer | None" = None,
        person_detector: "PersonDetector | None" = None,
    ) -> None:
        super().__init__()
        self.username = username
        self._logout_requested = False
        self._active_nav = "live"
        self._alerts: deque[dict] = deque(maxlen=MAX_ALERTS)
        self._face_presence: dict[str, float] = {}   # identity_key → last_seen_epoch
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

        # Uniform verdict = pretrained-embedding match (garment fingerprint) AND colour match.
        # Both generalise from the correct-uniform photos; the from-scratch YOLO classifier
        # degenerated on the abundant-correct / scarce-wrong dataset and is no longer used live.
        self._uniform_matcher = UniformColorMatcher()
        self._uniform_embed = UniformEmbedMatcher()

        # Detect-and-track pipeline. Two background workers feed a UI-owned tracker:
        #   • detect worker — fast SCRFD-only boxes (~52ms) every couple of frames,
        #     so squares follow motion smoothly with no lag.
        #   • recognize worker — slow ArcFace identity (~520ms) only when a track is
        #     unidentified or on a periodic refresh, keeping CPU low.
        # The tracker binds identity to a persistent track by IoU, so two people can
        # never merge onto one label. Tracker is mutated only on the UI thread (workers
        # post via after(0)), so it needs no lock.
        self._tracker = FaceTracker()
        self._face_frame_counter: int = 0
        self._last_recog_offer: float = 0.0
        self._face_queue: queue.Queue = queue.Queue(maxsize=1)    # detect worker input
        self._recog_queue: queue.Queue = queue.Queue(maxsize=1)   # recognize worker input
        # Worker → UI result hand-off. Tk/Tcl is NOT thread-safe (esp. on macOS): the
        # workers must never call self.after()/winfo_exists(). They drop results in these
        # maxsize-1 queues and the UI thread drains them in _update_feed.
        self._boxes_out: queue.Queue = queue.Queue(maxsize=1)     # detect → tracker.update
        self._ident_out: queue.Queue = queue.Queue(maxsize=1)     # recog → identities + alerts
        self._violen_out: queue.Queue = queue.Queue(maxsize=1)    # recog → violation overlay
        self._violation_dirty = False                              # set by worker, read by UI
        # Cap OpenCV's thread pool so continuous background detection can't peg every core
        # and starve the Tk main thread (keeps the UI snappy).
        try:
            cv2.setNumThreads(2)
        except Exception:
            pass
        self._detect_worker = threading.Thread(target=self._detect_worker_loop, daemon=True)
        self._recognize_worker = threading.Thread(target=self._recognize_worker_loop, daemon=True)
        self._detect_worker.start()
        self._recognize_worker.start()
        # Pre-warm the heavy models (InsightFace, YOLO, trained classifiers) in the
        # background at launch — they otherwise load lazily on the first frame, so the
        # Live Monitor would take several seconds to start scanning. Warming now hides
        # that behind the login→dashboard build + camera warmup.
        threading.Thread(target=self._prewarm_models, daemon=True).start()

        # Separate DB-write cooldown (much longer than UI presence timeout)
        # Prevents rapid re-logging of the same person making deletes appear to do nothing
        self._db_log_cooldowns: dict[str, float] = {}  # identity_key → last_logged_epoch
        self._uniform_ema: dict[str, float] = {}       # identity_key → smoothed P(correct uniform)

        self._enrollment_panel: EnrollmentPanel | None = None
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
        # Deliver notifications to the UI thread (toast + bell badge).
        self._notifier.subscribe(lambda n: self.after(0, self._on_notification, n))
        self._build_menubar()
        self._tick_clock()
        self._schedule_feed_update()
        self.after(80, self._deferred_start_camera)   # start the camera ASAP after the UI maps

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=220)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=280)
        self.grid_rowconfigure(0, weight=1)

        self._build_left_sidebar()
        self._build_center_panel()
        self._build_right_sidebar()

    def _build_left_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self,
            width=SIDEBAR_LEFT_WIDTH,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(PADDING, 0), pady=PADDING)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="CBVMS", font=heading_font(26), text_color=COLOR_ACCENT,
        ).pack(anchor="w", padx=PADDING, pady=(PADDING_LG, 0))

        ctk.CTkLabel(
            sidebar, text="Vision Monitoring System",
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
            ("enrollment", "👤  Student Enrollment"),
            ("violations", "⚠  Violation Log"),
            ("records",    "🗄  Records"),
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
            footer, text=f"Logged in as {self.username}",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkButton(
            footer, text="Logout", height=36, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_DANGER, command=self._logout,
        ).pack(fill="x")

    def _build_center_panel(self) -> None:
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.grid(row=0, column=1, sticky="nsew", padx=PADDING, pady=PADDING)
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(1, weight=1)

        title_row = ctk.CTkFrame(center, fg_color="transparent")
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
            corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER, height=48,
        )
        status_bar.grid(row=2, column=0, sticky="ew")
        status_bar.grid_propagate(False)

        self._status_camera = ctk.CTkLabel(
            status_bar, text="Camera: Starting…",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        )
        self._status_camera.pack(side="left", padx=PADDING, pady=12)

        self._camera_spinner = ctk.CTkProgressBar(status_bar, mode="indeterminate", width=140)
        self._camera_spinner.pack(side="right", padx=(0, 10), pady=14)
        self._camera_spinner.stop()
        self._camera_spinner.pack_forget()

        self._status_fps = ctk.CTkLabel(
            status_bar, text="FPS: —",
            font=body_small_font(), text_color=COLOR_ACCENT,
        )
        self._status_fps.pack(side="right", padx=PADDING)

        self._enrollment_panel = EnrollmentPanel(
            self._view_host,
            database=self._database,
            recognizer=self._recognizer,
            get_frame=self._get_camera_frame,
        )
        self._enrollment_panel.grid_remove()

        self._violation_panel = ViolationLogPanel(self._view_host, database=self._database)
        self._violation_panel.grid_remove()

        self._training_panel = TrainingPanel(
            self._view_host,
            trainer=self._trainer,
            get_frame=self._get_camera_frame,
        )
        self._training_panel.grid_remove()

        self._settings_panel = SettingsPanel(
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
        )
        self._settings_panel.grid_remove()

        self._notifications_panel = NotificationsPanel(
            self._view_host, notifier=self._notifier, on_change=self._update_bell_badge,
        )
        self._notifications_panel.grid_remove()

        self._records_panel = RecordsPanel(self._view_host, database=self._database)
        self._records_panel.grid_remove()

        self._account_manager_panel = AccountManagerPanel(
            self._view_host, database=self._database)
        self._account_manager_panel.grid_remove()

        self._views: dict[str, ctk.CTkFrame] = {
            "live":       self._live_frame,
            "enrollment": self._enrollment_panel,
            "violations": self._violation_panel,
            "records":    self._records_panel,
            "training":   self._training_panel,
            "accounts":   self._account_manager_panel,
            "settings":   self._settings_panel,
            "alerts":     self._notifications_panel,
        }
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
        self._stat_unreviewed_value = _card(1, label="Unreviewed",           accent=COLOR_WARNING)
        self._stat_students_value = _card(2, label="Students Enrolled",       accent=COLOR_ACCENT)
        self._stat_last_value     = _card(3, label="Last Detection",          accent=COLOR_SAFE)
        return row

    def _build_right_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self, width=SIDEBAR_RIGHT_WIDTH, fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER,
        )
        sidebar.grid(row=0, column=2, rowspan=4, sticky="nsew", padx=(0, 10), pady=10)
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            sidebar, text="Live Alerts", font=heading_font(18), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, PADDING))

        self._alerts_scroll = ctk.CTkScrollableFrame(
            sidebar, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS,
        )
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

    def _on_nav_select(self, key: str) -> None:
        if key not in self._views:
            return
        previous_nav = self._active_nav
        self._active_nav = key

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

        titles = {
            "live":       "Live Monitor",
            "enrollment": "Student Enrollment",
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
            if key == "training" and self._training_panel is not None:
                self._training_panel.on_show()
            if key == "violations" and self._violation_panel is not None:
                self._violation_panel.refresh()
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
            count = self._notifier.unread_count()
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
        self._mark_all_read()

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
        self._face_frame_counter = 0   # so the first frame after (re)start is scanned at once
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
                    self._camera_events.put(("opened", generation, cap, ok))
                    while ok and not stop.is_set():
                        frame = cap.read()
                        if frame is None:
                            if not cap.is_open:
                                break
                            time.sleep(0.005)
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

    # ------------------------------------------------------------------
    # Background face detection worker
    # ------------------------------------------------------------------

    def _prewarm_models(self) -> None:
        """Load + prime the heavy models at launch so Live Monitor scans immediately.

        Runs on a daemon thread (pure CV work, no Tk). A tiny dummy inference primes the
        ONNX/torch graphs so the very first real frame isn't slowed by graph allocation.
        """
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        try:
            if self._recognizer._ensure_models():
                self._recognizer.detect_faces(dummy)        # prime SCRFD detection graph
        except Exception as exc:
            print(f"[CBVMS] recognizer prewarm failed: {exc}")
        try:
            if self._person_detector is not None:
                self._person_detector._ensure_model()
                self._person_detector.detect_persons(dummy)  # prime YOLO graph
        except Exception as exc:
            print(f"[CBVMS] person-detector prewarm failed: {exc}")
        # Warm any already-trained violation classifiers so the first check isn't delayed.
        for module in ("uniform", "earring"):
            try:
                if self._trainer.is_trained(module):
                    self._trainer._get_model(module)
            except Exception:
                pass
        # Build the uniform COLOUR reference from the inference-matched torso crops
        # (data/training_cropped via _source_label_dir) so the hue is learned from the shirt,
        # not the scene background. The live verdict is classifier AND colour (min); the
        # ResNet18 embedder was dropped — Step 4 showed it vetoed correctly-worn polos — so it
        # is no longer built here.
        try:
            correct_dir = self._trainer._source_label_dir("uniform", "correct_uniform")
            n_have = len(list(correct_dir.glob("*.jpg"))) if correct_dir.exists() else 0
            if n_have >= 1 and (not self._uniform_matcher.is_loaded()
                                or self._uniform_matcher.sample_count != n_have):
                self._uniform_matcher.build_from_dir(correct_dir)
        except Exception as exc:
            print(f"[CBVMS] uniform reference build failed: {exc}")
        # Warm the pose model so the first live uniform check doesn't stall on download/load.
        try:
            if self._person_detector is not None:
                self._person_detector._ensure_pose_model()
        except Exception:
            pass

    def _detect_worker_loop(self) -> None:
        """Background thread: fast SCRFD-only detection → fresh boxes for the tracker.

        Cheap (~52ms) so it runs often, giving each square a near-real-time position
        that follows head motion. Identity is handled separately by the recognize worker.
        """
        while True:
            try:
                frame = self._face_queue.get(timeout=1.0)
                boxes = [d["box"] for d in self._recognizer.detect_faces(frame)]
                # Hand off to the UI thread via a queue — never call Tk from here.
                _put_latest(self._boxes_out, boxes)
            except queue.Empty:
                pass
            except Exception as exc:
                print(f"[CBVMS] detect worker error: {exc}")

    def _recognize_worker_loop(self) -> None:
        """Background thread: slow ArcFace identity (~520ms), run only when needed.

        Delivers identities to the UI tracker first (labels appear fast), then runs the
        uniform/earring classifiers and re-binds the enriched results so the torso/
        violation overlay follows on a later tick.
        """
        while True:
            try:
                frame = self._recog_queue.get(timeout=1.0)
                detections = self._recognizer.recognize_faces(frame)
                # Identity + alerts first (fast label), then enrich with violations.
                # All applied on the UI thread — never call Tk from here.
                _put_latest(self._ident_out, detections)
                # Uniform/earring classifiers mutate the det dicts in place; re-bind so
                # the tracks pick up violation/torso fields on the next render tick.
                self._check_violations(detections, frame)
                _put_latest(self._violen_out, detections)
            except queue.Empty:
                pass
            except Exception as exc:
                print(f"[CBVMS] recognize worker error: {exc}")

    @staticmethod
    def _match_face_to_person(face_box, person_boxes):
        """Return the person box that best contains the face (overlap/face_area ≥ 0.5)."""
        fx1, fy1, fx2, fy2 = face_box
        fa = max(1, (fx2 - fx1) * (fy2 - fy1))
        best, best_ov = None, 0.0
        for pb in person_boxes:
            px1, py1, px2, py2 = pb
            ix1, iy1 = max(fx1, px1), max(fy1, py1)
            ix2, iy2 = min(fx2, px2), min(fy2, py2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            ov = inter / fa
            if ov > best_ov:
                best_ov, best = ov, pb
        return best if best_ov >= 0.5 else None

    def _check_violations(self, detections: list[dict], frame: np.ndarray | None) -> None:
        """Background-thread: run uniform/earring classifiers on each enrolled person.

        Attaches per-detection: violation (str|None), uniform_label, uniform_conf,
        torso_box. Logs real violations / unknown persons to the DB (300s cooldown).
        """
        for det in detections:
            det["violation"] = None
            det["uniform_label"] = None
            det["uniform_conf"] = 0.0
            det["torso_box"] = None
        if frame is None:
            return

        h, w = frame.shape[:2]

        # Person detection runs once per frame, only when uniform checking is useful.
        person_boxes: list = []
        uniform_on = (
            self._person_detector is not None
            and self._checker.check_uniform
            and (self._trainer.is_trained("uniform") or self._uniform_matcher.is_loaded())
        )
        if uniform_on and any(d.get("matched") for d in detections):
            person_boxes = self._person_detector.detect_persons(frame)

        earring_on = self._checker.check_earring and self._trainer.is_trained("earring")

        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det["box"]]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue

            if not det.get("matched"):
                # Unknown person: log for security (no classifier run).
                self._log_db(det, frame, [x1, y1, x2, y2], "unknown_person")
                continue

            parts: list[str] = []
            person_box = None

            # --- Uniform check (torso/shirt region) ---
            if uniform_on:
                # The torso crop is anchored to the recognised FACE box with a HARD floor: its
                # top is forced below the chin (chin_y + 0.10*face_h), so it is geometrically
                # impossible to land on the face at any range/pose. Pose shoulders+hips refine
                # when available; the face-anthropometry chest box is the guaranteed fallback
                # (works even with no YOLO person box). See PersonDetector.chest_region.
                person_box = None
                if person_boxes:
                    person_box = self._match_face_to_person([x1, y1, x2, y2], person_boxes)
                    if person_box is None:
                        # Single-subject entrance: a clearly-visible person must not lose the
                        # torso box just because the face↔person overlap dipped below 0.5.
                        person_box = person_boxes[0]   # largest by area (detect_persons sorts)
                region = None
                region_method = "none"
                try:
                    region, region_method = self._person_detector.chest_region(
                        frame, [x1, y1, x2, y2], person_box
                    )
                except Exception as exc:
                    print(f"[CBVMS] torso localize error: {exc}")
                if region is not None:
                    ux1, uy1, ux2, uy2 = region
                    uniform_crop = frame[uy1:uy2, ux1:ux2]

                    # Skin guard AFTER clamping: with the top below the chin this should never
                    # trip, but if the final crop is still mostly bare skin, abstain rather than
                    # classify a face. The earring check below still runs. (rule 4)
                    skin = skin_fraction(uniform_crop)
                    if skin > UNIFORM_SKIN_ABSTAIN:
                        print(f"[CBVMS] uniform abstain ({region_method}): crop skin={skin:.0%} "
                              f"> {UNIFORM_SKIN_ABSTAIN:.0%}  face={[x1, y1, x2, y2]} region={region}")
                    else:
                        # Classifier P(correct) — YOLOv8-cls trained on 0.20-0.65 torso crops
                        # (data/training_cropped). The live crop is now the face-anchored
                        # chest_region; the classifier generalizes to it cleanly (verified: 100%
                        # correct/wrong separation on chest_region-cropped training data — it
                        # learned shirt, not framing). Replaces the ResNet18 embedder, which was
                        # mis-calibrated to the live crop and vetoed correct polos (Step 4 diagnosis).
                        p_cls = None
                        proba = self._trainer.predict_proba("uniform", uniform_crop)
                        if proba is not None:
                            p_cls = proba.get("correct_uniform")

                        # Colour-matcher P(correct) — uniform-colour signal (co-required veto).
                        p_col = None
                        if self._uniform_matcher.is_loaded():
                            verdict, p = self._uniform_matcher.is_uniform(uniform_crop)
                            if verdict is not None:   # None = too dark/small to judge → skip
                                p_col = p

                        # "Correct" needs BOTH stable signals to agree (min): the classifier confirms
                        # the garment, colour confirms the uniform colour. Either can veto. Both run
                        # on the identical torso crop used at train/reference time.
                        p_fused = fuse_uniform_prob(p_cls, p_col)
                        if p_fused is not None:
                            # Smooth per person so the shown % is stable, not flickering frame-to-frame.
                            key = det.get("student_id") or det.get("name") or "unknown"
                            prev = self._uniform_ema.get(key)
                            ema = p_fused if prev is None else (
                                UNIFORM_EMA_ALPHA * p_fused + (1.0 - UNIFORM_EMA_ALPHA) * prev
                            )
                            self._uniform_ema[key] = ema

                            is_correct = ema >= 0.5
                            # Confidence = probability of the verdict actually shown.
                            confidence = ema if is_correct else (1.0 - ema)
                            if confidence >= UNIFORM_MIN_CONF:
                                det["uniform_label"] = "correct_uniform" if is_correct else "wrong_uniform"
                                det["uniform_conf"] = confidence
                                det["torso_box"] = region
                                # Show the wrong verdict at MIN_CONF, but only log/alert above the
                                # stricter VIOLATION_CONF bar to avoid borderline false alarms.
                                if not is_correct and confidence >= UNIFORM_VIOLATION_CONF:
                                    parts.append(f"Wrong uniform ({confidence:.0%})")

            # --- Earring check (face crop, male only) ---
            if earring_on and (det.get("gender", "") or "").lower() == "male":
                try:
                    label, conf = self._trainer.predict("earring", frame[y1:y2, x1:x2])
                except Exception as exc:
                    print(f"[CBVMS] earring predict error: {exc}")
                    label, conf = None, 0.0
                if label == "with_earring" and conf >= 0.65:
                    parts.append(f"Earring detected ({conf:.0%})")

            det["violation"] = ", ".join(parts) if parts else None
            if det["violation"]:
                # Snapshot: full person box if known, else the face box.
                snap_box = person_box if person_box is not None else [x1, y1, x2, y2]
                self._log_db(det, frame, snap_box, det["violation"])

    def _log_db(self, det: dict, frame: np.ndarray, box: list[int], violation_type: str) -> None:
        """Persist a violation / unknown person with a 300s per-person cooldown."""
        key = det.get("student_id") or "unknown"
        cooldown_key = f"{key}:{'unknown' if violation_type == 'unknown_person' else 'violation'}"
        now = time.monotonic()
        if now - self._db_log_cooldowns.get(cooldown_key, 0.0) < DB_LOG_COOLDOWN_SECS:
            return
        self._db_log_cooldowns[cooldown_key] = now

        snapshot_jpeg: bytes | None = None
        try:
            bx1, by1, bx2, by2 = [int(v) for v in box]
            h, w = frame.shape[:2]
            bx1, by1 = max(0, bx1), max(0, by1)
            bx2, by2 = min(w, bx2), min(h, by2)
            crop = frame[by1:by2, bx1:bx2]
            if crop.size > 0:
                ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    snapshot_jpeg = buf.tobytes()
        except Exception:
            pass

        try:
            self._database.log_violation(
                student_id=key,
                student_name=det.get("name", "Unknown"),
                violation_type=violation_type,
                snapshot_jpeg=snapshot_jpeg,
                status="unreviewed",
            )
        except Exception as exc:
            print(f"[CBVMS] log_violation error: {exc}")
            return

        # Fire a real-time notification (sound + toast + bell badge + log panel).
        # Cooldown above already de-duplicates, so this won't spam per frame.
        display_name = (det.get("name") or "Unknown") if violation_type != "unknown_person" else "Unidentified person"
        display_violation = "Unidentified person detected" if violation_type == "unknown_person" else violation_type
        self._notifier.notify(display_name, display_violation)

        # Flag for the UI thread to refresh the violations panel — never touch Tk here
        # (this runs on the recognize worker thread).
        self._violation_dirty = True

    def _on_identities_ready(self, detections: list[dict]) -> None:
        """UI-thread callback: bind identities to tracks and fire presence-based alerts.

        One alert fires when a face first appears. While the face stays in frame,
        last_seen is refreshed and no duplicate alert is emitted. After PRESENCE_TIMEOUT_SECS
        without a detection the entry is purged — the next appearance fires a new alert.
        """
        self._tracker.assign_identities(detections)
        now = time.time()

        current_keys: set[str] = set()
        for det in detections:
            key = det["student_id"] if det["matched"] else "unknown"
            current_keys.add(key)

            last_seen = self._face_presence.get(key, 0.0)
            is_new_appearance = (now - last_seen) > PRESENCE_TIMEOUT_SECS

            self._face_presence[key] = now   # always refresh last-seen timestamp

            if is_new_appearance:
                self._push_alert(det)

        # Remove identities that have left the frame long enough
        stale = [
            k for k, t in self._face_presence.items()
            if k not in current_keys and (now - t) > PRESENCE_TIMEOUT_SECS
        ]
        for k in stale:
            del self._face_presence[k]

    def _push_alert(self, det: dict, frame: np.ndarray | None = None) -> None:
        """Add one ephemeral live-alert card. DB logging happens in the worker."""
        entry = {
            "identity_key": det["student_id"] or "unknown",
            "name": det["name"],
            "student_id": det["student_id"] or "—",
            "gender": det.get("gender", "—"),
            "matched": det["matched"],
            "violation": det.get("violation"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "epoch": time.time(),
        }
        self._alerts.appendleft(entry)
        self._refresh_alerts_ui()

    @staticmethod
    def _draw_pill(out, x: int, y_baseline: int, text: str, color) -> None:
        """Draw a filled label pill anchored with its bottom edge at y_baseline."""
        (lw, lh), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        top = max(0, y_baseline - lh - 8)
        cv2.rectangle(out, (x, top), (x + lw + 6, y_baseline), color, -1)
        cv2.putText(out, text, (x + 3, y_baseline - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

    def _annotate_frame(self, frame: np.ndarray, mirror: bool = False) -> np.ndarray:
        """Draw a square + label per tracked face, plus orange torso/uniform overlays.

        Boxes come from the tracker (fast SCRFD positions, IoU-locked to one face each);
        identity/color/violation come from the recognizer once it has identified the track.

        When ``mirror`` is set, the display frame is horizontally flipped and each box's
        x-coordinates are mirrored (``x1, x2 = w - x2, w - x1``) so boxes still line up while
        labels are drawn upright. The caller keeps feeding the un-mirrored ``frame`` to the
        detection/recognition queues, so mirroring is display-only.
        """
        tracks = self._tracker.renderable()
        out = cv2.flip(frame, 1) if mirror else frame
        if not tracks:
            return out
        out = out.copy()
        w = out.shape[1]

        def _mx(x1: int, x2: int) -> tuple[int, int]:
            return (w - x2, w - x1) if mirror else (x1, x2)

        for tr in tracks:
            x1, y1, x2, y2 = tr.box_int()
            x1, x2 = _mx(x1, x2)

            # Not yet identified by the (slower) recognizer — neutral box, no premature label.
            if not tr.identified:
                gray = (148, 163, 184)
                cv2.rectangle(out, (x1, y1), (x2, y2), gray, 2)
                self._draw_pill(out, x1, y1, "Scanning…", gray)
                continue

            matched = tr.matched
            # Face colour follows the SMOOTHED uniform verdict (not the raw per-cycle one) so it
            # can't flicker red/green out of sync with the torso label. Non-uniform violations
            # (e.g. earring) still come from the raw violation string.
            uniform_wrong = (tr.stable_uniform_label == "wrong_uniform")
            other_violation = bool(tr.violation) and "uniform" not in (tr.violation or "").lower()
            has_violation = uniform_wrong or other_violation

            # Face box: green (OK) / red (violation) / blue (unknown)
            if not matched:
                face_color = (239, 68, 68)        # BGR blue-ish for unknown
            elif has_violation:
                face_color = (68, 68, 239)         # BGR red
            else:
                face_color = (16, 185, 129)        # BGR green
            cv2.rectangle(out, (x1, y1), (x2, y2), face_color, 2)
            self._draw_pill(out, x1, y1, tr.name if matched else "Unknown", face_color)

            # Torso box: orange + uniform prediction label
            torso_box = tr.torso_box
            if torso_box is not None:
                tx1, ty1, tx2, ty2 = [int(v) for v in torso_box]
                tx1, tx2 = _mx(tx1, tx2)
                cv2.rectangle(out, (tx1, ty1), (tx2, ty2), ORANGE_BGR, 2)
                if tr.stable_uniform_label is not None:
                    conf = tr.stable_uniform_conf
                    if tr.stable_uniform_label == "wrong_uniform":
                        tag = f"X Wrong uniform {conf:.0%}"
                    else:
                        tag = f"OK Uniform {conf:.0%}"
                    self._draw_pill(out, tx1, ty1, tag, ORANGE_BGR)
        return out

    # ------------------------------------------------------------------
    # Feed update loop
    # ------------------------------------------------------------------

    def _schedule_feed_update(self) -> None:
        self._feed_job = self.after(self._feed_interval_ms, self._update_feed)

    def _update_feed(self) -> None:
        if not self.winfo_exists():
            return
        try:
            # Camera workers communicate only through this queue; all Tk updates stay
            # on the main thread.
            self._drain_camera_events()

            # A worker logged a violation — refresh the panel here, on the UI thread.
            if self._violation_dirty:
                self._violation_dirty = False
                if self._active_nav == "violations" and self._violation_panel is not None:
                    self._violation_panel.refresh()

            # Power saver: release the camera device when no consumer needs it.
            if self._camera is not None and not self._camera_needed():
                self._halt_camera()

            if self._active_nav == "live":
                if self._camera and self._camera.is_open:
                    # Non-blocking: the reader thread keeps this fresh. Never call
                    # read() here or a slow network frame would freeze the UI.
                    frame = self._camera.get_latest_frame()
                    if frame is not None:
                        _now = time.time()
                        self._last_frame_request = time.monotonic()  # keep camera alive
                        # Drain worker results on the UI thread (workers never call Tk).
                        _boxes = _drain(self._boxes_out)
                        if _boxes is not None:
                            self._tracker.update(_boxes)
                        _ident = _drain(self._ident_out)
                        if _ident is not None:
                            self._on_identities_ready(_ident)        # assign + presence alerts
                        _vio = _drain(self._violen_out)
                        if _vio is not None:
                            self._tracker.assign_identities(_vio)     # overlay enrich only

                        # Keep presence timestamps alive for currently-tracked identities so
                        # the recognition gap can't falsely reset presence and re-fire alerts.
                        for _tr in self._tracker.renderable():
                            if not _tr.identified:
                                continue
                            _key = _tr.student_id if _tr.matched else "unknown"
                            if _key in self._face_presence:
                                self._face_presence[_key] = _now

                        self._face_frame_counter += 1
                        # Fast detection: scan the very first frame immediately, then
                        # every Nth frame for smooth box tracking.
                        if self._face_frame_counter == 1 or self._face_frame_counter % DETECT_EVERY_N_FRAMES == 0:
                            try:
                                self._face_queue.put_nowait(frame.copy())
                            except queue.Full:
                                pass
                        # Slow recognition: only when a track needs an identity, or on a
                        # periodic refresh — and never more often than REID_MIN_GAP_SECS.
                        need_reid = (
                            self._tracker.has_unidentified()
                            or (_now - self._last_recog_offer) >= REID_INTERVAL_SECS
                        )
                        if need_reid and (_now - self._last_recog_offer) >= REID_MIN_GAP_SECS:
                            try:
                                self._recog_queue.put_nowait(frame.copy())
                                self._last_recog_offer = _now
                            except queue.Full:
                                pass
                        # Annotate from the tracker and render (mirror is display-only).
                        annotated = self._annotate_frame(frame, mirror=self._mirror.display_mirror())
                        self.camera_feed.render(self._mirror.apply_anim(annotated))
                    else:
                        self.camera_feed.show_placeholder()
                else:
                    # On Live but the camera is off/reopening (e.g. just released for
                    # power saving, or warming up) — show the placeholder meanwhile.
                    self.camera_feed.show_placeholder()
            # Non-live navs: render nothing (the live canvas is hidden anyway).
        except Exception as exc:
            print(f"[CBVMS] feed error: {exc}")
        self._feed_job = self.after(self._feed_interval_ms, self._update_feed)

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
        try:
            with self._database.connect() as conn:
                today      = conn.execute("SELECT COUNT(*) AS c FROM violations WHERE date(timestamp) = date('now')").fetchone()
                unreviewed = conn.execute("SELECT COUNT(*) AS c FROM violations WHERE status = 'unreviewed'").fetchone()
                students   = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()
                last       = conn.execute("SELECT MAX(timestamp) AS ts FROM violations").fetchone()

            if self._stat_today_value:
                self._stat_today_value.configure(text=str(int(today["c"] if today else 0)))
            if self._stat_unreviewed_value:
                self._stat_unreviewed_value.configure(text=str(int(unreviewed["c"] if unreviewed else 0)))
            if self._stat_students_value:
                self._stat_students_value.configure(text=str(int(students["c"] if students else 0)))
            if self._stat_last_value:
                ts = (last["ts"] if last else None) or ""
                self._stat_last_value.configure(
                    text=str(ts)[11:19] if len(str(ts)) >= 19 else (str(ts) or "—")
                )
        except Exception:
            pass
        self._stats_job = self.after(10_000, self._refresh_stats)

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _clear_alerts(self) -> None:
        self._alerts.clear()
        self._face_presence.clear()
        self._db_log_cooldowns.clear()   # reset so next detection logs fresh
        self._uniform_ema.clear()        # drop smoothed uniform state for a clean slate
        self._refresh_alerts_ui()

    def _refresh_alerts_ui(self) -> None:
        for child in self._alerts_scroll.winfo_children():
            child.destroy()

        if not self._alerts:
            ctk.CTkLabel(
                self._alerts_scroll, text="No alerts yet",
                font=body_font(12), text_color=COLOR_TEXT_MUTED,
            ).pack(pady=20)
            return

        for entry in self._alerts:
            matched = entry["matched"]
            violation = entry.get("violation")
            violations = violation.split(", ") if violation else []

            # Dot: yellow = unknown, red = violation(s), green = clean
            if not matched:
                dot_color = COLOR_WARNING
            elif violations:
                dot_color = COLOR_DANGER
            else:
                dot_color = COLOR_SAFE

            card = ctk.CTkFrame(
                self._alerts_scroll,
                fg_color=COLOR_SURFACE,
                corner_radius=CORNER_RADIUS,
                border_width=1,
                border_color=COLOR_BORDER,
            )
            card.pack(fill="x", pady=(0, 6))

            # Header row: dot + name + time
            header = ctk.CTkFrame(card, fg_color="transparent")
            header.pack(fill="x", padx=10, pady=(8, 2))

            ctk.CTkLabel(
                header, text="●", font=body_font(14), text_color=dot_color,
            ).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(
                header, text=entry["name"], font=body_font(13),
                text_color=COLOR_TEXT, anchor="w",
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                header, text=entry["time"], font=body_small_font(),
                text_color=COLOR_TEXT_MUTED,
            ).pack(side="right")

            # Details row: student ID + gender
            details = ctk.CTkFrame(card, fg_color="transparent")
            details.pack(fill="x", padx=10, pady=(0, 4))
            ctk.CTkLabel(
                details,
                text=f"ID: {entry['student_id']}   Gender: {entry['gender']}",
                font=body_small_font(), text_color=COLOR_TEXT_MUTED, anchor="w",
            ).pack(side="left")

            # Violation / status row
            if not matched:
                ctk.CTkLabel(
                    card, text="Not enrolled", font=body_small_font(),
                    text_color=COLOR_TEXT_MUTED, anchor="w",
                ).pack(anchor="w", padx=10, pady=(0, 8))
            elif violations:
                pill_row = ctk.CTkFrame(card, fg_color="transparent")
                pill_row.pack(anchor="w", fill="x", padx=10, pady=(0, 8))
                for v in violations:
                    ctk.CTkLabel(
                        pill_row, text=v, font=body_small_font(),
                        text_color=COLOR_DANGER, fg_color="#3A1414",
                        corner_radius=999, padx=8, pady=2,
                    ).pack(side="left", padx=(0, 4), pady=2)
            else:
                ctk.CTkLabel(
                    card, text="✓ OK", font=body_small_font(),
                    text_color=COLOR_SAFE, anchor="w",
                ).pack(anchor="w", padx=10, pady=(0, 8))

        # Scroll to newest (top)
        try:
            self._alerts_scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _logout(self) -> None:
        self._logout_requested = True
        self._on_close()

    def _on_close(self) -> None:
        if self._camera_switch_job is not None:
            try:
                self.after_cancel(self._camera_switch_job)
            except Exception:
                pass
            self._camera_switch_job = None
        for job_attr in ("_feed_job", "_clock_job", "_stats_job"):
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
    return getattr(app, "_logout_requested", False)
