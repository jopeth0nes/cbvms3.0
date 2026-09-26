"""Student self-registration window."""

from __future__ import annotations

from core.student_status import CONTACT_FIELDS, validate_contacts

import os
import queue
import time
from core.registration_camera import RegistrationCamera, PreviewMetrics
from core.face_capture import (CaptureSession, FacePreviewTracker, MAX_FRAME_AGE,
                               capture_payload, guide_geometry)
import threading
import tkinter as tk

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

from database.db_manager import CBVMSDatabase
from ui.components import (
    COLOR_ACCENT, COLOR_ACCENT_HOVER, COLOR_BG, COLOR_BORDER,
    COLOR_DANGER, COLOR_SAFE, COLOR_SURFACE, COLOR_TEXT, COLOR_TEXT_MUTED,
    COLOR_WARNING, CORNER_RADIUS, PADDING, body_font, heading_font,
)

PREVIEW_W = 340
PREVIEW_H = 260
PREVIEW_INTERVAL_MS = 33
VALIDATION_INTERVAL = .25  # at most four full validations/second, one in flight
# Reopening must not overlap expensive model loading/inference from old windows.
_PROCESSING_LOCK = threading.Lock()


class StudentRegistrationWindow(ctk.CTkToplevel):

    def __init__(self, parent, *, database: CBVMSDatabase) -> None:
        super().__init__(parent)
        self.database = database

        self._camera_source = RegistrationCamera()
        self._stop_event = threading.Event()
        self._preview_job = None
        self._success_job = None
        self._last_rendered = None
        self._next_validation = 0.0
        self._tracker = FacePreviewTracker()
        self._feedback_state = None
        self._camera_error = None
        self._metrics = PreviewMetrics()
        self._next_diagnostics = time.monotonic() + 5
        self._model_results = queue.Queue()
        # Reuse one model for repeated registrations under the same login window.
        if not hasattr(parent, "_registration_model"):
            parent._registration_model = {}
        self._model_cache = parent._registration_model
        self._recognizer = None
        self._captured_frame: np.ndarray | None = None
        self._alive = True
        self._capture_session = None
        self._capture_results = queue.Queue()
        self._capture_busy = False
        self._submitting = False

        self.title("Student Registration")
        self.configure(fg_color=COLOR_BG)
        self.geometry("860x580")
        self.resizable(False, False)
        self.transient(parent)
        self._initial_jobs = [self.after(120, self._safe_grab)]
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build_ui()
        self._load_recognizer()
        self._camera_source.start()
        self._live_tick()

    # ------------------------------------------------------------------ camera

    def _load_recognizer(self) -> None:
        self._model_load_failed = False

        def load():
            while not self._stop_event.is_set():
                if not _PROCESSING_LOCK.acquire(timeout=.1):
                    continue
                try:
                    if self._stop_event.is_set():
                        return
                    rec = self._model_cache.get("recognizer")
                    if rec is None:
                        from core.recognizer import FaceRecognizer
                        rec = FaceRecognizer(self.database)
                        if not rec._ensure_models():
                            raise RuntimeError("Face model unavailable")
                        self._model_cache["recognizer"] = rec
                    self._model_results.put((rec, None))
                except Exception as exc:
                    self._model_results.put((None, str(exc)))
                finally:
                    _PROCESSING_LOCK.release()
                return

        threading.Thread(target=load, daemon=True, name="registration-model").start()

    def _start_validation(self, session, sample):
        self._capture_busy = True
        self._next_validation = time.monotonic() + VALIDATION_INTERVAL

        def detect():
            while not self._stop_event.is_set():
                if not _PROCESSING_LOCK.acquire(timeout=.1):
                    continue
                started = time.monotonic()
                faces = []
                try:
                    if not self._stop_event.is_set() and session is self._capture_session:
                        faces = self._recognizer.enrollment_faces(sample.frame)
                except Exception:
                    faces = []
                finally:
                    _PROCESSING_LOCK.release()
                if not self._stop_event.is_set():
                    self._capture_results.put((session, sample, faces, time.monotonic()-started))
                return
            # Closed windows never schedule Tk callbacks or enqueue new work.

        threading.Thread(target=detect, daemon=True, name="registration-validation").start()

    def _feedback(self, message, *, ready=False, frozen=False):
        color = COLOR_SAFE if ready or frozen else COLOR_TEXT_MUTED
        button_text = "🔄  Retake" if frozen else "📷  Capture Face"
        button_state = "normal" if (ready or frozen) and not self._submitting else "disabled"
        value = (message, color, button_text, button_state)
        previous = self._feedback_state
        if previous is None or previous[:2] != value[:2]:
            self._cam_status.configure(text=message, text_color=color)
            self._dot.configure(text_color=color)
        if previous is None or previous[2:] != value[2:]:
            self._cap_btn.configure(text=button_text, state=button_state)
        self._feedback_state = value

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # ── Header ──────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=PADDING, pady=(PADDING, 6))
        ctk.CTkLabel(hdr, text="Student Registration",
                     font=heading_font(18), text_color=COLOR_TEXT).pack(side="left")

        # ── Two-column body ─────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PADDING, pady=0)
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=4)
        body.rowconfigure(0, weight=1)

        self._build_form(body)
        self._build_camera_panel(body)

        # ── Footer ──────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=PADDING, pady=(6, PADDING))

        self._err_lbl = ctk.CTkLabel(footer, text="", font=body_font(11),
                                     text_color=COLOR_DANGER, anchor="w", wraplength=500)
        self._err_lbl.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(footer, text="Cancel", width=110, height=40,
                      corner_radius=CORNER_RADIUS, fg_color=COLOR_BORDER,
                      hover_color=COLOR_DANGER, command=self._close).pack(side="right")
        ctk.CTkButton(footer, text="Register", width=130, height=40,
                      corner_radius=CORNER_RADIUS, fg_color=COLOR_ACCENT,
                      hover_color=COLOR_ACCENT_HOVER, font=heading_font(13),
                      command=self._submit).pack(side="right", padx=(0, 8))

    # ── Left: registration form ─────────────────────────────────────────

    def _build_form(self, parent) -> None:
        card = ctk.CTkScrollableFrame(parent, fg_color=COLOR_SURFACE,
                            corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 0))
        card.columnconfigure(1, weight=1)

        def row(r, label, widget_fn):
            ctk.CTkLabel(card, text=label, font=body_font(12),
                         text_color=COLOR_TEXT_MUTED, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(PADDING, 10), pady=(12, 0))
            w = widget_fn(card)
            w.grid(row=r, column=1, sticky="ew", padx=(0, PADDING), pady=(12, 0))
            return w

        def entry(ph="", show=""):
            return lambda p: ctk.CTkEntry(p, placeholder_text=ph, height=38,
                                          corner_radius=CORNER_RADIUS, fg_color=COLOR_BG,
                                          border_color=COLOR_BORDER, show=show)

        self._e_name     = row(0, "Full Name",        entry("e.g. Juan Dela Cruz"))
        self._e_username = row(1, "Username",         entry("choose a username"))
        self._e_password = row(2, "Password",         entry("at least 6 characters", show="•"))
        self._e_sid      = row(3, "Student ID",       entry("e.g. 2024-00001"))
        self._e_course   = row(4, "Course",           entry("e.g. BSIT"))
        self._e_year     = row(5, "Year & Section",   entry("e.g. 2ND YEAR - A"))

        # Gender
        ctk.CTkLabel(card, text="Gender", font=body_font(12),
                     text_color=COLOR_TEXT_MUTED, anchor="w").grid(
            row=6, column=0, sticky="w", padx=(PADDING, 10), pady=(12, 0))
        self._gender_var = ctk.StringVar(value="Male")
        ctk.CTkSegmentedButton(card, values=["Male", "Female"],
                               variable=self._gender_var, height=36).grid(
            row=6, column=1, sticky="ew", padx=(0, PADDING), pady=(12, 0))

        self._contact_entries = {}
        for index, (label, key) in enumerate(CONTACT_FIELDS, start=7):
            self._contact_entries[key] = row(index, label, lambda p: ctk.CTkEntry(p, height=36))
        # spacer
        ctk.CTkFrame(card, fg_color="transparent", height=12).grid(
            row=7 + len(CONTACT_FIELDS), column=0, columnspan=2)

    # ── Right: face capture ─────────────────────────────────────────────

    def _build_camera_panel(self, parent) -> None:
        card = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE,
                            corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 0))
        card.columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Face Registration",
                     font=heading_font(14), text_color=COLOR_TEXT).pack(
            anchor="w", padx=PADDING, pady=(PADDING, 4))
        ctk.CTkLabel(card,
                     text="Position your face in the frame,\nthen click Capture.",
                     font=body_font(11), text_color=COLOR_TEXT_MUTED,
                     justify="left").pack(anchor="w", padx=PADDING, pady=(0, 8))

        # Canvas — live feed OR captured snapshot
        self._canvas = tk.Canvas(card, width=PREVIEW_W, height=PREVIEW_H,
                                 bg="#0d0d0d", highlightthickness=0)
        self._canvas.pack(padx=PADDING, pady=(0, 6))
        self._canvas_item = None
        self._canvas_img  = None

        # Placeholder text while camera is loading
        self._canvas.create_text(
            PREVIEW_W // 2, PREVIEW_H // 2,
            text="Loading camera…", fill="#888888",
            font=("Helvetica", 12), tags="placeholder")

        # Status dot + text
        srow = ctk.CTkFrame(card, fg_color="transparent")
        srow.pack(pady=(0, 6))
        self._dot = ctk.CTkLabel(srow, text="●", font=body_font(16),
                                 text_color=COLOR_TEXT_MUTED)
        self._dot.pack(side="left", padx=(0, 6))
        self._cam_status = ctk.CTkLabel(srow, text="Starting camera…",
                                        font=body_font(12), text_color=COLOR_TEXT_MUTED,
                                        wraplength=PREVIEW_W - 30)
        self._cam_status.pack(side="left")

        # Capture / Retake button
        self._cap_btn = ctk.CTkButton(
            card, text="📷  Capture Face", height=40, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            font=heading_font(13), command=self._capture_face,
            state="disabled")   # enabled once camera is confirmed open
        self._cap_btn.pack(fill="x", padx=PADDING, pady=(0, PADDING))

        # The camera owner and the single UI preview callback start after UI creation.

    # ------------------------------------------------------------------ live feed

    def _capture_key(self):
        return ("register", self._e_sid.get().strip(), self._e_name.get().strip(),
                self._e_username.get().strip())

    def _live_tick(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        started = time.monotonic()
        # A single owned callback polls camera/model events and paints newest pixels.
        # Neither VideoCapture.read nor face inference runs on the Tk thread.
        try:
            self._recognizer, error = self._model_results.get_nowait()
            self._model_load_failed = error is not None
        except queue.Empty:
            pass
        try:
            event, value = self._camera_source.events.get_nowait()
            if event == "opened":
                self._camera_error = None
                print(f"[Registration camera] requested 1280x720 @ 30 FPS; reported {value}")
            else:
                self._camera_error = value
        except queue.Empty:
            pass
        key = self._capture_key()
        if self._capture_session is None or self._capture_session.student_key != key:
            self._capture_session = CaptureSession(key)
            self._captured_frame = None
            self._tracker.clear()
            self._last_rendered = None
        session = self._capture_session
        sample = self._camera_source.latest()
        now = time.monotonic()
        fresh = sample is not None and 0 <= now-sample.captured_at <= MAX_FRAME_AGE
        if session.frozen is None and not fresh:
            session.invalidate()
            self._tracker.clear()
        try:
            owner, analyzed, faces, seconds = self._capture_results.get_nowait()
            self._capture_busy = False
            self._metrics.inference_seconds.append(seconds)
            if (owner is session and session.frozen is None and fresh and
                    sample.frame_id[0] == analyzed.frame_id[0]):
                if not session.pending or analyzed.captured_at > self._requested_at:
                    face = session.observe(analyzed, faces, 0, now)
                    if face is not None:
                        self._tracker.reset(analyzed, face[0])
                    else:
                        self._tracker.clear()
                    if session.frozen is not None:
                        self._captured_frame = session.frozen.frame
                        crop = cv2.imdecode(np.frombuffer(session.frozen.photo, np.uint8), cv2.IMREAD_COLOR)
                        self._render_frame(crop, frozen=True)
        except queue.Empty:
            pass
        if session.frozen is None:
            box = self._tracker.advance(sample) if fresh else None
            if session.previous is not None and box is None:
                session.invalidate("Tracking uncertain. Hold still inside the guide.")
            if session.last_sample and now-session.last_sample.captured_at > MAX_FRAME_AGE:
                session.invalidate()
                self._tracker.clear()
                box = None
            if fresh and sample.frame_id != self._last_rendered:
                self._render_frame(sample.frame, box)
                self._last_rendered = sample.frame_id
                self._metrics.rendered(sample, time.monotonic())
            elif not fresh and self._last_rendered is not None:
                self._canvas.delete("all")
                self._canvas_item = None
                self._last_rendered = None
            if (fresh and self._recognizer is not None and not self._capture_busy and
                    started >= self._next_validation and
                    (session.last_sample is None or sample.frame_id != session.last_sample.frame_id)):
                self._start_validation(session, sample)
            message = (self._camera_error or
                       ("Face model unavailable — capture disabled" if self._model_load_failed else
                        "Loading face model…" if self._recognizer is None else
                        "Capturing — hold still" if session.pending else session.message))
            self._feedback(message, ready=session.ready and not session.pending)
        else:
            self._feedback("Frozen face preview — Register to save, or Retake", frozen=True)
        if os.environ.get("CBVMS_CAMERA_DIAGNOSTICS") == "1" and started >= self._next_diagnostics:
            print(f"[Registration preview] {self._metrics.summary(self._camera_source.frame_times)}")
            self._next_diagnostics = started + 5
        # Compensate for render time; after(33) *after* work would lower the cadence.
        delay = max(1, PREVIEW_INTERVAL_MS - round((time.monotonic()-started)*1000))
        self._preview_job = self.after(delay, self._live_tick)

    def _render_frame(self, frame, box=None, *, frozen=False):
        # Resize once before drawing/converting; registration retains full-res samples.
        disp = cv2.resize(frame, (PREVIEW_W, PREVIEW_H)) if not frozen else frame
        if not frozen:
            cx, cy, rx, ry = guide_geometry(disp.shape)
            cv2.ellipse(disp, (round(cx), round(cy)), (round(rx), round(ry)),
                        0, 0, 360, (255, 255, 255), 2)
            if box is not None:
                h, w = frame.shape[:2]
                scale = np.array([PREVIEW_W/w, PREVIEW_H/h]*2)
                x1, y1, x2, y2 = (np.asarray(box)*scale).astype(int)
                cv2.rectangle(disp, (x1, y1), (x2, y2), (90, 220, 40), 3)
        if frozen:
            h, w = disp.shape[:2]
            scale = min(PREVIEW_W / w, PREVIEW_H / h)
            fitted = cv2.resize(disp, (max(1, round(w*scale)), max(1, round(h*scale))))
            disp = np.zeros((PREVIEW_H, PREVIEW_W, 3), np.uint8)
            fh, fw = fitted.shape[:2]
            x, y = (PREVIEW_W-fw)//2, (PREVIEW_H-fh)//2
            disp[y:y+fh, x:x+fw] = fitted
        rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(image=Image.fromarray(rgb), master=self._canvas)
        self._canvas_img = photo
        if self._canvas_item is None:
            self._canvas.delete("placeholder")
            self._canvas_item = self._canvas.create_image(0, 0, anchor=tk.NW, image=photo)
        else:
            self._canvas.itemconfig(self._canvas_item, image=photo)

    def _capture_face(self) -> None:
        if self._submitting or self._capture_session is None:
            return
        session = self._capture_session
        if session.student_key != self._capture_key():
            return
        if session.frozen is not None:
            self._captured_frame = None
            self._canvas.delete("all")
            self._canvas_item = None
            self._capture_session = CaptureSession(self._capture_key())
            self._tracker.clear()
            self._last_rendered = None
            self._feedback_state = None
            self._cap_btn.configure(text="📷  Capture Face", state="disabled")
            self._err_lbl.configure(text="")
        elif session.request(time.monotonic()):
            self._requested_at = time.monotonic()
            self._next_validation = 0.0
            self._feedback_state = None
            self._cap_btn.configure(state="disabled")

    # ------------------------------------------------------------------ submit

    def _set_err(self, msg: str, *, ok: bool = False) -> None:
        try:
            self._err_lbl.configure(
                text=msg,
                text_color=COLOR_SAFE if ok else COLOR_DANGER)
        except Exception:
            pass

    def _submit(self) -> None:
        if self._submitting or not self._alive:
            return
        name     = self._e_name.get().strip()
        username = self._e_username.get().strip()
        password = self._e_password.get()
        sid      = self._e_sid.get().strip()
        course   = self._e_course.get().strip()
        year     = self._e_year.get().strip()
        gender   = self._gender_var.get()
        try:
            contacts = validate_contacts({key: entry.get() for key, entry in self._contact_entries.items()})
        except ValueError as exc:
            self._set_err(str(exc))
            return

        if not all([name, username, password, sid, course, year]):
            self._set_err("Please fill in all fields.")
            return
        if len(username) < 3:
            self._set_err("Username must be at least 3 characters.")
            return
        if len(password) < 6:
            self._set_err("Password must be at least 6 characters.")
            return
        if self.database.student_id_exists(sid):
            self._set_err(f"Student ID '{sid}' is already registered.")
            return
        if self.database.username_exists(username):
            self._set_err(f"Username '{username}' is already taken.")
            return
        if self._captured_frame is None:
            self._set_err("Position your face inside the guide.")
            return
        # A valid frozen capture always includes the selected face's embedding.
        if self._captured_frame is not None and self._recognizer is None \
                and not self._model_load_failed:
            self._set_err("Face model is still loading — please wait a moment and try again.")
            return

        blob, photo_bytes = b"", b""
        if self._captured_frame is not None:
            try:
                blob, photo_bytes = capture_payload([self._capture_session.frozen], self._capture_key())
            except (ValueError, AttributeError) as exc:
                self._set_err(f"Retake your face for this student: {exc}")
                return
        self._set_err("")
        self._submitting = True
        self._cap_btn.configure(state="disabled")
        # Commit the validated identity + frozen payload together on the UI thread.
        # No camera reads, inference, or mutable form reads after this point.
        try:
            self.database.insert_student(
                student_id=sid, name=name, course=course,
                year_and_section=year, gender=gender,
                encoding=blob, photo=photo_bytes, email=contacts["email"],
                contacts=contacts, registration_pending=True,
            )
            self.database.insert_student_account(sid, username, password)
            if self._recognizer and blob:
                self._recognizer.load_known_faces()
        except Exception as exc:
            self._submitting = False
            self._set_err(f"Registration failed: {exc}")
            self._cap_btn.configure(state="normal")
            return
        self._on_success()

    def _on_success(self) -> None:
        self._set_err("✓ Registered! You can log in; OSA must verify your enrollment.", ok=True)
        self._cap_btn.configure(state="disabled")
        self._success_job = self.after(2000, self._close)

    # ------------------------------------------------------------------ lifecycle

    def _safe_grab(self) -> None:
        try:
            if self.winfo_exists():
                self.grab_set()
        except Exception:
            pass

    def _shutdown_camera(self):
        if not self._alive:
            return
        self._alive = False
        self._stop_event.set()
        self._camera_source.stop()
        for job in [self._preview_job, self._success_job, *self._initial_jobs]:
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self._preview_job = None
        if os.environ.get("CBVMS_CAMERA_DIAGNOSTICS") == "1":
            print(f"[Registration preview final] {self._metrics.summary(self._camera_source.frame_times)}")

    def _on_destroy(self, event):
        if event.widget is self:
            self._shutdown_camera()

    def _close(self) -> None:
        self._shutdown_camera()
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
