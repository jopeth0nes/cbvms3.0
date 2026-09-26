"""Student enrollment panel for CBVMS."""

from __future__ import annotations

from core.student_status import CONTACT_FIELDS, validate_contacts, standing_label, suspension_label
from ui.student_management import open_student_details, open_premises_log

import os
import queue
from collections import deque
import time
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Callable

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk

from core.face_capture import (CaptureSession, FacePreviewTracker, capture_payload, guide_geometry,
                               POSITION_MESSAGE, MAX_FRAME_AGE)
from core.registration_camera import PreviewMetrics

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
    CORNER_RADIUS_LG,
    PADDING,
    PADDING_LG,
    CBVMSCard,
    MirrorController,
    body_font,
    heading_font,
    make_mirror_button,
)

if TYPE_CHECKING:
    from core.recognizer import Recognizer
    from database.db_manager import CBVMSDatabase

PREVIEW_WIDTH = 320
PREVIEW_HEIGHT = 240
PREVIEW_INTERVAL_MS = 33
VALIDATION_INTERVAL = .25

# Guided multi-angle capture order (angle_key, on-screen instruction).
_ANGLES = [
    ("front", "Look straight at the camera"),
    ("left", "Slowly turn your head LEFT"),
    ("right", "Slowly turn your head RIGHT"),
]
_ENROLL_FINISH_TEXT = "✓ All Angles Done — Enroll Student"
_UPDATE_FINISH_TEXT = "✓ All Angles Done — Update Photo"

# Lightweight email sanity check (only applied when an email is provided — it is optional).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EnrollmentPanel(ctk.CTkFrame):
    """Student list with a selected-student summary. Enrolling swaps the panel's center to an
    in-panel flow (details form → guided face capture); Update Photo still uses a modal.

    All photo rendering uses ImageTk.PhotoImage (CTkImage does not display on
    this macOS/CustomTkinter build).
    """

    def __init__(
        self,
        master,
        database: CBVMSDatabase,
        recognizer: Recognizer,
        get_frame: Callable[[], np.ndarray | None],
        username: str = "admin",
        on_open_suspensions: Callable[[str], None] | None = None,
        get_frame_sample=None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.username = username
        self.on_open_suspensions = on_open_suspensions
        self.database = database
        self.recognizer = recognizer
        self.get_frame = get_frame
        self.get_frame_sample = get_frame_sample
        # Shared by all enrollment/update wizard generations, including closing ones.
        self._capture_inference_lock = threading.Lock()

        self._students: list[dict] = []
        self._selected_pk: int | None = None

        # Enroll-flow-scoped widgets (created when the in-panel enroll flow opens)
        self._entries: dict[str, ctk.CTkEntry] = {}
        self._gender_var: ctk.StringVar | None = None
        self._enroll_status_label: ctk.CTkLabel | None = None
        self._enroll_close: Callable[[], None] | None = None
        self._enroll_screen: ctk.CTkFrame | None = None
        self._update_close = None
        self._enroll_step_chips: list | None = None

        # The panel hosts one full-bleed screen at a time (list ⇄ enroll flow).
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # The summary and table share the full available width.
        self._list_screen = ctk.CTkFrame(self, fg_color=COLOR_BG)
        self._list_screen.grid(row=0, column=0, sticky="nsew")
        self._list_screen.grid_columnconfigure(0, weight=1)
        self._list_screen.grid_rowconfigure(0, weight=1)

        self._build_left_panel()
        self._reload_students()
        self.grid_remove()
        self.after(15000, self._refresh_standing_tags)

    def _refresh_standing_tags(self):
        if self.winfo_ismapped() and self._enroll_screen is None:
            self._reload_students()
        self.after(15000, self._refresh_standing_tags)

    # ------------------------------------------------------------------
    # Student summary and list
    # ------------------------------------------------------------------

    def _build_left_panel(self) -> None:
        left = ctk.CTkFrame(
            self._list_screen,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_rowconfigure(4, weight=1)
        left.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Student Management", font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            header,
            text="+ Enroll New Student",
            height=32,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            command=self._open_enroll_flow,
        ).grid(row=0, column=1, sticky="e")

        self._build_summary_card(left)

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        search = ctk.CTkEntry(
            left,
            placeholder_text="Search by name or student ID…",
            textvariable=self._search_var,
        )
        search.grid(row=2, column=0, sticky="ew", padx=PADDING, pady=(0, 8))
        filters = ctk.CTkFrame(left, fg_color="transparent")
        filters.grid(row=3, column=0, sticky="ew", padx=PADDING, pady=(0, 8))
        self._status_filter = ctk.CTkOptionMenu(filters,
            values=["All", "Enrolled", "Graduate", "Unenrolled", "Pending verification"],
            command=lambda _: self._apply_filter())
        self._status_filter.pack(side="left")
        ctk.CTkButton(filters, text="Premises Entry Log", width=145,
            command=lambda: open_premises_log(self, self.database)).pack(side="right")

        tree_wrap = ctk.CTkFrame(left, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS)
        tree_wrap.grid(row=4, column=0, sticky="nsew", padx=PADDING, pady=(0, 8))
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        self._configure_tree_style()
        columns = ("name", "student_id", "course", "year_and_section", "gender", "student_status", "suspension", "enrolled_at")
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
            "student_status": "Status",
            "suspension": "Suspension",
            "enrolled_at": "Date Enrolled",
        }
        widths = {"name": 130, "student_id": 90, "course": 90,
                  "year_and_section": 100, "gender": 65, "student_status": 110, "suspension": 210, "enrolled_at": 100}
        for col in columns:
            self._tree.heading(col, text=headings[col])
            self._tree.column(col, width=widths[col], anchor="w")

        scroll_y = ttk.Scrollbar(tree_wrap, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll_y.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self._tree.xview)
        scroll_x.grid(row=1, column=0, sticky="ew")
        self._tree.configure(xscrollcommand=scroll_x.set)
        self._tree.bind("<<TreeviewSelect>>", self._on_row_select)

        footer = ctk.CTkFrame(left, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=PADDING, pady=(0, PADDING))
        footer.grid_columnconfigure(0, weight=1)

        self._count_label = ctk.CTkLabel(
            footer, text="0 students enrolled", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._count_label.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            footer, text="Reload", width=100, height=32, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, command=self._reload_students,
        ).grid(row=0, column=1, sticky="e")
        self._status_label = ctk.CTkLabel(
            footer, text="", height=20, font=body_font(12),
            text_color=COLOR_TEXT_MUTED, anchor="w", justify="left", wraplength=500,
        )
        self._status_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    def _edit_selected_details(self):
        if not self._tree.selection():
            self._set_status("Select a student first.", error=True)
            return
        student = self._resolve_selected_student()
        if not student:
            self._set_status("Select a student first.", error=True)
            return
        open_student_details(self, self.database, student["student_id"], self.username, self._reload_students)

    def _open_selected_suspensions(self):
        student = self._resolve_selected_student()
        if student and self.on_open_suspensions:
            self.on_open_suspensions(student["student_id"])

    # ------------------------------------------------------------------
    # Compact selected-student summary
    # ------------------------------------------------------------------

    def _build_summary_card(self, parent) -> None:
        self._summary_card = ctk.CTkFrame(
            parent, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_BORDER,
        )
        self._summary_card.grid(row=1, column=0, sticky="ew", padx=PADDING, pady=(0, 12))
        self._summary_card.grid_columnconfigure(1, weight=1)

        portrait = ctk.CTkFrame(self._summary_card, fg_color="transparent")
        portrait.grid(row=0, column=0, padx=12, pady=12, sticky="n")
        photo_wrap = ctk.CTkFrame(portrait, width=88, height=88, fg_color=COLOR_SURFACE,
                                corner_radius=8)
        photo_wrap.pack()
        photo_wrap.pack_propagate(False)

        # tk.Label (not CTkLabel): CTkLabel.configure(image=None) fails to clear a
        # raw ImageTk image, leaving a deleted student's photo on screen. tk.Label
        # clears reliably with image="".
        self._selected_photo_label = tk.Label(
            photo_wrap, text="No selection",
            bg=COLOR_SURFACE, fg=COLOR_TEXT_MUTED, cursor="",
            font=("Helvetica", 11), bd=0, wraplength=76,
        )
        self._selected_photo_label.pack(fill="both", expand=True, padx=4, pady=4)
        self._selected_photo_label.bind("<Button-1>", lambda _e: self._view_selected_photo())
        self._photo_hint = ctk.CTkLabel(portrait, text="", height=16,
                                      font=body_font(10), text_color=COLOR_TEXT_MUTED)
        self._photo_hint.pack(pady=(4, 0))

        info = ctk.CTkFrame(self._summary_card, fg_color="transparent", width=1)
        info.grid(row=0, column=1, sticky="new", pady=12, padx=(0, 12))
        info.grid_columnconfigure(0, weight=1)
        self._summary_name = ctk.CTkLabel(
            info, text="Select a student", width=1, anchor="w", justify="left",
            font=heading_font(17), text_color=COLOR_TEXT,
        )
        self._summary_identity = ctk.CTkLabel(
            info, text="Choose a row below to view details and actions.", width=1,
            anchor="w", justify="left", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._summary_standing = ctk.CTkLabel(
            info, text="", width=1, anchor="w", justify="left",
            font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._summary_suspension = ctk.CTkLabel(
            info, text="", width=1, anchor="w", justify="left",
            font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        labels = (self._summary_name, self._summary_identity,
                  self._summary_standing, self._summary_suspension)
        for row, label in enumerate(labels):
            label.grid(row=row, column=0, sticky="ew")

        def resize_text(event):
            width = max(60, int(self._reverse_widget_scaling(event.width)) - 4)
            for label in labels:
                label.configure(wraplength=width)
        info.bind("<Configure>", resize_text)

        actions = ctk.CTkFrame(self._summary_card, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="ne", padx=(0, 12), pady=12)
        self._details_btn = ctk.CTkButton(
            actions, text="Edit Details", width=185, height=30,
            corner_radius=8, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._edit_selected_details, state="disabled",
        )
        self._details_btn.pack(fill="x")
        self._suspensions_btn = ctk.CTkButton(
            actions, text="View Suspensions", width=185, height=30, corner_radius=8,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER,
            command=self._open_selected_suspensions, state="disabled",
        )
        if self.on_open_suspensions:
            self._suspensions_btn.pack(fill="x", pady=(6, 0))
        self._update_btn = ctk.CTkButton(
            actions, text="Update Photo", width=185, height=30, corner_radius=8,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER,
            command=self._update_selected_photo, state="disabled",
        )
        self._update_btn.pack(fill="x", pady=6)
        self._delete_btn = ctk.CTkButton(
            actions, text="Delete Selected", width=185, height=30, corner_radius=8,
            fg_color=COLOR_BG, border_width=1, border_color=COLOR_DANGER,
            text_color=COLOR_DANGER, hover_color=COLOR_BORDER,
            command=self._delete_selected, state="disabled",
        )
        self._delete_btn.pack(fill="x")

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

    def _apply_filter(self) -> None:
        selection = self._tree.selection()
        query = self._search_var.get().strip().lower()
        for item in self._tree.get_children():
            self._tree.delete(item)

        shown = 0
        for student in self._students:
            status_filter = self._status_filter.get()
            if status_filter != "All" and standing_label(student) != status_filter:
                continue
            name = (student.get("name") or "").lower()
            sid = (student.get("student_id") or "").lower()
            if query and query not in name and query not in sid:
                continue
            shown += 1
            active_suspension = self.database.get_active_suspension(student["student_id"])
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
                    standing_label(student),
                    suspension_label(active_suspension),
                    enrolled or "—",
                ),
            )

        self._count_label.configure(text=f"{shown} of {len(self._students)} students")
        if selection and self._tree.exists(selection[0]):
            self._tree.selection_set(selection[0])
        self._on_row_select()

    def _on_row_select(self, _event: tk.Event | None = None) -> None:
        selection = self._tree.selection()
        previous_pk = self._selected_pk
        self._selected_pk = int(selection[0]) if selection else None
        if previous_pk != self._selected_pk and self._update_close is not None:
            self._update_close()
        student = self._resolve_selected_student()
        for button in (self._details_btn, self._update_btn, self._delete_btn):
            button.configure(state="normal" if student else "disabled")
        self._suspensions_btn.configure(state="normal" if student and self.on_open_suspensions else "disabled")

        if student is None:
            self._clear_photo_label("No selection")
            self._selected_photo_label.configure(cursor="")
            self._photo_hint.configure(text="")
            self._summary_name.configure(text="Select a student")
            self._summary_identity.configure(text="Choose a row below to view details and actions.")
            self._summary_standing.configure(text="")
            self._summary_suspension.configure(text="")
            return

        if student.get("photo"):
            self._show_photo_bytes(student["photo"], self._selected_photo_label)
        else:
            self._clear_photo_label("No photo on file")
        self._selected_photo_label.configure(cursor="hand2")
        self._photo_hint.configure(text="Click to enlarge" if student.get("photo") else "")
        self._summary_name.configure(text=student.get("name") or "Unnamed student")
        self._summary_identity.configure(text=" · ".join(
            str(student.get(key) or "—") for key in ("student_id", "course", "year_and_section")))
        standing = standing_label(student)
        self._summary_standing.configure(
            text=f"Status: {standing}",
            text_color=COLOR_SAFE if standing == "Enrolled" else COLOR_WARNING,
        )
        active = self.database.get_active_suspension(student["student_id"])
        self._summary_suspension.configure(
            text=suspension_label(active), text_color=COLOR_DANGER if active else COLOR_TEXT_MUTED)

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

    def _show_photo_bytes(self, photo_blob: bytes, label, max_w: int = 80, max_h: int = 80) -> None:
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
        # Always land on the student list when the panel is (re)opened.
        if self._enroll_screen is None:
            self._reload_students()
            self._list_screen.grid()

    def on_hide(self) -> None:
        # Navigating away mid-enroll: tear the flow down so its camera after()
        # loops stop and we return cleanly to the list for next time.
        if self._enroll_close is not None:
            self._enroll_close()
        if self._update_close is not None:
            self._update_close()

    def update_preview(self, frame: np.ndarray | None) -> None:
        return

    # ------------------------------------------------------------------
    # In-panel enroll flow (form → guided capture, no pop-up window)
    # ------------------------------------------------------------------

    def _open_enroll_flow(self) -> None:
        """Take over the panel's center: show the details form first, then (once it is
        filled with valid info) swap to the guided face-capture wizard — no modal window."""
        if self.recognizer is None:
            self._set_status(
                "Face recognition not ready. Please wait for the model to load.", error=True,
            )
            return
        if self._enroll_screen is not None:
            return  # already in a flow

        self._list_screen.grid_remove()

        screen = ctk.CTkFrame(self, fg_color=COLOR_BG)
        screen.grid(row=0, column=0, sticky="nsew")
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(2, weight=1)
        self._enroll_screen = screen

        header = ctk.CTkFrame(screen, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(0, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Enroll New Student", font=heading_font(18), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        self._build_enroll_stepper(header).grid(row=0, column=1, sticky="e")

        # Body host — the form and capture sub-steps swap in and out of this cell.
        body = ctk.CTkFrame(screen, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Shared status line — persists across both sub-steps.
        self._enroll_status_label = ctk.CTkLabel(
            screen, text="", font=body_font(12), text_color=COLOR_TEXT_MUTED, wraplength=560,
        )
        self._enroll_status_label.grid(row=3, column=0, sticky="w", padx=PADDING, pady=(4, 0))

        state: dict = {
            "modal": self,  # wizard schedules after()/checks winfo_exists() against the panel
            "step": 0, "angle_frames": {}, "capturing": False, "alive": True, "body": body,
        }

        def _return_to_list() -> None:
            if state.get("capturing"):
                return
            state["alive"] = False
            for job_key in ("job_tick", "job_detect"):
                if state.get(job_key) is not None:
                    try:
                        self.after_cancel(state[job_key])
                    except Exception:
                        pass
                    state[job_key] = None
            self._enroll_status_label = None
            self._enroll_close = None
            self._enroll_screen = None
            self._enroll_step_chips = None
            try:
                screen.destroy()
            except Exception:
                pass
            self._list_screen.grid()

        state["close"] = _return_to_list
        self._enroll_close = _return_to_list

        self._build_enroll_form(body, state)

    # ------------------------------------------------------------------
    # Enroll progress stepper (① Details → ② Face Capture)
    # ------------------------------------------------------------------

    def _build_enroll_stepper(self, parent) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        self._enroll_step_chips: list | None = []
        steps = [("1", "Details"), ("2", "Face Capture")]
        for i, (num, label) in enumerate(steps):
            chip = ctk.CTkFrame(bar, fg_color=COLOR_SURFACE, corner_radius=999,
                                border_width=1, border_color=COLOR_BORDER)
            chip.pack(side="left")
            dot = ctk.CTkLabel(chip, text=num, width=24, height=24, corner_radius=12,
                               fg_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED, font=body_font(12))
            dot.pack(side="left", padx=(6, 6), pady=4)
            txt = ctk.CTkLabel(chip, text=label, font=body_font(12), text_color=COLOR_TEXT_MUTED)
            txt.pack(side="left", padx=(0, 12), pady=4)
            self._enroll_step_chips.append((chip, dot, txt, num))
            if i < len(steps) - 1:
                ctk.CTkFrame(bar, fg_color=COLOR_BORDER, height=2, width=32).pack(side="left", padx=8)
        return bar

    def _set_enroll_step(self, idx: int) -> None:
        chips = getattr(self, "_enroll_step_chips", None)
        if not chips:
            return
        for i, (chip, dot, txt, num) in enumerate(chips):
            if i == idx:
                dot.configure(fg_color=COLOR_ACCENT, text_color=COLOR_TEXT, text=num)
                txt.configure(text_color=COLOR_TEXT)
                chip.configure(border_color=COLOR_ACCENT)
            elif i < idx:
                dot.configure(fg_color=COLOR_SAFE, text_color=COLOR_TEXT, text="✓")
                txt.configure(text_color=COLOR_TEXT_MUTED)
                chip.configure(border_color=COLOR_BORDER)
            else:
                dot.configure(fg_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED, text=num)
                txt.configure(text_color=COLOR_TEXT_MUTED)
                chip.configure(border_color=COLOR_BORDER)

    def _build_enroll_form(self, body, state: dict) -> None:
        """Sub-step 1 — details card (left) + guidance/tips panel (right), filling the width."""
        self._set_enroll_step(0)

        form_frame = ctk.CTkFrame(body, fg_color="transparent")
        form_frame.grid(row=0, column=0, sticky="nsew")
        form_frame.grid_columnconfigure(0, weight=3, uniform="enrollcols")
        form_frame.grid_columnconfigure(1, weight=2, uniform="enrollcols")
        form_frame.grid_rowconfigure(0, weight=1)
        state["form_frame"] = form_frame

        # LEFT — student details card
        card = CBVMSCard(form_frame)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, PADDING))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="Student Information", font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=PADDING_LG, pady=(PADDING_LG, 0))
        ctk.CTkLabel(
            card, text="Fill in the details, then continue to face capture.",
            font=body_font(12), text_color=COLOR_TEXT_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=PADDING_LG, pady=(2, 14))

        card.grid_rowconfigure(2, weight=1)
        form = ctk.CTkScrollableFrame(card, fg_color="transparent", height=310)
        form.grid(row=2, column=0, sticky="nsew", padx=PADDING_LG, pady=(0, 4))
        form.grid_columnconfigure(1, weight=1)

        fields = [
            ("Full Name", "name"),
            ("Student ID", "student_id"),
            ("Course", "course"),
            ("Year and Section", "year_and_section"),
            *CONTACT_FIELDS,
        ]
        self._entries = {}
        for r, (label, key) in enumerate(fields):
            ctk.CTkLabel(form, text=label, font=body_font(13), text_color=COLOR_TEXT_MUTED).grid(
                row=r, column=0, sticky="w", pady=9, padx=(0, 16)
            )
            entry = ctk.CTkEntry(form, height=38)
            entry.grid(row=r, column=1, sticky="ew", pady=9)
            entry.bind("<Return>", lambda _e: self._enroll_continue(state))
            self._entries[key] = entry

        ctk.CTkLabel(form, text="Gender", font=body_font(13), text_color=COLOR_TEXT_MUTED).grid(
            row=len(fields), column=0, sticky="w", pady=9, padx=(0, 16)
        )
        self._gender_var = ctk.StringVar(value="Male")
        ctk.CTkSegmentedButton(
            form, values=["Male", "Female"], variable=self._gender_var, height=36,
        ).grid(row=len(fields), column=1, sticky="ew", pady=9)

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=PADDING_LG, pady=(14, PADDING_LG))
        ctk.CTkButton(
            btns, text="Cancel", width=130, height=40, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_DANGER, command=state["close"],
        ).pack(side="left")
        ctk.CTkButton(
            btns, text="Continue →", width=170, height=40, corner_radius=CORNER_RADIUS,
            font=heading_font(13), fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=lambda: self._enroll_continue(state),
        ).pack(side="right")

        # RIGHT — guidance / what happens next
        self._build_enroll_guidance(form_frame)

        self._entries["name"].focus_set()

    def _build_enroll_guidance(self, parent) -> None:
        """Static side panel that fills the right column and previews the capture step."""
        guide = CBVMSCard(parent)
        guide.grid(row=0, column=1, sticky="nsew")
        guide.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(guide, text="👤", font=heading_font(48), text_color=COLOR_ACCENT).grid(
            row=0, column=0, pady=(PADDING_LG, 2)
        )
        ctk.CTkLabel(
            guide, text="Next: Face Capture", font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=1, column=0, padx=PADDING_LG)
        ctk.CTkLabel(
            guide,
            text="After you continue, we'll capture 3 quick angles of the student's face "
                 "(front, left, right) to build an accurate recognition profile.",
            font=body_font(12), text_color=COLOR_TEXT_MUTED, wraplength=240, justify="left",
        ).grid(row=2, column=0, padx=PADDING_LG, pady=(8, 16), sticky="w")

        tips = [
            ("💡", "Use even, front-facing lighting"),
            ("🙂", "Look straight at the camera"),
            ("🧢", "Remove hats, masks, or sunglasses"),
        ]
        tip_wrap = ctk.CTkFrame(guide, fg_color="transparent")
        tip_wrap.grid(row=3, column=0, padx=PADDING_LG, pady=(0, PADDING_LG), sticky="w")
        for icon, text in tips:
            trow = ctk.CTkFrame(tip_wrap, fg_color="transparent")
            trow.pack(anchor="w", pady=5)
            ctk.CTkLabel(trow, text=icon, font=body_font(15)).pack(side="left", padx=(0, 10))
            ctk.CTkLabel(
                trow, text=text, font=body_font(12), text_color=COLOR_TEXT_MUTED,
            ).pack(side="left")

    def _enroll_continue(self, state: dict) -> None:
        """Validate the form; on success, advance to the capture sub-step."""
        if not self._entries or self._gender_var is None:
            return
        name = self._entries["name"].get().strip()
        student_id = self._entries["student_id"].get().strip()
        course = self._entries["course"].get().strip()
        year_and_section = self._entries["year_and_section"].get().strip()
        email = self._entries["email"].get().strip()

        if not all([name, student_id, course, year_and_section]):
            self._set_enroll_status("Name, student ID, course, and year/section are required. Contacts are optional.", error=True)
            return
        if email and not _EMAIL_RE.match(email):
            self._set_enroll_status(
                "Please enter a valid email address (or leave it blank).", error=True)
            return
        if self.database.student_id_exists(student_id):
            self._set_enroll_status(f"Student ID '{student_id}' is already enrolled.", error=True)
            return

        try:
            validate_contacts({key: self._entries[key].get() for _, key in CONTACT_FIELDS})
        except ValueError as exc:
            self._set_enroll_status(str(exc), error=True)
            return
        self._set_enroll_status("")
        self._build_enroll_capture(state)

    def _build_enroll_capture(self, state: dict) -> None:
        """Sub-step 2 — guided multi-angle capture. The form is hidden (not destroyed) so
        _finish_enroll can still read self._entries / self._gender_var, and Back can restore it."""
        form_frame = state.get("form_frame")
        if form_frame is not None:
            form_frame.grid_remove()
        self._set_enroll_step(1)

        cap_frame = ctk.CTkFrame(state["body"], fg_color="transparent")
        cap_frame.grid(row=0, column=0, sticky="nsew")
        cap_frame.grid_columnconfigure(0, weight=1)
        cap_frame.grid_rowconfigure(1, weight=1)
        state["capture_frame"] = cap_frame

        ctk.CTkButton(
            cap_frame, text="← Back to details", width=150, height=28,
            corner_radius=CORNER_RADIUS, fg_color="transparent", hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MUTED, font=body_font(11),
            command=lambda: self._enroll_back_to_form(state),
        ).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 4))

        cam_card = CBVMSCard(cap_frame)
        cam_card.grid(row=1, column=0)  # centered

        # Fresh capture pass each time we enter this sub-step. The enroll capture uses a
        # large preview (big=True) to fill the panel; the Update-Photo modal keeps defaults.
        state["alive"] = True
        state["step"] = 0
        state["angle_frames"] = {}
        self._build_capture_wizard(
            cam_card, state, on_finish=self._finish_enroll, finish_text=_ENROLL_FINISH_TEXT,
            preview_size=(520, 390), big=True,
        )

    def _enroll_back_to_form(self, state: dict) -> None:
        """Return to the details form (values preserved), stopping the live camera loops."""
        if state.get("capturing"):
            return
        state["alive"] = False
        for job_key in ("job_tick", "job_detect"):
            if state.get(job_key) is not None:
                try:
                    self.after_cancel(state[job_key])
                except Exception:
                    pass
                state[job_key] = None
        cap_frame = state.get("capture_frame")
        if cap_frame is not None:
            try:
                cap_frame.destroy()
            except Exception:
                pass
            state["capture_frame"] = None
        self._set_enroll_step(0)
        form_frame = state.get("form_frame")
        if form_frame is not None:
            form_frame.grid()
        self._set_enroll_status("")

    def _clear_form(self) -> None:
        for entry in self._entries.values():
            try:
                entry.delete(0, "end")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Guided multi-angle capture wizard (shared by enroll + update)
    # ------------------------------------------------------------------

    def _build_capture_wizard(
        self, card, state: dict, *, on_finish, finish_text: str,
        preview_size: tuple[int, int] = (PREVIEW_WIDTH, PREVIEW_HEIGHT), big: bool = False,
    ) -> None:
        """Build the 3-step front/left/right capture UI into `card`.

        `state` carries: modal, step, angle_frames, capturing, alive, widget refs.
        `on_finish(modal, state)` runs once all angles are captured/skipped.
        `preview_size`/`big` scale the preview + controls (enroll uses a big preview; the
        Update-Photo modal keeps the compact default).
        """
        pw, ph = preview_size
        state["preview_w"], state["preview_h"] = pw, ph
        state["mirror"] = MirrorController()
        state["student_key"] = self._capture_student_key(state)
        state["session"] = CaptureSession(state["student_key"])
        state["results"] = queue.Queue()
        state["worker_busy"] = False
        state["reviewing"] = False
        state["capturing"] = False
        state["on_finish"] = on_finish
        state["finish_text"] = finish_text
        self._init_wizard_preview(state)
        pad = 16 if big else 12
        csz = 38 if big else 30

        # Warm up inference without blocking the UI thread.
        if self.recognizer is not None:
            threading.Thread(target=self.recognizer._ensure_models, daemon=True).start()

        # Step indicator (3 circles + caption)
        ind = ctk.CTkFrame(card, fg_color="transparent")
        ind.pack(fill="x", padx=pad, pady=(pad, 2))
        crow = ctk.CTkFrame(ind, fg_color="transparent")
        crow.pack()
        circles = []
        for i in range(3):
            c = ctk.CTkLabel(crow, text=str(i + 1), width=csz, height=csz, corner_radius=csz // 2,
                             fg_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED,
                             font=body_font(15 if big else 13))
            c.pack(side="left", padx=8 if big else 6)
            circles.append(c)
        step_caption = ctk.CTkLabel(ind, text="", font=body_font(14 if big else 12),
                                    text_color=COLOR_TEXT)
        step_caption.pack(pady=(8 if big else 6, 0))
        if "target_pk" in state:
            identity = state["target_student_id"]
        else:
            identity = f"{self._entries['name'].get().strip()} · {self._entries['student_id'].get().strip()}"
        state["identity"] = identity
        state["circles"] = circles
        state["step_caption"] = step_caption

        # Live preview canvas (pose-guide overlay drawn each tick) with an overlaid mirror toggle.
        cam_wrap = ctk.CTkFrame(card, fg_color="transparent")
        cam_wrap.pack(padx=pad, pady=(8, 6))
        canvas = tk.Canvas(cam_wrap, width=pw, height=ph,
                           bg=COLOR_BG, highlightthickness=0, borderwidth=0)
        canvas.pack()
        mirror_btn = make_mirror_button(cam_wrap, state["mirror"])
        mirror_btn.place(in_=canvas, relx=1.0, x=-8, y=8, anchor="ne")
        mirror_btn.lift()
        state["canvas"] = canvas
        state["canvas_item"] = None
        state["img"] = None

        # Detection status (dot + text)
        srow = ctk.CTkFrame(card, fg_color="transparent")
        srow.pack(pady=(0, 6))
        dot = ctk.CTkLabel(srow, text="●", font=body_font(16 if big else 14),
                           text_color=COLOR_TEXT_MUTED)
        dot.pack(side="left", padx=(0, 6))
        det_status = ctk.CTkLabel(srow, text="Loading model…", font=body_font(13 if big else 12),
                                  text_color=COLOR_TEXT_MUTED)
        det_status.pack(side="left")
        state["dot"] = dot
        state["det_status"] = det_status

        # Capture + skip
        cap_btn = ctk.CTkButton(
            card, text="Capture This Angle", height=46 if big else 40, corner_radius=CORNER_RADIUS,
            font=heading_font(14) if big else None,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=lambda: self._wizard_capture(state, on_finish, finish_text),
        )
        cap_btn.pack(fill="x", padx=pad, pady=(2, 4))
        state["cap_btn"] = cap_btn
        skip_btn = ctk.CTkButton(
            card, text="Skip this angle →", height=26 if big else 24, corner_radius=CORNER_RADIUS,
            fg_color="transparent", hover_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED,
            font=body_font(12 if big else 11),
            command=lambda: self._wizard_skip(state, on_finish, finish_text),
        )
        skip_btn.pack(padx=pad, pady=(0, 6))
        state["skip_btn"] = skip_btn

        # Progress pills
        prow = ctk.CTkFrame(card, fg_color="transparent")
        prow.pack(pady=(0, pad))
        pills = {}
        for key, _instr in _ANGLES:
            p = ctk.CTkLabel(prow, text=f"{key.title()}: 0", font=body_font(12 if big else 11),
                             text_color=COLOR_TEXT_MUTED, fg_color=COLOR_BG,
                             corner_radius=999, padx=10 if big else 8, pady=3 if big else 2)
            p.pack(side="left", padx=5 if big else 4)
            pills[key] = p
        state["pills"] = pills

        self._wizard_refresh(state, finish_text)
        self._wizard_tick(state)

    def _capture_student_key(self, state):
        if "target_pk" in state:
            return ("update", self._selected_pk, state["target_student_id"])
        return ("enroll", tuple((key, entry.get().strip())
                               for key, entry in self._entries.items()), self._gender_var.get())

    def _capture_current(self, state):
        return (state.get("alive", False) and state["modal"].winfo_exists()
                and state["student_key"] == self._capture_student_key(state))

    @staticmethod
    def _draw_pose_guide(disp, step: int, mirror: bool = False):
        cx, cy, rx, ry = guide_geometry(disp.shape, step)
        if mirror:
            cx = disp.shape[1] - cx
        cv2.ellipse(disp, (round(cx), round(cy)), (round(rx), round(ry)),
                    0, 0, 360, (255, 255, 255), 2)
        return disp

    def _render_capture(self, state, frame, box=None, *, frozen=False):
        pw, ph = state["preview_w"], state["preview_h"]
        if frozen:
            # The saved JPEG stays unmirrored; only its display copy is fitted.
            h, w = frame.shape[:2]
            scale = min(pw / w, ph / h)
            fitted = cv2.resize(frame, (max(1, round(w*scale)), max(1, round(h*scale))))
            disp = np.zeros((ph, pw, 3), dtype=np.uint8)
            fh, fw = fitted.shape[:2]
            x, y = (pw-fw)//2, (ph-fh)//2
            disp[y:y+fh, x:x+fw] = fitted
        else:
            # Resize once, then draw at preview resolution. Detection/capture retain
            # the untouched full-resolution sample and raw-coordinate face box.
            disp = cv2.resize(frame, (pw, ph))
            self._draw_pose_guide(disp, state["step"])
            if box is not None:
                h, w = frame.shape[:2]
                x1, y1, x2, y2 = (np.asarray(box)*np.array([pw/w, ph/h]*2)).astype(int)
                cv2.rectangle(disp, (x1, y1), (x2, y2), (90, 220, 40), 2)
            if state["mirror"].display_mirror():
                disp = cv2.flip(disp, 1)
        rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(image=Image.fromarray(rgb), master=state["canvas"])
        state["img"] = photo
        if state["canvas_item"] is None:
            state["canvas_item"] = state["canvas"].create_image(0, 0, anchor=tk.NW, image=photo)
        else:
            state["canvas"].itemconfig(state["canvas_item"], image=photo)

    def _show_frozen(self, state, capture):
        # Decode the exact JPEG bytes that persistence receives, unmirrored.
        crop = cv2.imdecode(np.frombuffer(capture.photo, np.uint8), cv2.IMREAD_COLOR)
        self._render_capture(state, crop, frozen=True)

    @staticmethod
    def _init_wizard_preview(state):
        state.update(preview_tracker=FacePreviewTracker(), preview_session=None,
                     last_rendered=None, next_validation=0.0, preview_feedback=None,
                     preview_metrics=PreviewMetrics(), source_times=deque(maxlen=300),
                     last_source_sample=None, next_diagnostics=time.monotonic()+5)

    def _start_wizard_validation(self, state, session, sample):
        # The panel-level lock also covers workers from a closed/backed-out wizard.
        # Never queue a second worker while an obsolete native inference finishes.
        if not self._capture_inference_lock.acquire(blocking=False):
            return
        state["worker_busy"] = True
        state["validation_started_at"] = time.monotonic()
        state["next_validation"] = state["validation_started_at"] + VALIDATION_INTERVAL
        results = state["results"]
        recognizer = self.recognizer

        def detect():
            faces, error = [], None
            try:
                if state.get("alive") and state["session"] is session:
                    faces = recognizer.enrollment_faces(sample.frame)
            except Exception as exc:
                error = str(exc)
            finally:
                results.put((session, sample, faces, error))
                self._capture_inference_lock.release()

        try:
            threading.Thread(target=detect, daemon=True, name="enrollment-validation").start()
        except Exception:
            state["worker_busy"] = False
            self._capture_inference_lock.release()
            raise

    @staticmethod
    def _wizard_feedback(state, session):
        frozen = session.frozen is not None
        message = ("Frozen face preview — confirm or retake" if frozen else
                   "Capturing — hold still" if session.pending else session.message)
        ready = frozen or session.ready and not session.pending
        color = COLOR_SAFE if ready else COLOR_DANGER
        value = (message, color, "Use This Capture" if frozen else "Capture This Angle",
                 "normal" if ready else "disabled", "Retake" if frozen else "Skip this angle →",
                 "disabled" if session.pending else "normal")
        previous = state.get("preview_feedback")
        if previous is None or previous[:2] != value[:2]:
            state["det_status"].configure(text=message, text_color=color)
            state["dot"].configure(text_color=color)
        if previous is None or previous[2:4] != value[2:4]:
            state["cap_btn"].configure(text=value[2], state=value[3])
        if previous is None or previous[4:] != value[4:]:
            state["skip_btn"].configure(text=value[4], state=value[5])
        state["preview_feedback"] = value

    def _wizard_tick(self, state: dict) -> None:
        if not state.get("alive") or not state["modal"].winfo_exists():
            return
        started = time.monotonic()
        session = state["session"]
        tracker = state["preview_tracker"]
        if not self._capture_current(state):
            session.invalidate("Student changed. Close this capture and select the student again.")
            state["angle_frames"] = {}
            tracker.clear()
            state["canvas"].delete("all")
            state["canvas_item"] = None
            state["img"] = None
            state["cap_btn"].configure(state="disabled")
            state["skip_btn"].configure(state="disabled")
            state["det_status"].configure(text=session.message, text_color=COLOR_DANGER)
            return
        if state["preview_session"] is not session:
            tracker.clear()
            state["preview_session"] = session
            state["last_rendered"] = None
            state["next_validation"] = 0.0
            state["preview_feedback"] = None
            # A deliberate frozen-review pause is not a slow live-preview frame.
            state["preview_metrics"] = PreviewMetrics()
            state["source_times"].clear()
            state["last_source_sample"] = None
            state["next_diagnostics"] = started + 5
        if not state["reviewing"] and session.frozen is None:
            sample = self.get_frame_sample() if self.get_frame_sample else None
            now = time.monotonic()
            fresh = sample is not None and 0 <= now-sample.captured_at <= MAX_FRAME_AGE
            if not fresh:
                session.invalidate()
                tracker.clear()
                if state["last_rendered"] is not None:
                    state["canvas"].delete("all")
                    state["canvas_item"] = None
                    state["img"] = None
                    state["last_rendered"] = None
            elif session.last_sample and sample.frame_id[0] != session.last_sample.frame_id[0]:
                session.invalidate("Camera changed. Hold still and capture again.")
                tracker.clear()
            if fresh and sample.frame_id != state["last_source_sample"]:
                state["source_times"].append(sample.captured_at)
                state["last_source_sample"] = sample.frame_id
            # A delayed post-click result must not win a race with newer pixels
            # showing that the target left. Cancel before observe() can freeze it.
            if session.pending and fresh and tracker.advance(sample, state["step"]) is None:
                session.invalidate("Tracking uncertain. Hold still inside the guide.")
            try:
                owner, detected_sample, faces, error = state["results"].get_nowait()
                state["worker_busy"] = False
                state["preview_metrics"].inference_seconds.append(
                    max(0, now-state.get("validation_started_at", now)))
                if owner is session and fresh and sample.frame_id[0] == detected_sample.frame_id[0]:
                    # Never freeze an in-flight frame acquired before the user's click.
                    if not session.pending or detected_sample.captured_at > state.get("requested_at", 0):
                        face = session.observe(detected_sample, faces, state["step"], now)
                        if error:
                            session.invalidate("Face detection unavailable. Please try again.")
                            face = None
                        if face is None:
                            tracker.clear()
                        else:
                            tracker.reset(detected_sample, face[0])
                        if session.frozen is not None:
                            captures = self._ordered_captures(state)
                            if captures and float(captures[0].embedding @ session.frozen.embedding) < .55:
                                session.frozen = None
                                session.invalidate("Target changed. Retake with the same person.")
                                tracker.clear()
                            else:
                                self._show_frozen(state, session.frozen)
            except queue.Empty:
                pass
            if session.frozen is None:
                # Only optical flow runs between validations; its box is display-only.
                # Full validation of a fresh frame still determines every saved pixel.
                box = tracker.advance(sample, state["step"]) if fresh else None
                if session.previous is not None and box is None:
                    session.invalidate("Tracking uncertain. Hold still inside the guide.")
                if session.last_sample and now-session.last_sample.captured_at > MAX_FRAME_AGE:
                    session.invalidate()
                    tracker.clear()
                    box = None
                orientation = state["mirror"].display_mirror()
                render_key = (sample.frame_id, orientation) if fresh else None
                if fresh and render_key != state["last_rendered"]:
                    self._render_capture(state, sample.frame, box)
                    state["last_rendered"] = render_key
                    state["preview_metrics"].rendered(sample, time.monotonic())
                if (fresh and not state["worker_busy"] and now >= state["next_validation"] and
                        (session.last_sample is None or sample.frame_id != session.last_sample.frame_id)):
                    self._start_wizard_validation(state, session, sample)
            self._wizard_feedback(state, session)
        if os.environ.get("CBVMS_CAMERA_DIAGNOSTICS") == "1" and started >= state["next_diagnostics"]:
            metrics = state["preview_metrics"].summary(state["source_times"])
            # These source samples are those observed by this consumer, not all reads.
            metrics["observed_source_fps"] = metrics.pop("source_fps")
            metrics["phase"] = "review" if state["reviewing"] or session.frozen else "live"
            print(f"[Enrollment preview] {metrics}")
            state["next_diagnostics"] = started + 5
        elapsed_ms = round((time.monotonic()-started)*1000)
        state["job_tick"] = state["modal"].after(
            max(1, PREVIEW_INTERVAL_MS-elapsed_ms), lambda: self._wizard_tick(state))

    @staticmethod
    def _ordered_captures(state):
        return [c for key, _ in _ANGLES for c in state["angle_frames"].get(key, [])]

    def _update_pills(self, state: dict) -> None:
        for key, pill in state["pills"].items():
            n = len(state["angle_frames"].get(key, []))
            pill.configure(text=f"{key.title()}: {'✓' if n else '0'}",
                           text_color=COLOR_SAFE if n else COLOR_TEXT_MUTED)

    def _wizard_refresh(self, state: dict, finish_text: str) -> None:
        state["preview_feedback"] = None
        step = state["step"]
        for i, circle in enumerate(state["circles"]):
            circle.configure(text="✓" if i < step else str(i+1),
                             fg_color=COLOR_SAFE if i < step else COLOR_ACCENT if i == step else COLOR_BORDER)
        state["step_caption"].configure(text=f"Step {step+1} of 3 — {_ANGLES[step][1]}")
        state["cap_btn"].configure(text="Capture This Angle", state="disabled")
        state["skip_btn"].configure(text="Skip this angle →", state="normal")
        self._update_pills(state)

    def _wizard_capture(self, state: dict, on_finish, finish_text: str) -> None:
        if not self._capture_current(state) or state.get("capturing"):
            return
        if state["reviewing"]:
            on_finish(state["modal"], state)
            return
        session = state["session"]
        if session.frozen is not None:
            state["angle_frames"][_ANGLES[state["step"]][0]] = [session.frozen]
            self._wizard_next(state, on_finish, finish_text)
        elif session.request(time.monotonic()):
            state["requested_at"] = time.monotonic()
            state["next_validation"] = 0.0
            state["preview_feedback"] = None
            state["cap_btn"].configure(state="disabled")
            state["skip_btn"].configure(state="disabled")

    def _wizard_retake(self, state):
        if state.get("capturing") or not self._capture_current(state):
            return
        if state["reviewing"]:
            state["angle_frames"] = {}
            state["step"] = 0
        state["reviewing"] = False
        state["session"] = CaptureSession(state["student_key"])
        state["canvas"].delete("all")
        state["canvas_item"] = None
        state["img"] = None
        self._wizard_refresh(state, state["finish_text"])

    def _wizard_skip(self, state: dict, on_finish, finish_text: str) -> None:
        if not self._capture_current(state) or state.get("capturing") or state["session"].pending:
            return
        if state["reviewing"] or state["session"].frozen is not None:
            self._wizard_retake(state)
            return
        if state["step"] == 2 and not self._ordered_captures(state):
            state["det_status"].configure(text="Capture at least one angle before finishing.", text_color=COLOR_DANGER)
            return
        self._wizard_next(state, on_finish, finish_text)

    def _wizard_next(self, state: dict, on_finish, finish_text: str) -> None:
        state["step"] += 1
        state["session"] = CaptureSession(state["student_key"])
        if state["step"] >= len(_ANGLES):
            state["reviewing"] = True
            self._show_frozen(state, self._ordered_captures(state)[0])
            state["step_caption"].configure(text=f"Review — {state['identity']}")
            state["det_status"].configure(text="This photo and the confirmed angle embeddings will be saved.", text_color=COLOR_SAFE)
            state["cap_btn"].configure(text="Save", state="normal")
            state["skip_btn"].configure(text="Retake", state="normal")
            self._update_pills(state)
            return
        self._wizard_refresh(state, finish_text)

    def _capture_save_payload(self, state):
        if not self._capture_current(state) or not state.get("reviewing") or state.get("capturing"):
            raise ValueError("Capture changed. Review the selected student's face again.")
        if "target_pk" in state:
            row = self.database.get_student(state["target_pk"])
            if row is None or row["student_id"] != state["target_student_id"]:
                raise ValueError("Student record changed. Reopen capture for the correct student.")
        return capture_payload(self._ordered_captures(state), state["student_key"])

    def _finish_enroll(self, modal, state: dict) -> None:
        try:
            blob, photo = self._capture_save_payload(state)
        except ValueError as exc:
            state["det_status"].configure(text=str(exc), text_color=COLOR_DANGER)
            return
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
        email = self._entries.get("email", None)
        email = email.get().strip() if email else ""
        contacts = {key: self._entries[key].get() for _, key in CONTACT_FIELDS}
        gender = self._gender_var.get()

        def _rearm(msg: str) -> None:
            state["capturing"] = False
            self._set_enroll_status(msg, error=True)
            if modal.winfo_exists():
                state["cap_btn"].configure(text="Save", state="normal")

        if not all([name, student_id, course, year_and_section]):
            _rearm("Name, student ID, course, and year/section are required. Contacts are optional.")
            return
        if self.database.student_id_exists(student_id):
            _rearm(f"Student ID '{student_id}' is already enrolled.")
            return
        if self.recognizer is None:
            _rearm("Face recognition not ready.")
            return

        # Commit only the already reviewed payload; block repeated saves.
        state["capturing"] = True
        state["cap_btn"].configure(text="Enrolling…", state="disabled")
        state["skip_btn"].configure(state="disabled")
        self._set_enroll_status("Saving confirmed captures…")

        try:
            self.database.insert_student(
                student_id=student_id, name=name, course=course,
                year_and_section=year_and_section, gender=gender,
                encoding=blob, photo=photo, email=email, contacts=contacts,
            )
        except Exception as exc:
            _rearm(f"Enrollment failed: {exc}")
            return

        def _do_enroll() -> None:
            import random
            import string
            from core.email_sender import send_credentials

            # Auto-generate a new password and upsert the student account.
            # upsert preserves an existing username (e.g. from self-registration)
            # while resetting the password to the newly-generated one.
            chars = string.ascii_letters + string.digits
            password = "".join(random.choices(chars, k=10))
            ok_acct, actual_username = self.database.upsert_student_account(
                student_id, student_id, password
            )

            email_note = ""
            if email and ok_acct:
                ok_mail, err = send_credentials(
                    to_email=email,
                    student_name=name,
                    student_id=student_id,
                    username=actual_username,
                    password=password,
                )
                email_note = " Email sent." if ok_mail else f" (Email failed: {err})"
            elif not ok_acct:
                email_note = " (Account creation failed — email not sent.)"

            modal.after(0, lambda: _on_success(email_note, actual_username, password))

        def _on_success(email_note: str, username: str, password: str) -> None:
            state["capturing"] = False
            self._clear_form()
            self._reload_students()
            self.recognizer.load_known_faces()
            msg = f"Enrolled! Login: {username} / {password}.{email_note}"
            self._set_status(msg, success=True)
            close = state.get("close")
            if close is not None:
                close()

        threading.Thread(target=_do_enroll, daemon=True).start()

    def _finish_update(self, modal, state: dict) -> None:
        try:
            blob, photo = self._capture_save_payload(state)
        except ValueError as exc:
            state["det_status"].configure(text=str(exc), text_color=COLOR_DANGER)
            return
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
            state["cap_btn"].configure(text="Save", state="normal")

        # Commit only the already reviewed payload; block repeated saves.
        state["capturing"] = True
        state["cap_btn"].configure(text="Updating…", state="disabled")
        state["skip_btn"].configure(state="disabled")
        state["det_status"].configure(text="Saving confirmed captures…", text_color=COLOR_TEXT_MUTED)

        def _on_done(ok: bool, photo: bytes) -> None:
            if not ok:
                _rearm("Update failed.")
                return
            self.recognizer.load_known_faces()
            self._reload_students()
            state["capturing"] = False
            self._set_status("Photo updated successfully.", success=True)
            close = state.get("close")
            if close is not None:
                close()

        try:
            ok = self.database.update_student_encoding(pk, blob, photo)
        except Exception as exc:
            _rearm(f"Update failed: {exc}")
            return
        _on_done(ok, photo)

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
        if self._update_close is not None:
            self._update_close()
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
            "alive": True, "target_pk": target_pk, "target_student_id": student["student_id"],
        }

        def _close() -> None:
            if state.get("capturing"):
                return
            state["alive"] = False
            self._update_close = None
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
        self._update_close = _close

        self._build_capture_wizard(
            card, state, on_finish=self._finish_update, finish_text=_UPDATE_FINISH_TEXT,
        )

        ctk.CTkButton(
            card, text="Cancel", height=30, corner_radius=CORNER_RADIUS,
            fg_color="transparent", hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MUTED, command=_close,
        ).pack(pady=(0, PADDING))

        modal.protocol("WM_DELETE_WINDOW", _close)
