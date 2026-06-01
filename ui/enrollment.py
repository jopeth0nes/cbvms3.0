"""Student enrollment panel for CBVMS."""

from __future__ import annotations

import pickle
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Callable

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

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
    body_font,
    heading_font,
)

if TYPE_CHECKING:
    from core.recognizer import Recognizer
    from database.db_manager import CBVMSDatabase

PREVIEW_WIDTH = 320
PREVIEW_HEIGHT = 240

# Guided multi-angle capture order (angle_key, on-screen instruction).
_ANGLES = [
    ("front", "Look straight at the camera"),
    ("left", "Slowly turn your head LEFT"),
    ("right", "Slowly turn your head RIGHT"),
]
_ENROLL_FINISH_TEXT = "✓ All Angles Done — Enroll Student"
_UPDATE_FINISH_TEXT = "✓ All Angles Done — Update Photo"


class EnrollmentPanel(ctk.CTkFrame):
    """Student list + selected-photo preview. Enrolling happens in a modal.

    All photo rendering uses ImageTk.PhotoImage (CTkImage does not display on
    this macOS/CustomTkinter build).
    """

    def __init__(
        self,
        master,
        database: CBVMSDatabase,
        recognizer: Recognizer,
        get_frame: Callable[[], np.ndarray | None],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database = database
        self.recognizer = recognizer
        self.get_frame = get_frame

        self._students: list[dict] = []
        self._selected_pk: int | None = None

        # Modal-scoped widgets (created when a modal opens)
        self._entries: dict[str, ctk.CTkEntry] = {}
        self._gender_var: ctk.StringVar | None = None
        self._enroll_status_label: ctk.CTkLabel | None = None
        self._enroll_close: Callable[[], None] | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_left_panel()
        self._build_preview_panel()
        self._reload_students()
        self.grid_remove()

    # ------------------------------------------------------------------
    # Left panel — student list
    # ------------------------------------------------------------------

    def _build_left_panel(self) -> None:
        left = ctk.CTkFrame(
            self,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PADDING // 2))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Enrolled Students", font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            header,
            text="+ Enroll New Student",
            height=32,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            command=self._open_enroll_modal,
        ).grid(row=0, column=1, sticky="e")

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        search = ctk.CTkEntry(
            left,
            placeholder_text="Search by name or student ID…",
            textvariable=self._search_var,
        )
        search.grid(row=1, column=0, sticky="ew", padx=PADDING, pady=(0, 8))

        tree_wrap = ctk.CTkFrame(left, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS)
        tree_wrap.grid(row=2, column=0, sticky="nsew", padx=PADDING, pady=(0, 8))
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        self._configure_tree_style()
        columns = ("name", "student_id", "course", "year_and_section", "gender", "enrolled_at")
        self._tree = ttk.Treeview(
            tree_wrap,
            columns=columns,
            show="headings",
            style="CBVMS.Treeview",
            selectmode="browse",
        )
        headings = {
            "name": "Name",
            "student_id": "Student ID",
            "course": "Course",
            "year_and_section": "Year & Section",
            "gender": "Gender",
            "enrolled_at": "Date Enrolled",
        }
        widths = {"name": 130, "student_id": 90, "course": 90,
                  "year_and_section": 100, "gender": 65, "enrolled_at": 100}
        for col in columns:
            self._tree.heading(col, text=headings[col])
            self._tree.column(col, width=widths[col], anchor="w")

        scroll_y = ttk.Scrollbar(tree_wrap, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll_y.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        self._tree.bind("<<TreeviewSelect>>", self._on_row_select)

        footer = ctk.CTkFrame(left, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=PADDING, pady=(0, PADDING))
        footer.grid_columnconfigure(0, weight=1)

        self._count_label = ctk.CTkLabel(
            footer, text="0 students enrolled", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._count_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

        btn_row = ctk.CTkFrame(footer, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew")
        btn_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="enroll_btns")

        ctk.CTkButton(
            btn_row, text="Reload", height=32, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, command=self._reload_students,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._update_btn = ctk.CTkButton(
            btn_row, text="Update Photo", height=32, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._update_selected_photo, state="disabled",
        )
        self._update_btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            btn_row, text="Delete Selected", height=32, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_DANGER, hover_color="#DC2626", command=self._delete_selected,
        ).grid(row=0, column=2, sticky="ew")

    # ------------------------------------------------------------------
    # Right panel — selected student photo preview
    # ------------------------------------------------------------------

    def _build_preview_panel(self) -> None:
        right = ctk.CTkFrame(
            self, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_BORDER,
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(PADDING // 2, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            right, text="Student Photo  ·  click to enlarge",
            font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, 8))

        photo_wrap = ctk.CTkFrame(right, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS)
        photo_wrap.grid(row=1, column=0, sticky="nsew", padx=PADDING, pady=(0, 8))
        photo_wrap.grid_rowconfigure(0, weight=1)
        photo_wrap.grid_columnconfigure(0, weight=1)

        # tk.Label (not CTkLabel): CTkLabel.configure(image=None) fails to clear a
        # raw ImageTk image, leaving a deleted student's photo on screen. tk.Label
        # clears reliably with image="".
        self._selected_photo_label = tk.Label(
            photo_wrap, text="Select a student to view photo",
            bg=COLOR_BG, fg=COLOR_TEXT_MUTED, cursor="hand2",
            font=("Helvetica", 13), bd=0,
        )
        self._selected_photo_label.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self._selected_photo_label.bind("<Button-1>", lambda _e: self._view_selected_photo())

        self._preview_caption = ctk.CTkLabel(
            right, text="", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._preview_caption.grid(row=2, column=0, sticky="w", padx=PADDING, pady=(0, 4))

        self._status_label = ctk.CTkLabel(
            right, text="", font=body_font(12), text_color=COLOR_TEXT_MUTED, wraplength=360,
        )
        self._status_label.grid(row=3, column=0, sticky="w", padx=PADDING, pady=(0, PADDING))

    def _configure_tree_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "CBVMS.Treeview",
            background=COLOR_BG,
            foreground=COLOR_TEXT,
            fieldbackground=COLOR_BG,
            bordercolor=COLOR_BORDER,
            rowheight=28,
        )
        style.configure(
            "CBVMS.Treeview.Heading",
            background=COLOR_SURFACE,
            foreground=COLOR_TEXT,
            relief="flat",
        )
        style.map(
            "CBVMS.Treeview",
            background=[("selected", COLOR_ACCENT)],
            foreground=[("selected", COLOR_TEXT)],
        )

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_status(self, message: str, *, success: bool = False, error: bool = False) -> None:
        color = COLOR_DANGER if error else COLOR_SAFE if success else COLOR_TEXT_MUTED
        self._status_label.configure(text=message, text_color=color)

    def _set_enroll_status(self, message: str, *, success: bool = False, error: bool = False) -> None:
        label = self._enroll_status_label
        if label is None or not label.winfo_exists():
            self._set_status(message, success=success, error=error)
            return
        color = COLOR_DANGER if error else COLOR_SAFE if success else COLOR_TEXT_MUTED
        label.configure(text=message, text_color=color)

    # ------------------------------------------------------------------
    # Data + list
    # ------------------------------------------------------------------

    def _reload_students(self) -> None:
        rows = self.database.get_all_students()
        self._students = [dict(row) for row in rows]
        self._apply_filter()
        self._count_label.configure(text=f"{len(self._students)} students enrolled")

    def _apply_filter(self) -> None:
        query = self._search_var.get().strip().lower()
        for item in self._tree.get_children():
            self._tree.delete(item)

        for student in self._students:
            name = (student.get("name") or "").lower()
            sid = (student.get("student_id") or "").lower()
            if query and query not in name and query not in sid:
                continue
            enrolled = student.get("enrolled_at") or ""
            if enrolled and "T" not in enrolled:
                enrolled = enrolled.replace(" ", " ")[:16]
            self._tree.insert(
                "", "end", iid=str(student["id"]),
                values=(
                    student.get("name", ""),
                    student.get("student_id", ""),
                    student.get("course", "") or "—",
                    student.get("year_and_section", "") or "—",
                    student.get("gender", "") or "—",
                    enrolled or "—",
                ),
            )

    def _on_row_select(self, _event: tk.Event | None = None) -> None:
        selection = self._tree.selection()
        if not selection:
            self._selected_pk = None
            self._update_btn.configure(state="disabled")
            self._clear_photo_label("Select a student to view photo")
            self._preview_caption.configure(text="")
            return

        self._selected_pk = int(selection[0])
        self._update_btn.configure(state="normal")
        student = self._resolve_selected_student()

        if student and student.get("photo"):
            self._show_photo_bytes(student["photo"], self._selected_photo_label)
        else:
            self._clear_photo_label("No photo on file")

        if student:
            self._preview_caption.configure(
                text=f"{student.get('name', '')}  ·  {student.get('student_id', '')}"
            )
        else:
            self._preview_caption.configure(text="")

    # ------------------------------------------------------------------
    # Multi-frame capture (async — camera cache refreshes between after() ticks)
    # ------------------------------------------------------------------

    def _collect_frames(self, on_done, *, count: int = 10, interval_ms: int = 100) -> None:
        """Collect `count` fresh camera frames `interval_ms` apart without blocking
        the UI loop, then call on_done(frames). A blocking sleep loop would freeze
        the event loop and return identical cached frames, so we chain after()."""
        frames: list = []

        def _grab(i: int = 0) -> None:
            if not self.winfo_exists() or i >= count:
                on_done(frames)
                return
            f = self.get_frame()
            if f is not None:
                frames.append(f.copy())
            self.after(interval_ms, _grab, i + 1)

        _grab(0)

    # ------------------------------------------------------------------
    # Image rendering (ImageTk — the render path that works here)
    # ------------------------------------------------------------------

    def _frame_to_photo(self, frame_bgr, max_w: int, max_h: int):
        try:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            pil.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image=pil, master=self)
        except Exception:
            return None

    def _photo_bytes_to_photo(self, photo_blob: bytes, max_w: int, max_h: int):
        try:
            arr = np.frombuffer(photo_blob, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return None
            return self._frame_to_photo(frame, max_w, max_h)
        except Exception:
            return None

    def _show_photo_bytes(self, photo_blob: bytes, label, max_w: int = 440, max_h: int = 440) -> None:
        photo = self._photo_bytes_to_photo(photo_blob, max_w, max_h)
        if photo is None:
            self._clear_photo_label("Could not load photo")
            return
        label.configure(image=photo, text="")
        label._cbvms_photo = photo  # prevent GC

    def _clear_photo_label(self, text: str) -> None:
        """Reliably clear the preview photo (tk.Label clears with image='')."""
        self._selected_photo_label.configure(image="", text=text)
        self._selected_photo_label._cbvms_photo = None

    def _resolve_selected_student(self) -> dict | None:
        if self._selected_pk is None:
            return None
        student = next((s for s in self._students if s["id"] == self._selected_pk), None)
        if student is None:
            row = self.database.get_student(self._selected_pk)
            student = dict(row) if row is not None else None
        return student

    @staticmethod
    def _safe_grab(modal) -> None:
        try:
            if modal.winfo_exists():
                modal.grab_set()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Dashboard hooks (camera preview now lives in modals — no-ops)
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        return

    def on_hide(self) -> None:
        return

    def update_preview(self, frame: np.ndarray | None) -> None:
        return

    # ------------------------------------------------------------------
    # Enroll modal
    # ------------------------------------------------------------------

    def _open_enroll_modal(self) -> None:
        if self.recognizer is None:
            self._set_status(
                "Face recognition not ready. Please wait for the model to load.", error=True,
            )
            return

        modal = ctk.CTkToplevel(self)
        modal.title("Enroll New Student")
        modal.configure(fg_color=COLOR_BG)
        modal.geometry("860x560")
        modal.resizable(False, False)
        modal.transient(self.winfo_toplevel())
        modal.after(120, modal.lift)
        modal.after(200, lambda: self._safe_grab(modal))

        modal.grid_columnconfigure((0, 1), weight=1)
        modal.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            modal, text="Enroll New Student", font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=PADDING, pady=(PADDING, 8))

        # LEFT — form
        form_card = ctk.CTkFrame(modal, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                                 border_width=1, border_color=COLOR_BORDER)
        form_card.grid(row=1, column=0, sticky="nsew", padx=(PADDING, 8), pady=(0, 8))
        form_card.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(form_card, fg_color="transparent")
        form.pack(fill="x", padx=PADDING, pady=PADDING)
        form.grid_columnconfigure(1, weight=1)

        fields = [
            ("Full Name", "name"),
            ("Student ID", "student_id"),
            ("Course", "course"),
            ("Year and Section", "year_and_section"),
        ]
        self._entries = {}
        for r, (label, key) in enumerate(fields):
            ctk.CTkLabel(form, text=label, font=body_font(12), text_color=COLOR_TEXT_MUTED).grid(
                row=r, column=0, sticky="w", pady=6, padx=(0, 12)
            )
            entry = ctk.CTkEntry(form)
            entry.grid(row=r, column=1, sticky="ew", pady=6)
            self._entries[key] = entry

        ctk.CTkLabel(form, text="Gender", font=body_font(12), text_color=COLOR_TEXT_MUTED).grid(
            row=4, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        self._gender_var = ctk.StringVar(value="Male")
        ctk.CTkSegmentedButton(
            form, values=["Male", "Female"], variable=self._gender_var,
        ).grid(row=4, column=1, sticky="ew", pady=6)

        self._enroll_status_label = ctk.CTkLabel(
            form_card, text="", font=body_font(12), text_color=COLOR_TEXT_MUTED, wraplength=360,
        )
        self._enroll_status_label.pack(anchor="w", padx=PADDING, pady=(0, PADDING))

        # RIGHT — guided multi-angle capture wizard
        cam_card = ctk.CTkFrame(modal, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                                border_width=1, border_color=COLOR_BORDER)
        cam_card.grid(row=1, column=1, sticky="nsew", padx=(8, PADDING), pady=(0, 8))

        state: dict = {
            "modal": modal, "step": 0, "angle_frames": {}, "capturing": False, "alive": True,
        }

        def _close() -> None:
            state["alive"] = False
            for job_key in ("job_tick", "job_detect"):
                if state.get(job_key) is not None:
                    try:
                        modal.after_cancel(state[job_key])
                    except Exception:
                        pass
                    state[job_key] = None
            try:
                modal.grab_release()
            except Exception:
                pass
            self._enroll_status_label = None
            self._enroll_close = None
            modal.destroy()

        state["close"] = _close
        self._enroll_close = _close

        self._build_capture_wizard(
            cam_card, state, on_finish=self._finish_enroll, finish_text=_ENROLL_FINISH_TEXT,
        )

        btns = ctk.CTkFrame(modal, fg_color="transparent")
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", padx=PADDING, pady=(0, PADDING))
        ctk.CTkButton(
            btns, text="Cancel", width=130, height=36, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_DANGER, command=_close,
        ).pack(side="right")

        modal.protocol("WM_DELETE_WINDOW", _close)

    def _clear_form(self) -> None:
        for entry in self._entries.values():
            try:
                entry.delete(0, "end")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Guided multi-angle capture wizard (shared by enroll + update)
    # ------------------------------------------------------------------

    def _build_capture_wizard(self, card, state: dict, *, on_finish, finish_text: str) -> None:
        """Build the 3-step front/left/right capture UI into `card`.

        `state` carries: modal, step, angle_frames, capturing, alive, widget refs.
        `on_finish(modal, state)` runs once all angles are captured/skipped.
        """
        # Warm up the recognizer models (for the live "face detected" indicator) without
        # blocking the UI thread — has_face() returns False until this finishes.
        if self.recognizer is not None:
            threading.Thread(target=self.recognizer._ensure_models, daemon=True).start()

        # Step indicator (3 circles + caption)
        ind = ctk.CTkFrame(card, fg_color="transparent")
        ind.pack(fill="x", padx=12, pady=(12, 2))
        crow = ctk.CTkFrame(ind, fg_color="transparent")
        crow.pack()
        circles = []
        for i in range(3):
            c = ctk.CTkLabel(crow, text=str(i + 1), width=30, height=30, corner_radius=15,
                             fg_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED, font=body_font(13))
            c.pack(side="left", padx=6)
            circles.append(c)
        step_caption = ctk.CTkLabel(ind, text="", font=body_font(12), text_color=COLOR_TEXT)
        step_caption.pack(pady=(6, 0))
        state["circles"] = circles
        state["step_caption"] = step_caption

        # Live preview canvas (pose-guide overlay drawn each tick)
        canvas = tk.Canvas(card, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT,
                           bg=COLOR_BG, highlightthickness=0, borderwidth=0)
        canvas.pack(padx=12, pady=(8, 6))
        state["canvas"] = canvas
        state["canvas_item"] = None
        state["img"] = None

        # Detection status (dot + text)
        srow = ctk.CTkFrame(card, fg_color="transparent")
        srow.pack(pady=(0, 6))
        dot = ctk.CTkLabel(srow, text="●", font=body_font(14), text_color=COLOR_TEXT_MUTED)
        dot.pack(side="left", padx=(0, 6))
        det_status = ctk.CTkLabel(srow, text="Loading model…", font=body_font(12),
                                  text_color=COLOR_TEXT_MUTED)
        det_status.pack(side="left")
        state["dot"] = dot
        state["det_status"] = det_status

        # Capture + skip
        cap_btn = ctk.CTkButton(
            card, text="Capture This Angle", height=40, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=lambda: self._wizard_capture(state, on_finish, finish_text),
        )
        cap_btn.pack(fill="x", padx=12, pady=(2, 4))
        state["cap_btn"] = cap_btn
        skip_btn = ctk.CTkButton(
            card, text="Skip this angle →", height=24, corner_radius=CORNER_RADIUS,
            fg_color="transparent", hover_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED,
            font=body_font(11), command=lambda: self._wizard_skip(state, on_finish, finish_text),
        )
        skip_btn.pack(padx=12, pady=(0, 6))
        state["skip_btn"] = skip_btn

        # Progress pills
        prow = ctk.CTkFrame(card, fg_color="transparent")
        prow.pack(pady=(0, 10))
        pills = {}
        for key, _instr in _ANGLES:
            p = ctk.CTkLabel(prow, text=f"{key.title()}: 0", font=body_font(11),
                             text_color=COLOR_TEXT_MUTED, fg_color=COLOR_BG,
                             corner_radius=999, padx=8, pady=2)
            p.pack(side="left", padx=4)
            pills[key] = p
        state["pills"] = pills

        self._wizard_refresh(state, finish_text)
        self._wizard_tick(state)
        self._wizard_detect(state)

    @staticmethod
    def _draw_pose_guide(disp, step: int):
        """Draw a face-oval guide (+ direction arrow) onto the preview frame in place."""
        h, w = disp.shape[:2]
        cx, cy = w // 2, h // 2
        if step == 1:        # turn LEFT → guide oval shifts right
            center = (cx + 30, cy)
        elif step == 2:      # turn RIGHT → guide oval shifts left
            center = (cx - 30, cy)
        else:
            center = (cx, cy)
        cv2.ellipse(disp, center, (60, 80), 0, 0, 360, (255, 255, 255), 2)
        # cv2 (Hershey) fonts are ASCII-only, so use "<-"/"->" for the arrows.
        if step == 1:
            cv2.putText(disp, "<-", (cx - 95, cy + 8), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (255, 255, 255), 2, cv2.LINE_AA)
        elif step == 2:
            cv2.putText(disp, "->", (cx + 60, cy + 8), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (255, 255, 255), 2, cv2.LINE_AA)
        return disp

    def _wizard_tick(self, state: dict) -> None:
        modal = state["modal"]
        if not modal.winfo_exists() or not state.get("alive", True):
            return
        frame = self.get_frame()
        if frame is not None:
            disp = cv2.resize(frame, (PREVIEW_WIDTH, PREVIEW_HEIGHT))
            self._draw_pose_guide(disp, state["step"])
            rgb = np.ascontiguousarray(cv2.cvtColor(disp, cv2.COLOR_BGR2RGB))
            photo = ImageTk.PhotoImage(image=Image.fromarray(rgb), master=state["canvas"])
            state["img"] = photo
            if state["canvas_item"] is None:
                state["canvas_item"] = state["canvas"].create_image(0, 0, anchor=tk.NW, image=photo)
            else:
                state["canvas"].itemconfig(state["canvas_item"], image=photo)
        state["job_tick"] = modal.after(50, lambda: self._wizard_tick(state))

    def _wizard_detect(self, state: dict) -> None:
        modal = state["modal"]
        if not modal.winfo_exists() or not state.get("alive", True):
            return
        if not state.get("capturing"):
            frame = self.get_frame()
            rec = self.recognizer
            if rec is None or not getattr(rec, "_models_loaded", False):
                state["dot"].configure(text_color=COLOR_TEXT_MUTED)
                state["det_status"].configure(text="Loading model…", text_color=COLOR_TEXT_MUTED)
            elif frame is not None and rec.has_face(frame):
                state["dot"].configure(text_color=COLOR_SAFE)
                state["det_status"].configure(text="Face detected ✓", text_color=COLOR_SAFE)
            else:
                state["dot"].configure(text_color=COLOR_DANGER)
                state["det_status"].configure(
                    text="No face detected — adjust your position", text_color=COLOR_DANGER)
        state["job_detect"] = modal.after(200, lambda: self._wizard_detect(state))

    def _update_pills(self, state: dict) -> None:
        for key, pill in state["pills"].items():
            n = len(state["angle_frames"].get(key, []))
            color = COLOR_SAFE if n >= 2 else COLOR_WARNING if n == 1 else COLOR_TEXT_MUTED
            pill.configure(text=f"{key.title()}: {n}", text_color=color)

    def _wizard_refresh(self, state: dict, finish_text: str) -> None:
        step = state["step"]
        for i, c in enumerate(state["circles"]):
            if i < step:
                c.configure(text="✓", fg_color=COLOR_SAFE, text_color=COLOR_TEXT)
            elif i == step:
                c.configure(text=str(i + 1), fg_color=COLOR_ACCENT, text_color=COLOR_TEXT)
            else:
                c.configure(text=str(i + 1), fg_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED)
        instr = _ANGLES[step][1] if step < len(_ANGLES) else ""
        state["step_caption"].configure(text=f"Step {min(step + 1, 3)} of 3 — {instr}")
        is_last = step >= len(_ANGLES) - 1
        state["cap_btn"].configure(text=finish_text if is_last else "Capture This Angle",
                                   state="normal")
        state["skip_btn"].configure(state="normal")
        self._update_pills(state)

    def _wizard_capture(self, state: dict, on_finish, finish_text: str) -> None:
        if state.get("capturing") or state["step"] >= len(_ANGLES):
            return
        angle_key = _ANGLES[state["step"]][0]
        state["capturing"] = True
        state["cap_btn"].configure(state="disabled")
        state["skip_btn"].configure(state="disabled")
        state["dot"].configure(text_color=COLOR_TEXT_MUTED)
        state["det_status"].configure(text="Capturing… hold still", text_color=COLOR_TEXT_MUTED)

        def _done(frames) -> None:
            if not state["modal"].winfo_exists():
                return
            good = [f for f in frames if f is not None]
            state["angle_frames"].setdefault(angle_key, []).extend(good)
            state["det_status"].configure(text=f"✓ {len(good)} frames captured", text_color=COLOR_SAFE)
            self._update_pills(state)
            state["modal"].after(800, lambda: self._wizard_next(state, on_finish, finish_text))

        self._collect_frames(_done, count=8, interval_ms=120)

    def _wizard_skip(self, state: dict, on_finish, finish_text: str) -> None:
        if state.get("capturing"):
            return
        is_last = state["step"] >= len(_ANGLES) - 1
        total = sum(len(v) for v in state["angle_frames"].values())
        if is_last and total == 0:
            state["dot"].configure(text_color=COLOR_DANGER)
            state["det_status"].configure(
                text="Capture at least one angle before finishing.", text_color=COLOR_DANGER)
            return
        self._wizard_next(state, on_finish, finish_text)

    def _wizard_next(self, state: dict, on_finish, finish_text: str) -> None:
        state["capturing"] = False
        state["step"] += 1
        if state["step"] >= len(_ANGLES):
            on_finish(state["modal"], state)
            return
        self._wizard_refresh(state, finish_text)

    @staticmethod
    def _first_frame(angle_frames: dict):
        for key, _instr in _ANGLES:
            for f in reversed(angle_frames.get(key, [])):
                if f is not None:
                    return f
        for frames in angle_frames.values():
            for f in reversed(frames):
                if f is not None:
                    return f
        return None

    @staticmethod
    def _crop_photo(frame, box) -> bytes:
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

    def _wizard_embed(self, angle_frames: dict):
        """Encode captured angles → (encoding_blob, best_box) or (None, None)."""
        non_empty = {k: v for k, v in angle_frames.items() if v}
        if len(non_empty) >= 2:
            embeddings, box = self.recognizer.encode_face_multi_angle(
                non_empty, min_valid_per_angle=2)
            if embeddings is None:
                return None, None
            return pickle.dumps(embeddings), box
        frames = next(iter(non_empty.values()))
        emb, box = self.recognizer.encode_face_multi(frames, min_valid=2)
        if emb is None:
            return None, None
        return pickle.dumps(emb), box

    def _finish_enroll(self, modal, state: dict) -> None:
        angle_frames = {k: v for k, v in state["angle_frames"].items() if v}
        if not angle_frames:
            self._set_enroll_status("Please capture at least one angle.", error=True)
            state["step"] = 0
            self._wizard_refresh(state, _ENROLL_FINISH_TEXT)
            return
        if not self._entries or self._gender_var is None:
            return

        name = self._entries["name"].get().strip()
        student_id = self._entries["student_id"].get().strip()
        course = self._entries["course"].get().strip()
        year_and_section = self._entries["year_and_section"].get().strip()
        gender = self._gender_var.get()

        def _rearm(msg: str) -> None:
            state["capturing"] = False
            self._set_enroll_status(msg, error=True)
            if modal.winfo_exists():
                state["step"] = 0
                self._wizard_refresh(state, _ENROLL_FINISH_TEXT)

        if not all([name, student_id, course, year_and_section]):
            _rearm("Please fill in all fields.")
            return
        if self.database.student_id_exists(student_id):
            _rearm(f"Student ID '{student_id}' is already enrolled.")
            return
        if self.recognizer is None:
            _rearm("Face recognition not ready.")
            return

        # Mark busy so the 200ms detect loop stops overwriting the status while encoding.
        state["capturing"] = True
        state["cap_btn"].configure(text="Enrolling…", state="disabled")
        state["skip_btn"].configure(state="disabled")
        self._set_enroll_status("Encoding faces and enrolling…")

        def _do_enroll() -> None:
            blob, box = self._wizard_embed(angle_frames)
            if blob is None:
                modal.after(0, lambda: _rearm(
                    "Could not get enough face detections. Try again in better lighting."))
                return
            photo = self._crop_photo(self._first_frame(angle_frames), box)
            try:
                self.database.insert_student(
                    student_id=student_id, name=name, course=course,
                    year_and_section=year_and_section, gender=gender,
                    encoding=blob, photo=photo,
                )
            except Exception as exc:
                modal.after(0, lambda e=exc: _rearm(f"Enrollment failed: {e}"))
                return
            modal.after(0, _on_success)

        def _on_success() -> None:
            self._clear_form()
            self._reload_students()
            self.recognizer.load_known_faces()
            self._set_status("Student enrolled successfully.", success=True)
            close = state.get("close")
            if close is not None:
                close()

        threading.Thread(target=_do_enroll, daemon=True).start()

    def _finish_update(self, modal, state: dict) -> None:
        angle_frames = {k: v for k, v in state["angle_frames"].items() if v}
        if not angle_frames:
            state["dot"].configure(text_color=COLOR_DANGER)
            state["det_status"].configure(text="Capture at least one angle.", text_color=COLOR_DANGER)
            state["step"] = 0
            self._wizard_refresh(state, _UPDATE_FINISH_TEXT)
            return
        pk = state["target_pk"]

        def _rearm(msg: str) -> None:
            state["capturing"] = False
            if not modal.winfo_exists():
                return
            state["dot"].configure(text_color=COLOR_DANGER)
            state["det_status"].configure(text=msg, text_color=COLOR_DANGER)
            state["step"] = 0
            self._wizard_refresh(state, _UPDATE_FINISH_TEXT)

        # Mark busy so the 200ms detect loop stops overwriting the status while encoding.
        state["capturing"] = True
        state["cap_btn"].configure(text="Updating…", state="disabled")
        state["skip_btn"].configure(state="disabled")
        state["det_status"].configure(text="Encoding faces…", text_color=COLOR_TEXT_MUTED)

        def _do_update() -> None:
            blob, box = self._wizard_embed(angle_frames)
            if blob is None:
                modal.after(0, lambda: _rearm(
                    "Could not get enough face detections. Better lighting helps."))
                return
            photo = self._crop_photo(self._first_frame(angle_frames), box)
            ok = self.database.update_student_encoding(pk, blob, photo)
            modal.after(0, lambda: _on_done(ok, photo))

        def _on_done(ok: bool, photo: bytes) -> None:
            if not ok:
                _rearm("Update failed.")
                return
            self.recognizer.load_known_faces()
            self._reload_students()
            try:
                self._show_photo_bytes(photo, self._selected_photo_label)
            except Exception:
                pass
            self._set_status("Photo updated successfully.", success=True)
            close = state.get("close")
            if close is not None:
                close()

        threading.Thread(target=_do_update, daemon=True).start()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _delete_selected(self) -> None:
        if self._selected_pk is None:
            self._set_status("Select a student to delete.", error=True)
            return

        student = next((s for s in self._students if s["id"] == self._selected_pk), None)
        name = student["name"] if student else "this student"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete {name}? This cannot be undone.",
            parent=self.winfo_toplevel(),
        ):
            return

        if self.database.delete_student(self._selected_pk):
            self._selected_pk = None
            self._update_btn.configure(state="disabled")
            self._clear_photo_label("Select a student to view photo")
            self._preview_caption.configure(text="")
            self._set_status("Student deleted.", success=True)
            self._reload_students()
            if self.recognizer is not None:
                self.recognizer.load_known_faces()
        else:
            self._set_status("Delete failed.", error=True)

    # ------------------------------------------------------------------
    # View photo (enlarge) modal
    # ------------------------------------------------------------------

    def _view_selected_photo(self) -> None:
        student = self._resolve_selected_student()
        if student is None:
            self._set_status("Select a student to view.", error=True)
            return

        modal = ctk.CTkToplevel(self)
        modal.title(f"Photo — {student.get('name', '')}")
        modal.configure(fg_color=COLOR_BG)
        modal.resizable(False, False)
        modal.transient(self.winfo_toplevel())
        modal.after(120, modal.lift)
        modal.after(200, lambda: self._safe_grab(modal))

        ctk.CTkLabel(
            modal, text=f"{student.get('name', '')}  ·  {student.get('student_id', '')}",
            font=heading_font(15), text_color=COLOR_TEXT,
        ).pack(padx=PADDING, pady=(PADDING, 8))

        photo = student.get("photo")
        img = self._photo_bytes_to_photo(photo, 560, 560) if photo else None
        if img is None:
            ctk.CTkLabel(
                modal, text="No photo on file", font=body_font(13),
                text_color=COLOR_TEXT_MUTED, width=400, height=300,
            ).pack(padx=PADDING, pady=PADDING)
        else:
            lbl = ctk.CTkLabel(modal, text="", image=img)
            lbl._cbvms_photo = img
            lbl.pack(padx=PADDING, pady=(0, PADDING))

        ctk.CTkButton(
            modal, text="Close", width=120, height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, command=modal.destroy,
        ).pack(pady=(0, PADDING))

    # ------------------------------------------------------------------
    # Update photo modal (explicit capture)
    # ------------------------------------------------------------------

    def _update_selected_photo(self) -> None:
        if self._selected_pk is None:
            self._set_status("Select a student to update.", error=True)
            return
        if self.recognizer is None:
            self._set_status(
                "Face recognition not ready. Please wait for the model to load.", error=True,
            )
            return
        student = self._resolve_selected_student()
        if student is None:
            self._set_status("Selected student not found.", error=True)
            return
        self._open_update_modal(student)

    def _open_update_modal(self, student: dict) -> None:
        target_pk = int(student["id"])

        modal = ctk.CTkToplevel(self)
        modal.title(f"Update Photo — {student.get('name', '')}")
        modal.configure(fg_color=COLOR_BG)
        modal.resizable(False, False)
        modal.transient(self.winfo_toplevel())
        modal.update_idletasks()
        sw, sh = modal.winfo_screenwidth(), modal.winfo_screenheight()
        W, H = 480, 640
        modal.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")
        modal.after(120, modal.lift)
        modal.after(200, lambda: self._safe_grab(modal))

        card = ctk.CTkFrame(modal, fg_color=COLOR_SURFACE, corner_radius=16,
                            border_width=1, border_color=COLOR_BORDER)
        card.pack(fill="both", expand=True, padx=PADDING, pady=PADDING)
        ctk.CTkLabel(
            card, text=student.get("name", ""), font=heading_font(16), text_color=COLOR_TEXT,
        ).pack(pady=(PADDING, 2))

        state: dict = {
            "modal": modal, "step": 0, "angle_frames": {}, "capturing": False,
            "alive": True, "target_pk": target_pk,
        }

        def _close() -> None:
            state["alive"] = False
            for job_key in ("job_tick", "job_detect"):
                if state.get(job_key) is not None:
                    try:
                        modal.after_cancel(state[job_key])
                    except Exception:
                        pass
                    state[job_key] = None
            try:
                modal.grab_release()
            except Exception:
                pass
            modal.destroy()

        state["close"] = _close

        self._build_capture_wizard(
            card, state, on_finish=self._finish_update, finish_text=_UPDATE_FINISH_TEXT,
        )

        ctk.CTkButton(
            card, text="Cancel", height=30, corner_radius=CORNER_RADIUS,
            fg_color="transparent", hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MUTED, command=_close,
        ).pack(pady=(0, PADDING))

        modal.protocol("WM_DELETE_WINDOW", _close)
