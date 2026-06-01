"""Student self-registration window."""

from __future__ import annotations

import io
import pickle
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


class StudentRegistrationWindow(ctk.CTkToplevel):

    def __init__(self, parent, *, database: CBVMSDatabase) -> None:
        super().__init__(parent)
        self.database = database

        self._cap: cv2.VideoCapture | None = None
        self._cap_lock = threading.Lock()
        self._recognizer = None
        self._captured_frame: np.ndarray | None = None
        self._alive = True

        self.title("Student Registration")
        self.configure(fg_color=COLOR_BG)
        self.geometry("860x580")
        self.resizable(False, False)
        self.transient(parent)
        self.after(120, self.lift)
        self.after(250, self._safe_grab)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build_ui()
        self._load_recognizer()
        # Open camera in background so the window appears immediately
        threading.Thread(target=self._open_camera_bg, daemon=True).start()

    # ------------------------------------------------------------------ camera

    def _open_camera_bg(self) -> None:
        """Try camera indices in a background thread, update UI when ready."""
        cap = None
        for idx in range(4):
            for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
                try:
                    c = cv2.VideoCapture(idx, backend)
                    if not c.isOpened():
                        c.release()
                        continue
                    # Warm up: read a few frames to flush the buffer
                    for _ in range(10):
                        c.read()
                    ok, frame = c.read()
                    if ok and frame is not None and frame.size > 0:
                        cap = c
                        break
                    c.release()
                except Exception:
                    pass
            if cap is not None:
                break

        with self._cap_lock:
            self._cap = cap

        try:
            if self._alive and self.winfo_exists():
                self.after(0, self._on_camera_ready, cap is not None)
        except Exception:
            pass

    def _on_camera_ready(self, found: bool) -> None:
        """Called on the UI thread once the background open attempt finishes."""
        if not self._alive or not self.winfo_exists():
            return
        if found:
            self._dot.configure(text_color=COLOR_TEXT_MUTED)
            self._cam_status.configure(text="Camera ready — position your face",
                                       text_color=COLOR_TEXT_MUTED)
            self._cap_btn.configure(state="normal")
            self._live_tick()
            self._detect_tick()
        else:
            self._dot.configure(text_color=COLOR_WARNING)
            self._cam_status.configure(text="No camera detected",
                                       text_color=COLOR_WARNING)
            self._canvas.delete("placeholder")
            self._canvas.create_text(
                PREVIEW_W // 2, PREVIEW_H // 2,
                text="No camera detected", fill="#888888",
                font=("Helvetica", 12))

    def _get_frame(self) -> np.ndarray | None:
        with self._cap_lock:
            cap = self._cap
        if cap and cap.isOpened():
            ok, frame = cap.read()
            return frame if ok else None
        return None

    def _load_recognizer(self) -> None:
        self._model_load_failed = False

        def _load() -> None:
            try:
                from core.recognizer import Recognizer
                rec = Recognizer(self.database)
                rec._ensure_models()
                self._recognizer = rec
            except Exception as exc:
                print(f"[Registration] recognizer load: {exc}")
                self._model_load_failed = True
            try:
                if self._alive and self.winfo_exists():
                    self.after(0, self._on_model_ready)
            except Exception:
                pass

        threading.Thread(target=_load, daemon=True).start()
        # Animate the dot while model is loading
        self.after(500, self._model_loading_tick)

    def _model_loading_tick(self) -> None:
        """Animate camera status dot while face model is still loading."""
        if not self._alive or not self.winfo_exists():
            return
        if self._recognizer is not None or self._model_load_failed:
            return  # model finished — stop ticking
        # Toggle dot color to show activity
        try:
            current = self._cam_status.cget("text")
            if "model" in current.lower() or "camera" in current.lower():
                dots = current.count(".")
                self._cam_status.configure(
                    text="Loading face model" + "." * ((dots % 3) + 1),
                    text_color=COLOR_TEXT_MUTED)
                self._dot.configure(text_color=COLOR_WARNING)
        except Exception:
            pass
        self.after(600, self._model_loading_tick)

    def _on_model_ready(self) -> None:
        """Called on UI thread when the face model finishes loading."""
        if not self._alive or not self.winfo_exists():
            return
        if self._recognizer is not None:
            # Clear any stale "model loading" error
            try:
                if "model" in self._err_lbl.cget("text").lower():
                    self._set_err("")
            except Exception:
                pass
            # If photo already captured, prompt user to register now
            if self._captured_frame is not None:
                self._set_err("✓ Face model ready — you can now click Register.", ok=True)
            # Update detect status
            try:
                self._cam_status.configure(text="Face model ready ✓",
                                           text_color=COLOR_SAFE)
                self._dot.configure(text_color=COLOR_SAFE)
            except Exception:
                pass
        else:
            # Model failed — still allow registration (photo stored, no recognition encoding)
            try:
                if "model" in self._err_lbl.cget("text").lower():
                    self._set_err("")
                self._cam_status.configure(
                    text="Model unavailable — photo only", text_color=COLOR_WARNING)
                self._dot.configure(text_color=COLOR_WARNING)
            except Exception:
                pass

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
        card = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE,
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

        # spacer
        ctk.CTkFrame(card, fg_color="transparent", height=12).grid(
            row=7, column=0, columnspan=2)

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
                                        font=body_font(12), text_color=COLOR_TEXT_MUTED)
        self._cam_status.pack(side="left")

        # Capture / Retake button
        self._cap_btn = ctk.CTkButton(
            card, text="📷  Capture Face", height=40, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            font=heading_font(13), command=self._capture_face,
            state="disabled")   # enabled once camera is confirmed open
        self._cap_btn.pack(fill="x", padx=PADDING, pady=(0, PADDING))

        # Camera opens in background thread (_on_camera_ready will start the feed)

    # ------------------------------------------------------------------ live feed

    def _live_tick(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        if self._captured_frame is None:          # only update when not frozen
            frame = self._get_frame()
            if frame is not None:
                self._render_frame(frame)
        self.after(40, self._live_tick)           # ~25 fps

    def _detect_tick(self) -> None:
        if not self._alive or not self.winfo_exists():
            return
        # Don't overwrite status when photo already captured or model still loading
        if self._captured_frame is not None:
            self.after(400, self._detect_tick)
            return
        with self._cap_lock:
            has_cam = self._cap is not None
        if not has_cam:
            self.after(400, self._detect_tick)
            return
        rec = self._recognizer
        if rec is None:
            # Model still loading — _model_loading_tick handles the dot animation
            self.after(400, self._detect_tick)
            return
        # Model ready — show face detection status
        frame = self._get_frame()
        if frame is not None and rec.has_face(frame):
            self._dot.configure(text_color=COLOR_SAFE)
            self._cam_status.configure(text="Face detected — ready to capture",
                                       text_color=COLOR_SAFE)
        else:
            self._dot.configure(text_color=COLOR_DANGER)
            self._cam_status.configure(text="No face detected — adjust position",
                                       text_color=COLOR_DANGER)
        self.after(200, self._detect_tick)

    def _render_frame(self, frame: np.ndarray) -> None:
        try:
            disp = cv2.resize(frame, (PREVIEW_W, PREVIEW_H))
            cx, cy = PREVIEW_W // 2, PREVIEW_H // 2
            cv2.ellipse(disp, (cx, cy), (65, 85), 0, 0, 360, (255, 255, 255), 2)
            rgb = np.ascontiguousarray(cv2.cvtColor(disp, cv2.COLOR_BGR2RGB))
            photo = ImageTk.PhotoImage(image=Image.fromarray(rgb), master=self._canvas)
            self._canvas_img = photo
            if self._canvas_item is None:
                # Clear placeholder text on first real frame
                self._canvas.delete("placeholder")
                self._canvas_item = self._canvas.create_image(
                    0, 0, anchor=tk.NW, image=photo)
            else:
                self._canvas.itemconfig(self._canvas_item, image=photo)
        except Exception:
            pass

    # ------------------------------------------------------------------ capture

    def _capture_face(self) -> None:
        if self._captured_frame is not None:
            # Retake
            self._captured_frame = None
            self._cap_btn.configure(text="📷  Capture Face", fg_color=COLOR_ACCENT)
            self._dot.configure(text_color=COLOR_TEXT_MUTED)
            self._cam_status.configure(text="Position face and capture again",
                                       text_color=COLOR_TEXT_MUTED)
            self._err_lbl.configure(text="")
            return

        with self._cap_lock:
            has_cam = self._cap is not None
        if not has_cam:
            return

        frame = self._get_frame()
        if frame is None:
            self._cam_status.configure(text="Could not read frame — try again",
                                       text_color=COLOR_DANGER)
            return

        self._captured_frame = frame.copy()

        # Show frozen snapshot with green tint border
        snap = cv2.resize(frame, (PREVIEW_W, PREVIEW_H))
        rgb = np.ascontiguousarray(cv2.cvtColor(snap, cv2.COLOR_BGR2RGB))
        photo = ImageTk.PhotoImage(image=Image.fromarray(rgb), master=self._canvas)
        self._canvas_img = photo
        if self._canvas_item is None:
            self._canvas_item = self._canvas.create_image(0, 0, anchor=tk.NW, image=photo)
        else:
            self._canvas.itemconfig(self._canvas_item, image=photo)
        # Green border to indicate captured
        self._canvas.configure(highlightthickness=3, highlightbackground=COLOR_SAFE)

        self._dot.configure(text_color=COLOR_SAFE)
        self._cam_status.configure(text="✓ Photo captured — click Retake to redo",
                                   text_color=COLOR_SAFE)
        self._cap_btn.configure(text="🔄  Retake", fg_color=COLOR_WARNING,
                                hover_color="#B45309")

    # ------------------------------------------------------------------ submit

    def _set_err(self, msg: str, *, ok: bool = False) -> None:
        try:
            self._err_lbl.configure(
                text=msg,
                text_color=COLOR_SAFE if ok else COLOR_DANGER)
        except Exception:
            pass

    def _submit(self) -> None:
        name     = self._e_name.get().strip()
        username = self._e_username.get().strip()
        password = self._e_password.get()
        sid      = self._e_sid.get().strip()
        course   = self._e_course.get().strip()
        year     = self._e_year.get().strip()
        gender   = self._gender_var.get()

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
        with self._cap_lock:
            has_cam = self._cap is not None
        if has_cam and self._captured_frame is None:
            self._set_err("Please capture your face photo before registering.")
            return
        # If model is still loading (not failed), ask user to wait
        if self._captured_frame is not None and self._recognizer is None \
                and not self._model_load_failed:
            self._set_err("Face model is still loading — please wait a moment and try again.")
            return

        self._set_err("")
        self._cap_btn.configure(state="disabled")

        frame = self._captured_frame  # may be None if no camera

        def _do() -> None:
            blob: bytes = b""
            photo_bytes: bytes = b""

            if frame is not None:
                # Always store the captured frame as JPEG — photo must be saved
                # regardless of whether face encoding succeeds.
                ok, buf = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if ok:
                    photo_bytes = buf.tobytes()

                if self._recognizer is not None:
                    try:
                        emb, box = self._recognizer.encode_face_multi(
                            [frame] * 5, min_valid=1)
                        if emb is not None:
                            blob = pickle.dumps(emb)
                            # Use face-cropped photo when encoding succeeds
                            cropped = _crop_photo(frame, box)
                            if cropped:
                                photo_bytes = cropped
                        else:
                            self.after(0, lambda: self._set_err(
                                "Could not detect a face clearly — "
                                "photo saved but recognition may not work. "
                                "Admin can update the photo later."))
                    except Exception as exc:
                        self.after(0, lambda e=exc: self._set_err(
                            f"Face encoding error: {e} — photo still saved."))

            try:
                self.database.insert_student(
                    student_id=sid, name=name, course=course,
                    year_and_section=year, gender=gender,
                    encoding=blob, photo=photo_bytes,
                )
                self.database.insert_student_account(sid, username, password)
                if self._recognizer and blob:
                    self._recognizer.load_known_faces()
            except Exception as exc:
                self.after(0, lambda e=exc: self._set_err(f"Registration failed: {e}"))
                self.after(0, lambda: self._cap_btn.configure(state="normal"))
                return

            self.after(0, self._on_success)

        threading.Thread(target=_do, daemon=True).start()

    def _on_success(self) -> None:
        self._set_err("✓  Registered successfully! You can now log in.", ok=True)
        self._cap_btn.configure(state="disabled")
        self.after(2000, self._close)

    # ------------------------------------------------------------------ lifecycle

    def _safe_grab(self) -> None:
        try:
            if self.winfo_exists():
                self.grab_set()
        except Exception:
            pass

    def _close(self) -> None:
        self._alive = False
        with self._cap_lock:
            cap = self._cap
            self._cap = None
        if cap:
            try:
                cap.release()
            except Exception:
                pass
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


# ── helpers ────────────────────────────────────────────────────────────

def _crop_photo(frame: np.ndarray, box) -> bytes:
    if frame is None:
        return b""
    crop = frame
    if box is not None:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [max(0, int(v)) for v in box]
        x2, y2 = min(w, x2), min(h, y2)
        sub = frame[y1:y2, x1:x2]
        if sub.size > 0:
            crop = sub
    ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return buf.tobytes() if ok else b""
