"""Violation history panel — Computer Based Vision Monitoring System (CBVMS)."""

from __future__ import annotations

import csv
import io
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

from database.db_manager import CBVMSDatabase
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
    CBVMSCard,
    body_font,
    body_small_font,
    heading_font,
)


PAGE_SIZE = 20

_CHIP_H = 28
_CHIP_RADIUS = 999


def _parse_yyyy_mm_dd(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _human_violation_type(value: str) -> str:
    v = (value or "").strip()
    mapping = {
        "face_detected": "Face Detected",
        "unknown_person": "Unknown Person",
        "wrong_hair_color": "Wrong Hair Color",
        "no_id_badge": "No ID Badge",
        "wrong_uniform": "Wrong Uniform",
        "earring_violation": "Earring Violation",
        "with_earring": "Earring Violation",
    }
    return mapping.get(v, v.replace("_", " ").title() if v else "—")


def _violation_badge_color(value: str) -> str:
    v = (value or "").strip()
    if v == "face_detected":
        return COLOR_SAFE
    if v == "unknown_person":
        return COLOR_DANGER
    if v in ("no_id_badge",):
        return COLOR_DANGER
    if v in ("wrong_uniform",):
        return COLOR_WARNING
    if v in ("wrong_hair_color", "earring_violation", "with_earring"):
        return COLOR_ACCENT
    return COLOR_BORDER


class ViolationLogPanel(CBVMSCard):
    def __init__(self, master, *, database: CBVMSDatabase, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.database = database

        self._selected_violation_id: int | None = None
        self._selected_violation_row: dict | None = None
        self._snapshot_photo: ImageTk.PhotoImage | None = None

        self._page = 1
        self._page_count = 1
        self._total_records = 0
        self._rows: list[dict] = []

        self._search_after_id: str | None = None
        self._active_date_chip: str = "all"  # "all" | "today" | "week" | "month" | "custom"
        self._pending_delete_action = None   # callable — stored when confirm strip is shown

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)   # top bar
        self.grid_rowconfigure(1, weight=0)   # confirm strip (hidden by default)
        self.grid_rowconfigure(2, weight=1)   # main area
        self.grid_rowconfigure(3, weight=0)   # status banner (hidden by default)

        self._build_top_bar()
        self._build_confirm_strip()
        self._build_status_banner()
        self._build_main_area()
        self._configure_tree_style()

        self.refresh()

    # ------------------------------------------------------------------
    # Top bar: search + date chips + filters
    # ------------------------------------------------------------------

    def _build_top_bar(self) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 8))
        top.grid_columnconfigure(0, weight=1)

        self._search_var = tk.StringVar()
        self._type_var = tk.StringVar(value="All")
        self._status_var = tk.StringVar(value="All")
        self._from_var = tk.StringVar()
        self._to_var = tk.StringVar()

        # Row 0: search bar (full width)
        search_row = ctk.CTkFrame(top, fg_color="transparent")
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        search_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            search_row, text="🔍", font=body_font(16), text_color=COLOR_TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=(0, 4))

        self._search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self._search_var,
            placeholder_text="Search by student name or ID…",
            height=36,
            fg_color=COLOR_BG,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        self._search_entry.grid(row=0, column=1, sticky="ew")

        # Row 1: date quick-chips + custom date entries
        chip_row = ctk.CTkFrame(top, fg_color="transparent")
        chip_row.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        date_label = ctk.CTkLabel(
            chip_row, text="Date:", font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        )
        date_label.pack(side="left", padx=(0, 8))

        self._chip_buttons: dict[str, ctk.CTkButton] = {}
        chips = [
            ("all",    "All Time"),
            ("today",  "Today"),
            ("week",   "This Week"),
            ("month",  "This Month"),
            ("custom", "Custom ▾"),
        ]
        for key, label in chips:
            btn = ctk.CTkButton(
                chip_row,
                text=label,
                height=_CHIP_H,
                corner_radius=_CHIP_RADIUS,
                fg_color=COLOR_ACCENT if key == "all" else COLOR_BORDER,
                hover_color=COLOR_ACCENT_HOVER,
                text_color=COLOR_TEXT,
                font=body_small_font(),
                command=lambda k=key: self._on_date_chip(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._chip_buttons[key] = btn

        # Custom date range (hidden by default)
        self._custom_date_frame = ctk.CTkFrame(chip_row, fg_color="transparent")
        self._from_entry = ctk.CTkEntry(
            self._custom_date_frame,
            textvariable=self._from_var,
            placeholder_text="From YYYY-MM-DD",
            width=136,
            height=_CHIP_H,
            fg_color=COLOR_BG,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
            font=body_small_font(),
        )
        self._from_entry.pack(side="left", padx=(0, 4))
        self._to_entry = ctk.CTkEntry(
            self._custom_date_frame,
            textvariable=self._to_var,
            placeholder_text="To YYYY-MM-DD",
            width=136,
            height=_CHIP_H,
            fg_color=COLOR_BG,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
            font=body_small_font(),
        )
        self._to_entry.pack(side="left")
        # custom date frame NOT packed yet — shown when "Custom" chip clicked

        # Row 2: type, status dropdowns + action buttons
        filter_row = ctk.CTkFrame(top, fg_color="transparent")
        filter_row.grid(row=2, column=0, sticky="ew")

        self._type_combo = ctk.CTkComboBox(
            filter_row,
            values=["All", "face_detected", "unknown_person"],
            variable=self._type_var,
            height=32,
            width=180,
            fg_color=COLOR_BG,
            border_color=COLOR_BORDER,
            button_color=COLOR_BORDER,
            button_hover_color=COLOR_ACCENT_HOVER,
            dropdown_fg_color=COLOR_SURFACE,
            dropdown_hover_color=COLOR_ACCENT_HOVER,
            text_color=COLOR_TEXT,
            font=body_small_font(),
        )
        self._type_combo.pack(side="left", padx=(0, 6))

        self._status_combo = ctk.CTkComboBox(
            filter_row,
            values=["All", "Unreviewed", "Reviewed"],
            variable=self._status_var,
            height=32,
            width=140,
            fg_color=COLOR_BG,
            border_color=COLOR_BORDER,
            button_color=COLOR_BORDER,
            button_hover_color=COLOR_ACCENT_HOVER,
            dropdown_fg_color=COLOR_SURFACE,
            dropdown_hover_color=COLOR_ACCENT_HOVER,
            text_color=COLOR_TEXT,
            font=body_small_font(),
        )
        self._status_combo.pack(side="left", padx=(0, 12))

        # Separator visual gap
        ctk.CTkFrame(filter_row, width=1, height=28, fg_color=COLOR_BORDER).pack(
            side="left", padx=(0, 12)
        )

        ctk.CTkButton(
            filter_row,
            text="Delete Selected",
            height=32,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_DANGER,
            hover_color="#DC2626",
            text_color=COLOR_TEXT,
            font=body_small_font(),
            command=self._delete_selected,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            filter_row,
            text="Delete All Filtered",
            height=32,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_WARNING,
            hover_color="#D97706",
            text_color=COLOR_TEXT,
            font=body_small_font(),
            command=self._delete_all_filtered,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            filter_row,
            text="Reset",
            height=32,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER,
            hover_color=COLOR_ACCENT_HOVER,
            font=body_small_font(),
            command=self._clear_filters,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            filter_row,
            text="Export CSV",
            height=32,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_SAFE,
            hover_color="#0EA371",
            text_color=COLOR_TEXT,
            font=body_small_font(),
            command=self._export_csv,
        ).pack(side="right")

        # Wire live search (debounced 400 ms)
        for var in (self._search_var, self._type_var, self._status_var, self._from_var, self._to_var):
            var.trace_add("write", self._schedule_search)

    # ------------------------------------------------------------------
    # Main area: treeview + detail panel
    # ------------------------------------------------------------------

    def _build_confirm_strip(self) -> None:
        """Inline confirmation bar (replaces OS messagebox — CTk-safe on macOS)."""
        self._confirm_strip = ctk.CTkFrame(
            self,
            fg_color="#2D1F0E",
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_WARNING,
            height=48,
        )
        # NOT gridded yet — shown on demand via grid/grid_remove

        inner = ctk.CTkFrame(self._confirm_strip, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=PADDING, pady=8)

        self._confirm_msg_label = ctk.CTkLabel(
            inner,
            text="",
            font=body_small_font(),
            text_color=COLOR_TEXT,
            anchor="w",
        )
        self._confirm_msg_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            inner,
            text="Cancel",
            height=30,
            width=80,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER,
            hover_color=COLOR_ACCENT_HOVER,
            font=body_small_font(),
            command=self._hide_confirm_strip,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            inner,
            text="Yes, Delete",
            height=30,
            width=100,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_DANGER,
            hover_color="#DC2626",
            font=body_small_font(),
            command=self._run_pending_delete,
        ).pack(side="right")

    def _show_confirm_strip(self, message: str, action) -> None:
        self._pending_delete_action = action
        self._confirm_msg_label.configure(text=message)
        self._confirm_strip.grid(row=1, column=0, sticky="ew", padx=PADDING, pady=(0, 4))

    def _hide_confirm_strip(self) -> None:
        self._pending_delete_action = None
        self._confirm_strip.grid_remove()

    def _run_pending_delete(self) -> None:
        action = self._pending_delete_action
        self._hide_confirm_strip()
        if action is not None:
            action()

    def _build_status_banner(self) -> None:
        """Inline status banner (replaces floating toasts — no separate window)."""
        self._status_banner = ctk.CTkLabel(
            self,
            text="",
            font=body_small_font(),
            text_color=COLOR_TEXT,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            anchor="w",
            height=30,
        )
        # NOT gridded yet — shown on demand
        self._status_clear_job: str | None = None

    def _set_status(self, message: str, kind: str = "info") -> None:
        """Show a transient inline status message in-panel (no popup window)."""
        if not hasattr(self, "_status_banner"):
            return
        colors = {
            "info": COLOR_ACCENT,
            "success": COLOR_SAFE,
            "error": COLOR_DANGER,
            "warning": COLOR_WARNING,
        }
        self._status_banner.configure(
            text=f"  {message}",
            text_color=colors.get(kind, COLOR_TEXT),
        )
        self._status_banner.grid(row=3, column=0, sticky="ew", padx=PADDING, pady=(0, PADDING))
        if self._status_clear_job:
            try:
                self.after_cancel(self._status_clear_job)
            except Exception:
                pass
        self._status_clear_job = self.after(3000, self._clear_status)

    def _clear_status(self) -> None:
        self._status_clear_job = None
        if hasattr(self, "_status_banner"):
            self._status_banner.grid_remove()

    # ------------------------------------------------------------------

    def _build_main_area(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=2, column=0, sticky="nsew", padx=PADDING, pady=(0, PADDING))
        main.grid_columnconfigure(0, weight=3)
        main.grid_columnconfigure(1, weight=2)
        main.grid_rowconfigure(0, weight=1)

        # LEFT: Table + pagination
        left = ctk.CTkFrame(
            main,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        table_host = ctk.CTkFrame(left, fg_color="transparent")
        table_host.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        table_host.grid_columnconfigure(0, weight=1)
        table_host.grid_rowconfigure(0, weight=1)

        columns = ("idx", "student_name", "student_id", "violation_type", "timestamp", "status")
        self._tree = ttk.Treeview(
            table_host,
            columns=columns,
            show="headings",
            style="CBVMS.Treeview",
            selectmode="extended",   # multi-select: Ctrl+click, Shift+click
        )
        self._tree.heading("idx", text="#")
        self._tree.heading("student_name", text="Student Name")
        self._tree.heading("student_id", text="Student ID")
        self._tree.heading("violation_type", text="Type")
        self._tree.heading("timestamp", text="Date & Time")
        self._tree.heading("status", text="Status")

        self._tree.column("idx", width=46, anchor="center", stretch=False)
        self._tree.column("student_name", width=200, anchor="w")
        self._tree.column("student_id", width=110, anchor="w", stretch=False)
        self._tree.column("violation_type", width=150, anchor="w")
        self._tree.column("timestamp", width=150, anchor="w", stretch=False)
        self._tree.column("status", width=100, anchor="w", stretch=False)

        vsb = ttk.Scrollbar(table_host, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self._tree.tag_configure("unreviewed", foreground=COLOR_WARNING)
        self._tree.tag_configure("reviewed", foreground=COLOR_TEXT_MUTED)

        self._tree.bind("<<TreeviewSelect>>", self._on_row_select)

        pager = ctk.CTkFrame(left, fg_color="transparent")
        pager.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

        self._prev_btn = ctk.CTkButton(
            pager, text="Prev", height=32, width=80,
            corner_radius=CORNER_RADIUS, fg_color=COLOR_BORDER,
            hover_color=COLOR_ACCENT_HOVER, command=self._prev_page,
        )
        self._prev_btn.pack(side="left")

        self._page_label = ctk.CTkLabel(
            pager, text="Page 1 of 1", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._page_label.pack(side="left", padx=12)

        self._next_btn = ctk.CTkButton(
            pager, text="Next", height=32, width=80,
            corner_radius=CORNER_RADIUS, fg_color=COLOR_BORDER,
            hover_color=COLOR_ACCENT_HOVER, command=self._next_page,
        )
        self._next_btn.pack(side="left")

        self._count_label = ctk.CTkLabel(
            pager, text="0 records", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._count_label.pack(side="right")

        # RIGHT: Detail card
        right = ctk.CTkFrame(
            main,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            right, text="Detection Details",
            font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, 8))

        # Rounded box wrapping a plain tk.Label. tk.Label handles raw ImageTk
        # reassignment reliably; CTkLabel + ImageTk leaves dangling Tcl image
        # references on clear, which crashed re-renders after a delete.
        snap_box = ctk.CTkFrame(
            right, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS,
            width=300, height=230,
        )
        snap_box.grid(row=1, column=0, sticky="ew", padx=PADDING, pady=(0, 12))
        snap_box.grid_propagate(False)
        snap_box.grid_rowconfigure(0, weight=1)
        snap_box.grid_columnconfigure(0, weight=1)
        self._snapshot_label = tk.Label(
            snap_box, text="No snapshot", bd=0,
            bg=COLOR_BG, fg=COLOR_TEXT_MUTED,
        )
        self._snapshot_label.grid(row=0, column=0)

        self._detail_name = ctk.CTkLabel(right, text="Student: —", font=body_font(13), text_color=COLOR_TEXT)
        self._detail_name.grid(row=2, column=0, sticky="w", padx=PADDING)

        self._detail_id = ctk.CTkLabel(right, text="Student ID: —", font=body_font(13), text_color=COLOR_TEXT)
        self._detail_id.grid(row=3, column=0, sticky="w", padx=PADDING, pady=(2, 0))

        self._detail_course = ctk.CTkLabel(right, text="Course: —", font=body_font(13), text_color=COLOR_TEXT)
        self._detail_course.grid(row=4, column=0, sticky="w", padx=PADDING, pady=(2, 0))

        self._detail_year = ctk.CTkLabel(right, text="Year & Section: —", font=body_font(13), text_color=COLOR_TEXT)
        self._detail_year.grid(row=5, column=0, sticky="w", padx=PADDING, pady=(2, 0))

        badge_row = ctk.CTkFrame(right, fg_color="transparent")
        badge_row.grid(row=6, column=0, sticky="ew", padx=PADDING, pady=(12, 0))

        self._violation_badge = ctk.CTkLabel(
            badge_row, text="—", font=body_font(12), text_color=COLOR_TEXT,
            fg_color=COLOR_BORDER, corner_radius=999, padx=10, pady=4,
        )
        self._violation_badge.pack(side="left")

        self._detail_timestamp = ctk.CTkLabel(
            right, text="Timestamp: —", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._detail_timestamp.grid(row=7, column=0, sticky="w", padx=PADDING, pady=(10, 0))

        self._detail_status = ctk.CTkLabel(
            right, text="Status: —", font=body_font(12), text_color=COLOR_TEXT_MUTED,
        )
        self._detail_status.grid(row=8, column=0, sticky="w", padx=PADDING, pady=(2, 0))

        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.grid(row=9, column=0, sticky="ew", padx=PADDING, pady=(14, PADDING))
        btn_row.grid_columnconfigure((0, 1), weight=1, uniform="detail_btns")

        self._toggle_btn = ctk.CTkButton(
            btn_row, text="Mark as Reviewed", height=34,
            corner_radius=CORNER_RADIUS, fg_color=COLOR_SAFE, hover_color="#0EA371",
            command=self._toggle_status, state="disabled",
        )
        self._toggle_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self._delete_one_btn = ctk.CTkButton(
            btn_row, text="Delete", height=34,
            corner_radius=CORNER_RADIUS, fg_color=COLOR_DANGER, hover_color="#DC2626",
            command=self._delete_current, state="disabled",
        )
        self._delete_one_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # --- Appeal & AI section ---
        ctk.CTkFrame(right, height=1, fg_color=COLOR_BORDER).grid(
            row=10, column=0, sticky="ew", padx=PADDING, pady=(8, 0))

        self._appeal_section = ctk.CTkFrame(right, fg_color="transparent")
        self._appeal_section.grid(row=11, column=0, sticky="ew", padx=PADDING, pady=(8, PADDING))
        self._appeal_section.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self._appeal_section, text="Student Appeal",
                     font=heading_font(13), text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        self._appeal_status_lbl = ctk.CTkLabel(
            self._appeal_section, text="No appeal submitted",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._appeal_status_lbl.grid(row=1, column=0, sticky="w")

        self._ai_rec_lbl = ctk.CTkLabel(
            self._appeal_section, text="",
            font=body_font(12), text_color=COLOR_TEXT,
            fg_color=COLOR_BORDER, corner_radius=999, padx=10, pady=3)
        # not gridded yet — shown when a recommendation exists

        self._ai_analysis_lbl = ctk.CTkLabel(
            self._appeal_section, text="",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
            wraplength=260, justify="left")
        # not gridded yet — shown when analysis text exists

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
    # Search & filter logic
    # ------------------------------------------------------------------

    def _schedule_search(self, *_) -> None:
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(400, self._do_search)

    def _do_search(self) -> None:
        self._page = 1
        self.refresh()

    def _on_date_chip(self, key: str) -> None:
        self._active_date_chip = key
        today = date.today()

        # Update chip button colors
        for k, btn in self._chip_buttons.items():
            btn.configure(fg_color=COLOR_ACCENT if k == key else COLOR_BORDER)

        # Show/hide custom date entries
        if key == "custom":
            self._custom_date_frame.pack(side="left", padx=(8, 0))
        else:
            self._custom_date_frame.pack_forget()

        # Set from/to vars based on selection
        if key == "all":
            self._from_var.set("")
            self._to_var.set("")
        elif key == "today":
            self._from_var.set(today.strftime("%Y-%m-%d"))
            self._to_var.set(today.strftime("%Y-%m-%d"))
        elif key == "week":
            monday = today - timedelta(days=today.weekday())
            self._from_var.set(monday.strftime("%Y-%m-%d"))
            self._to_var.set(today.strftime("%Y-%m-%d"))
        elif key == "month":
            self._from_var.set(today.replace(day=1).strftime("%Y-%m-%d"))
            self._to_var.set(today.strftime("%Y-%m-%d"))
        # "custom" — user fills in from/to manually; trace_add fires search

        if key != "custom":
            self._page = 1
            self.refresh()

    def _clear_filters(self) -> None:
        self._search_var.set("")
        self._type_var.set("All")
        self._status_var.set("All")
        self._on_date_chip("all")

    def _filters_to_where(self) -> tuple[str, list]:
        params: list = []
        clauses: list[str] = []

        q = (self._search_var.get() or "").strip()
        if q:
            clauses.append("(v.student_name LIKE ? OR v.student_id LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])

        vtype = (self._type_var.get() or "All").strip()
        if vtype and vtype != "All":
            clauses.append("v.violation_type = ?")
            params.append(vtype)

        status = (self._status_var.get() or "All").strip()
        if status == "Unreviewed":
            clauses.append("v.status = 'unreviewed'")
        elif status == "Reviewed":
            clauses.append("v.status = 'reviewed'")

        from_date = _parse_yyyy_mm_dd(self._from_var.get())
        to_date = _parse_yyyy_mm_dd(self._to_var.get())
        if (self._from_var.get().strip() and not from_date) or \
           (self._to_var.get().strip() and not to_date):
            raise ValueError("Date range must be in YYYY-MM-DD format.")
        if from_date:
            clauses.append("date(v.timestamp) >= date(?)")
            params.append(from_date)
        if to_date:
            clauses.append("date(v.timestamp) <= date(?)")
            params.append(to_date)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # ------------------------------------------------------------------
    # Data loading & display
    # ------------------------------------------------------------------

    def _fetch_page(self) -> None:
        where, params = self._filters_to_where()

        with self.database.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM violations v {where}", params
            ).fetchone()
            self._total_records = int(total["c"] if total else 0)
            self._page_count = max(1, (self._total_records + PAGE_SIZE - 1) // PAGE_SIZE)
            self._page = min(max(1, self._page), self._page_count)
            offset = (self._page - 1) * PAGE_SIZE

            rows = conn.execute(
                f"""
                SELECT v.id, v.student_id, v.student_name, v.violation_type, v.timestamp, v.status
                FROM violations v
                {where}
                ORDER BY datetime(v.timestamp) DESC, v.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, PAGE_SIZE, offset],
            ).fetchall()

        self._rows = [dict(r) for r in rows]

    def refresh(self) -> None:
        try:
            self._fetch_page()
        except ValueError as exc:
            self._set_status(str(exc), "error")
            return
        except Exception as exc:
            self._set_status(f"Failed to load violations: {exc}", "error")
            return

        for item in self._tree.get_children():
            self._tree.delete(item)

        start_idx = (self._page - 1) * PAGE_SIZE
        for i, row in enumerate(self._rows, start=1):
            status = (row.get("status") or "unreviewed").strip().lower()
            tag = "unreviewed" if status == "unreviewed" else "reviewed"
            self._tree.insert(
                "", "end",
                iid=str(row["id"]),
                values=(
                    start_idx + i,
                    row.get("student_name") or "—",
                    row.get("student_id") or "—",
                    _human_violation_type(row.get("violation_type") or ""),
                    (row.get("timestamp") or "—")[:19],
                    "Unreviewed" if status == "unreviewed" else "Reviewed",
                ),
                tags=(tag,),
            )

        self._page_label.configure(text=f"Page {self._page} of {self._page_count}")
        self._count_label.configure(text=f"{self._total_records} records")
        self._prev_btn.configure(state="normal" if self._page > 1 else "disabled")
        self._next_btn.configure(state="normal" if self._page < self._page_count else "disabled")

        if self._selected_violation_id is not None:
            iid = str(self._selected_violation_id)
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)
                self._load_violation_details(self._selected_violation_id)
                return

        self._clear_details()

    def _prev_page(self) -> None:
        if self._page <= 1:
            return
        self._page -= 1
        self.refresh()

    def _next_page(self) -> None:
        if self._page >= self._page_count:
            return
        self._page += 1
        self.refresh()

    # ------------------------------------------------------------------
    # Row selection & detail panel
    # ------------------------------------------------------------------

    def _on_row_select(self, _event: tk.Event | None = None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        # Load details for the most-recently clicked item (last in selection)
        try:
            vid = int(sel[-1])
        except Exception:
            return
        self._selected_violation_id = vid
        self._load_violation_details(vid)

    def _clear_details(self) -> None:
        self._selected_violation_row = None
        self._snapshot_photo = None
        self._snapshot_label.configure(image="", text="No snapshot")
        self._detail_name.configure(text="Student: —")
        self._detail_id.configure(text="Student ID: —")
        self._detail_course.configure(text="Course: —")
        self._detail_year.configure(text="Year & Section: —")
        self._violation_badge.configure(text="—", fg_color=COLOR_BORDER)
        self._detail_timestamp.configure(text="Timestamp: —")
        self._detail_status.configure(text="Status: —")
        self._toggle_btn.configure(state="disabled", text="Mark as Reviewed", fg_color=COLOR_SAFE)
        self._delete_one_btn.configure(state="disabled")
        self._appeal_status_lbl.configure(text="No appeal submitted", text_color=COLOR_TEXT_MUTED)
        self._ai_rec_lbl.grid_remove()
        self._ai_analysis_lbl.grid_remove()

    def _load_violation_details(self, violation_id: int) -> None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    v.id, v.student_id, v.student_name, v.violation_type,
                    v.timestamp, v.snapshot, v.status,
                    s.course AS course, s.year_and_section AS year_and_section
                FROM violations v
                LEFT JOIN students s ON s.student_id = v.student_id
                WHERE v.id = ?
                """,
                (violation_id,),
            ).fetchone()
            appeal_row = conn.execute(
                "SELECT * FROM appeals WHERE violation_id = ?", (violation_id,)
            ).fetchone()

        if row is None:
            self._clear_details()
            return

        data = dict(row)
        self._selected_violation_row = data

        self._detail_name.configure(text=f"Student: {data.get('student_name') or '—'}")
        self._detail_id.configure(text=f"Student ID: {data.get('student_id') or '—'}")
        self._detail_course.configure(text=f"Course: {data.get('course') or '—'}")
        self._detail_year.configure(text=f"Year & Section: {data.get('year_and_section') or '—'}")

        vtype_raw = data.get("violation_type") or ""
        self._violation_badge.configure(
            text=_human_violation_type(vtype_raw),
            fg_color=_violation_badge_color(vtype_raw),
        )

        ts = (data.get("timestamp") or "—")[:19]
        self._detail_timestamp.configure(text=f"Timestamp: {ts}")

        status = (data.get("status") or "unreviewed").strip().lower()
        self._detail_status.configure(
            text=f"Status: {'Unreviewed' if status == 'unreviewed' else 'Reviewed'}"
        )
        if status == "unreviewed":
            self._toggle_btn.configure(
                state="normal", text="Mark as Reviewed",
                fg_color=COLOR_SAFE, hover_color="#0EA371",
            )
        else:
            self._toggle_btn.configure(
                state="normal", text="Mark as Unreviewed",
                fg_color=COLOR_WARNING, hover_color="#D97706",
            )
        self._delete_one_btn.configure(state="normal")

        # Appeal + AI recommendation
        self._ai_rec_lbl.grid_remove()
        self._ai_analysis_lbl.grid_remove()
        if appeal_row is None:
            self._appeal_status_lbl.configure(
                text="No appeal submitted", text_color=COLOR_TEXT_MUTED)
        else:
            ap = dict(appeal_row)
            ap_status = (ap.get("status") or "pending").title()
            ap_submitted = (ap.get("submitted_at") or "")[:16]
            self._appeal_status_lbl.configure(
                text=f"Appeal: {ap_status}  ·  Submitted {ap_submitted}",
                text_color=COLOR_WARNING if ap_status == "Pending"
                else COLOR_SAFE if ap_status == "Approved" else COLOR_DANGER,
            )
            ai_rec = (ap.get("ai_recommendation") or "").strip()
            ai_conf = (ap.get("ai_confidence") or "").strip()
            ai_text = (ap.get("ai_analysis") or "").strip()
            if ai_rec and ai_rec != "Unavailable":
                rec_color = COLOR_SAFE if "Valid" in ai_rec else COLOR_DANGER
                pill = f"🤖  {ai_rec}" + (f"  ·  {ai_conf}" if ai_conf and ai_conf != "—" else "")
                self._ai_rec_lbl.configure(text=pill, fg_color=rec_color)
                self._ai_rec_lbl.grid(row=2, column=0, sticky="w", pady=(6, 0))
                if ai_text:
                    self._ai_analysis_lbl.configure(text=ai_text)
                    self._ai_analysis_lbl.grid(row=3, column=0, sticky="w", pady=(4, 0))
            elif ai_rec == "Unavailable":
                self._ai_rec_lbl.configure(text="🤖  AI: Unavailable", fg_color=COLOR_BORDER)
                self._ai_rec_lbl.grid(row=2, column=0, sticky="w", pady=(6, 0))
                if ai_text:
                    self._ai_analysis_lbl.configure(text=ai_text)
                    self._ai_analysis_lbl.grid(row=3, column=0, sticky="w", pady=(4, 0))
            else:
                self._ai_rec_lbl.configure(text="🤖  AI: Analyzing…", fg_color=COLOR_BORDER)
                self._ai_rec_lbl.grid(row=2, column=0, sticky="w", pady=(6, 0))

        snap = data.get("snapshot")
        if not snap:
            self._snapshot_photo = None
            self._snapshot_label.configure(image="", text="No snapshot")
            return

        try:
            img = Image.open(io.BytesIO(snap))
            img = img.convert("RGB")
            img.thumbnail((300, 220), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img, master=self._snapshot_label)
            self._snapshot_photo = photo
            self._snapshot_label.configure(image=photo, text="")
            self._snapshot_label.image = photo  # extra strong ref on the widget
        except Exception:
            self._snapshot_photo = None
            self._snapshot_label.configure(image="", text="Snapshot unavailable")

    # ------------------------------------------------------------------
    # Actions: status toggle, delete, export
    # ------------------------------------------------------------------

    def _toggle_status(self) -> None:
        if self._selected_violation_row is None:
            return
        vid = int(self._selected_violation_row["id"])
        current = (self._selected_violation_row.get("status") or "unreviewed").strip().lower()
        new_status = "reviewed" if current == "unreviewed" else "unreviewed"
        try:
            with self.database.connect() as conn:
                conn.execute("UPDATE violations SET status = ? WHERE id = ?", (new_status, vid))
                conn.commit()
        except Exception as exc:
            self._set_status(f"Failed to update status: {exc}", "error")
            return
        self._selected_violation_row["status"] = new_status
        self.refresh()

    def _delete_current(self) -> None:
        if self._selected_violation_row is None:
            return
        vid = int(self._selected_violation_row["id"])

        def _do() -> None:
            self.database.delete_violation(vid)
            self._selected_violation_id = None
            self._set_status("Record deleted.", "success")
            self.refresh()

        self._show_confirm_strip("Delete this detection record?", _do)

    def _delete_selected(self) -> None:
        selected = self._tree.selection()
        if not selected:
            self._set_status("No records selected. Click a row first.", "info")
            return
        count = len(selected)
        ids = [int(s) for s in selected]

        def _do() -> None:
            deleted = self.database.delete_violations(ids)
            self._selected_violation_id = None
            self._set_status(f"Deleted {deleted} record(s).", "success")
            self.refresh()

        self._show_confirm_strip(f"Delete {count} selected record(s)?", _do)

    def _delete_all_filtered(self) -> None:
        try:
            where, params = self._filters_to_where()
        except ValueError as exc:
            self._set_status(str(exc), "error")
            return

        label = "all" if not where else "filtered"
        count = self._total_records

        def _do() -> None:
            deleted = self.database.delete_all_violations(where, params)
            self._selected_violation_id = None
            self._set_status(f"Deleted {deleted} record(s).", "success")
            self.refresh()

        self._show_confirm_strip(
            f"Delete ALL {count} {label} record(s)? This cannot be undone.", _do
        )

    def _export_csv(self) -> None:
        try:
            where, params = self._filters_to_where()
        except ValueError as exc:
            self._set_status(str(exc), "error")
            return

        filename = filedialog.asksaveasfilename(
            title="Export Violations CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not filename:
            return

        try:
            with self.database.connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT v.student_name, v.student_id, v.violation_type, v.timestamp, v.status
                    FROM violations v
                    {where}
                    ORDER BY datetime(v.timestamp) DESC, v.id DESC
                    """,
                    params,
                ).fetchall()

            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Student Name", "Student ID", "Type", "Date & Time", "Status"])
                for r in rows:
                    status = (r["status"] or "unreviewed").strip().lower()
                    writer.writerow([
                        r["student_name"] or "—",
                        r["student_id"] or "—",
                        _human_violation_type(r["violation_type"] or ""),
                        (r["timestamp"] or "—")[:19],
                        "Unreviewed" if status == "unreviewed" else "Reviewed",
                    ])
        except Exception as exc:
            self._set_status(f"Export failed: {exc}", "error")
            return

        self._set_status(f"Exported {len(rows)} records to CSV.", "success")
