"""Database & Record Management panel for CBVMS admin dashboard.

Five tabs, including daily Attendance Reports:
  1. Violation Reports  — searchable list of all logged violations
  2. Appeals Management — review, approve / reject student appeals + evidence viewer
  3. Evidence Files     — browse all uploaded evidence files
  4. Decision History   — chronological log of every appeal decision
"""

from __future__ import annotations

import io
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

from database.db_manager import CBVMSDatabase
from core.discipline import display_local_datetime
from core.evidence_integrity import original_evidence, supporting_evidence
from core.reports import (VIOLATION_HEADERS, violation_values, write_csv,
                          report_html, open_print_preview)
from ui.attendance_panel import AttendancePanel
from ui.components import (
    COLOR_ACCENT, COLOR_ACCENT_HOVER, COLOR_BG, COLOR_BORDER,
    COLOR_DANGER, COLOR_SAFE, COLOR_SURFACE, COLOR_TEXT, COLOR_TEXT_MUTED,
    COLOR_WARNING, CORNER_RADIUS, PADDING,
    body_font, body_small_font, heading_font,
)

_SAFE   = COLOR_SAFE
_DANGER = COLOR_DANGER
_WARN   = COLOR_WARNING
_MUTED  = COLOR_TEXT_MUTED
_ACCENT = COLOR_ACCENT


def _ts(raw: str) -> str:
    return display_local_datetime(raw)


def _status_color(status: str) -> str:
    s = (status or "").lower()
    if s == "approved":  return _SAFE
    if s == "rejected":  return _DANGER
    if s == "pending":   return _WARN
    return _MUTED


def _violation_status_label(status: str) -> str:
    normalized = (status or "").strip().lower()
    return {
        "pending_review": "Pending Review",
        "confirmed": "Confirmed",
        "auto_confirmed": "Auto Confirmed",
        "dismissed": "Dismissed",
        "unreviewed": "Legacy Unreviewed",
        "reviewed": "Legacy Reviewed",
    }.get(normalized, normalized.replace("_", " ").title() or "Unknown")


class RecordsPanel(ctk.CTkFrame):
    """Admin records management panel."""

    def __init__(self, master, *, database: CBVMSDatabase, username: str = "admin", on_open_appeals=None, **kwargs) -> None:
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database = database
        self.username = username
        self.on_open_appeals = on_open_appeals
        self._image_refs: list = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self._build_ui()
        self.grid_remove()

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 0))
        hdr.columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Records",
                     font=heading_font(20), text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(hdr,
                     text="Browse records, review evidence, and export reports.",
                     font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
            row=1, column=0, sticky="w")
        ctk.CTkButton(hdr, text="↻  Refresh", width=100, height=32,
                      corner_radius=CORNER_RADIUS, fg_color=COLOR_BORDER,
                      hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
                      command=self.refresh).grid(row=0, column=1, sticky="e")

        # Tab bar
        self._tab_var = tk.StringVar(value="violations")
        tab_row = ctk.CTkFrame(self, fg_color="transparent")
        tab_row.grid(row=1, column=0, sticky="ew", padx=PADDING, pady=(10, 0))
        self._tab_btns: dict[str, ctk.CTkButton] = {}
        tabs = [
            ("attendance", "Attendance"),
            ("violations", "Violations"),
            ("appeals",    "Appeals"),
            ("evidence",   "Evidence Files"),
            ("history",    "Decision History"),
        ]
        for column, (key, label) in enumerate(tabs):
            tab_row.grid_columnconfigure(column, weight=1, uniform="record_tabs")
            btn = ctk.CTkButton(
                tab_row, text=label, width=1, height=34, corner_radius=CORNER_RADIUS,
                fg_color=COLOR_ACCENT if key == "violations" else COLOR_SURFACE,
                hover_color=COLOR_ACCENT_HOVER,
                border_width=1, border_color=COLOR_BORDER,
                text_color=COLOR_TEXT, font=body_small_font(),
                command=lambda k=key: self._switch_tab(k),
            )
            btn.grid(row=0, column=column, sticky="ew", padx=(0, 6 if column < 4 else 0))
            self._tab_btns[key] = btn

        # Content area
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.grid(row=2, column=0, sticky="nsew", padx=PADDING, pady=PADDING)
        self._content.columnconfigure(0, weight=1)
        self._content.rowconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._configure_tree_style()
        self._build_violations_tab()
        self._attendance_frame = AttendancePanel(self._content, database=self.database)
        self._build_appeals_tab()
        self._build_evidence_tab()
        self._build_history_tab()
        self._switch_tab("violations")

    # ------------------------------------------------------------------ tab switch

    def _switch_tab(self, key: str) -> None:
        if key == "appeals" and self.on_open_appeals:
            self.on_open_appeals()
            return
        self._tab_var.set(key)
        for k, btn in self._tab_btns.items():
            btn.configure(fg_color=COLOR_ACCENT if k == key else COLOR_SURFACE)
        for frame in (self._attendance_frame, self._viol_frame, self._appeals_frame,
                      self._evidence_frame, self._history_frame):
            frame.grid_remove()
        {
            "attendance": self._attendance_frame,
            "violations": self._viol_frame,
            "appeals":    self._appeals_frame,
            "evidence":   self._evidence_frame,
            "history":    self._history_frame,
        }[key].grid(row=0, column=0, sticky="nsew")
        self.refresh()

    # ------------------------------------------------------------------ treeview style

    def _configure_tree_style(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Rec.Treeview", background=COLOR_BG, foreground=COLOR_TEXT,
                    fieldbackground=COLOR_BG, bordercolor=COLOR_BORDER, rowheight=26)
        s.configure("Rec.Treeview.Heading", background=COLOR_SURFACE,
                    foreground=COLOR_TEXT, relief="flat")
        s.map("Rec.Treeview",
              background=[("selected", COLOR_ACCENT)],
              foreground=[("selected", COLOR_TEXT)])

    def _make_tree(self, parent, columns: list[tuple[str, str, int]]) -> ttk.Treeview:
        cols = [c[0] for c in columns]
        tree = ttk.Treeview(parent, columns=cols, show="headings",
                            style="Rec.Treeview", selectmode="browse")
        for cid, label, width in columns:
            tree.heading(cid, text=label)
            tree.column(cid, width=width, anchor="w")
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        tree.configure(xscrollcommand=hsb.set)
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        return tree

    # ================================================================== TAB 1: Violations

    def _build_violations_tab(self) -> None:
        self._viol_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        self._viol_frame.columnconfigure(0, weight=1)
        self._viol_frame.rowconfigure(2, weight=1)
        self._violation_rows = {}
        self._snapshot_source = None
        self._snapshot_evidence = None
        self._record_windows = []
        self._snapshot_message = "Select a record to view evidence"
        self._evidence_width = 340
        self._split_resize_job = None

        # Search bar
        sbar = ctk.CTkFrame(self._viol_frame, fg_color="transparent")
        sbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        sbar.columnconfigure(1, weight=1)
        ctk.CTkLabel(sbar, text="Search", font=body_small_font()).grid(
            row=0, column=0, padx=(0, 8))
        self._viol_search = tk.StringVar()
        self._viol_search.trace_add("write", lambda *_: self._load_violations())
        ctk.CTkEntry(sbar, textvariable=self._viol_search,
                     placeholder_text="Search by student name, ID, or violation type…",
                     height=34, corner_radius=CORNER_RADIUS,
                     fg_color=COLOR_BG, border_color=COLOR_BORDER).grid(
            row=0, column=1, sticky="ew")
        export_menu = tk.Menu(self, tearoff=False)
        export_menu.add_command(label="Export CSV", command=self._export_violations)
        export_menu.add_command(label="Download Report", command=lambda: self._violation_report(False))
        export_menu.add_command(label="Print Report", command=lambda: self._violation_report(True))
        export_button = ctk.CTkButton(sbar, text="Export ▾", width=100, height=34)
        export_button.configure(command=lambda: export_menu.tk_popup(
            export_button.winfo_rootx(), export_button.winfo_rooty() + export_button.winfo_height()))
        export_button.grid(row=0, column=2, padx=(8, 0))
        self._viol_count = ctk.CTkLabel(self._viol_frame,
            text="Drag the divider to resize the table and evidence pane.",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED, anchor="w")
        self._viol_count.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        self._viol_split = tk.PanedWindow(self._viol_frame, orient=tk.HORIZONTAL,
            bg=COLOR_BORDER, bd=0, sashwidth=8, sashrelief=tk.FLAT,
            opaqueresize=True, width=1, height=1)
        self._viol_split.grid(row=2, column=0, sticky="nsew")

        # Left: list
        left = ctk.CTkFrame(self._viol_split, fg_color=COLOR_SURFACE,
                            corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        self._viol_split.add(left, minsize=int(self._apply_widget_scaling(220)), stretch="always")
        self._viol_tree = self._make_tree(left, [
            ("idx",        "#",           40),
            ("student",    "Student",     150),
            ("sid",        "Student ID",  100),
            ("type",       "Violation",   140),
            ("timestamp",  "Date & Time (UTC)", 150),
            ("status",     "Status",       130),
            ("appeal",     "Appeal",       80),
        ])
        self._viol_tree.bind("<<TreeviewSelect>>", self._on_viol_select)

        # Right: resizable evidence pane. A wrapper lets Tk manage the CTk scroll frame.
        right = ctk.CTkFrame(self._viol_split, width=340, fg_color=COLOR_SURFACE)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        self._viol_split.add(right, minsize=int(self._apply_widget_scaling(280)), stretch="never")
        self._viol_split.bind("<Configure>", self._initialize_violation_split)
        self._viol_split.bind("<ButtonRelease-1>", self._remember_violation_split, add="+")
        self._viol_detail = ctk.CTkScrollableFrame(
            right, fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_BORDER)
        self._viol_detail.grid(row=0, column=0, sticky="nsew")
        self._viol_detail.columnconfigure(0, weight=1)
        self._vd_labels = []

        def detail_label(text, row, font=None, color=COLOR_TEXT_MUTED):
            label = ctk.CTkLabel(self._viol_detail, text=text, width=1, height=20,
                font=font or body_small_font(), text_color=color, anchor="w", justify="left")
            label.grid(row=row, column=0, sticky="ew", padx=12, pady=(4, 0))
            self._vd_labels.append(label)
            return label

        self._vd_title = detail_label("Select a violation", 0, heading_font(16), COLOR_TEXT)
        self._vd_student = detail_label("Choose a record from the table.", 1)
        self._vd_identity = detail_label("", 2)
        self._vd_status = detail_label("", 3, body_font(12), COLOR_WARNING)
        detail_label("EVIDENCE SNAPSHOT", 4)
        self._vd_snapshot = tk.Canvas(self._viol_detail, width=1,
            height=int(self._apply_widget_scaling(210)), bg=COLOR_BG,
            highlightthickness=0, bd=0)
        self._vd_snapshot.grid(row=5, column=0, sticky="ew", padx=12, pady=8)
        self._vd_snapshot.bind("<Configure>", lambda _e: self._render_violation_snapshot())
        self._vd_snapshot.bind("<Button-1>", lambda _e: self._open_violation_evidence())
        self._vd_enlarge = ctk.CTkButton(self._viol_detail, text="Enlarge Evidence",
            height=30, state="disabled", command=self._open_violation_evidence)
        self._vd_enlarge.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._vd_fields = {}
        for row, (key, label) in enumerate((("date", "Date & time (UTC)"),
                ("course", "Course / year & section"), ("semester", "Semester"),
                ("strike", "Strike"), ("appeal", "Appeal")), start=7):
            self._vd_fields[key] = detail_label(f"{label}: —", row)
        self._viol_detail.bind("<Configure>", self._resize_violation_details)

    def _initialize_violation_split(self, event):
        # Place the sash after Tk has allocated the panes; doing it during Configure
        # lets the children's initial requested sizes overwrite the position.
        if self._split_resize_job is not None:
            self.after_cancel(self._split_resize_job)
        def place():
            self._split_resize_job = None
            self._viol_split.sash_place(0, max(int(self._apply_widget_scaling(220)),
                self._viol_split.winfo_width() - int(self._apply_widget_scaling(self._evidence_width)) - 8), 0)
        self._split_resize_job = self.after_idle(place)

    def _remember_violation_split(self, _event):
        def remember():
            x, _ = self._viol_split.sash_coord(0)
            self._evidence_width = self._reverse_widget_scaling(self._viol_split.winfo_width() - x - 8)
        self.after_idle(remember)

    def _resize_violation_details(self, event):
        width = max(80, int(self._reverse_widget_scaling(event.width)) - 24)
        for label in self._vd_labels:
            label.configure(wraplength=width)

    def _render_violation_snapshot(self):
        canvas = self._vd_snapshot
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        canvas.delete("all")
        canvas._ref = None
        if self._snapshot_source is None:
            canvas.configure(cursor="")
            canvas.create_text(width / 2, height / 2, text=self._snapshot_message,
                fill=COLOR_TEXT_MUTED, width=max(1, width - 20), justify="center")
            return
        img = self._snapshot_source.copy()
        img.thumbnail((max(1, width - 12), max(1, height - 12)), Image.Resampling.LANCZOS)
        canvas._ref = ImageTk.PhotoImage(img, master=canvas)
        canvas.create_image(width / 2, height / 2, image=canvas._ref)
        canvas.configure(cursor="hand2")

    def _open_violation_evidence(self):
        if self._snapshot_source is None:
            return
        source = self._snapshot_source.copy()
        modal = ctk.CTkToplevel(self)
        self._record_windows.append(modal)
        modal._evidence_key = self._snapshot_evidence["key"]
        modal._evidence_image = source
        modal.title(f"Violation #{self._snapshot_evidence['key'][1]} — {self._snapshot_evidence['label']}")
        modal.geometry("800x700")
        modal.minsize(440, 360)
        modal.transient(self.winfo_toplevel())
        modal.configure(fg_color=COLOR_BG)
        modal.grid_columnconfigure(0, weight=1)
        modal.grid_rowconfigure(1, weight=1)
        title = ctk.CTkLabel(modal, text=f"{self._vd_student.cget('text')} · {self._vd_title.cget('text')}",
            width=1, wraplength=700, font=heading_font(15))
        title.grid(row=0, column=0, sticky="ew", padx=16, pady=12)
        title.bind("<Configure>", lambda event: title.configure(
            wraplength=max(100, int(self._reverse_widget_scaling(event.width)) - 8)))
        canvas = tk.Canvas(modal, bg=COLOR_BG, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew", padx=16)
        def render(event):
            img = source.copy()
            img.thumbnail((max(1, event.width - 16), max(1, event.height - 16)), Image.Resampling.LANCZOS)
            canvas._ref = ImageTk.PhotoImage(img, master=canvas)
            canvas.delete("all")
            canvas.create_image(event.width / 2, event.height / 2, image=canvas._ref)
        canvas.bind("<Configure>", render)
        ctk.CTkButton(modal, text="Close", command=modal.destroy).grid(row=2, column=0, pady=12)
        modal.bind("<Escape>", lambda _e: modal.destroy())
        modal.after(100, modal.lift)

    def _load_violations(self) -> None:
        selection = self._viol_tree.selection()
        q = (self._viol_search.get() or "").strip().lower()
        rows = self.database.get_all_violations_full()
        self._violation_rows = {str(r["id"]): r for r in rows}
        for item in self._viol_tree.get_children():
            self._viol_tree.delete(item)
        appeals_map = {a["violation_id"]: a["status"]
                       for a in self.database.get_all_appeals_full()}
        self._violation_appeals = appeals_map
        for i, r in enumerate(rows, 1):
            name = r.get("student_name") or "—"
            sid  = r.get("student_id") or "—"
            vtype = (r.get("violation_type") or "—").replace("_", " ").title()
            ts   = _ts(r.get("timestamp", ""))
            stat = _violation_status_label(r.get("status") or "unreviewed")
            ap   = (appeals_map.get(r["id"]) or "None").title()
            if q and q not in name.lower() and q not in sid.lower() \
                    and q not in vtype.lower():
                continue
            self._viol_tree.insert("", "end", iid=str(r["id"]),
                                   values=(i, name, sid, vtype, ts, stat, ap))
        shown = len(self._viol_tree.get_children())
        self._viol_count.configure(text=f"{shown} records · Drag the divider to resize the table and evidence pane.")
        if selection and self._viol_tree.exists(selection[0]):
            self._viol_tree.selection_set(selection[0])
        self._on_viol_select()

    def _on_viol_select(self, _e=None) -> None:
        for window in self._record_windows:
            if window.winfo_exists():
                window.destroy()
        self._record_windows.clear()
        self._snapshot_evidence = None
        sel = self._viol_tree.selection()
        r = self._violation_rows.get(sel[0]) if sel else None
        self._snapshot_source = None
        self._vd_enlarge.configure(state="disabled")
        self._snapshot_message = "No snapshot on file"
        if r is None:
            self._vd_title.configure(text="Select a violation")
            self._vd_student.configure(text="Choose a record from the table.")
            self._vd_identity.configure(text="")
            self._vd_status.configure(text="")
            for label in self._vd_fields.values():
                label.configure(text="")
            self._snapshot_message = "Select a record to view evidence"
            self._render_violation_snapshot()
            return
        vtype = (r.get("violation_type") or "—").replace("_", " ").title()
        self._vd_title.configure(text=vtype)
        self._vd_student.configure(text=r.get("student_name") or "Unknown student")
        self._vd_identity.configure(text=f"Student ID: {r.get('student_id') or '—'} · Record #{r['id']}")
        status = r.get("status") or ""
        self._vd_status.configure(text=_violation_status_label(status), text_color=(
            COLOR_WARNING if status == "pending_review" else COLOR_DANGER
            if status in ("confirmed", "auto_confirmed") else COLOR_TEXT_MUTED))
        fields = {
            "date": f"Detected: {_ts(r.get('timestamp', ''))}\nPublished: {_ts(r.get('appeal_opened_at', ''))}",
            "course": f"Course / year & section: {r.get('course') or '—'} · {r.get('year_and_section') or '—'}",
            "semester": f"Semester: {r.get('semester_name') or '—'} · {r.get('school_year') or '—'}",
            "strike": f"Strike: {'Active' if r.get('strike_active') else 'Inactive / Not Awarded'}",
            "appeal": f"Appeal: {(self._violation_appeals.get(r['id']) or 'None').title()}",
        }
        for key, value in fields.items():
            self._vd_fields[key].configure(text=value)
        source = self._snapshot_evidence = original_evidence(r)
        self._snapshot_source = source['image']
        self._snapshot_message = source['warning'] or 'Original evidence unavailable'
        if self._snapshot_source is not None:
            self._vd_enlarge.configure(state='normal')
        self._render_violation_snapshot()

    def _export_violations(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            title="Export Violation Reports")
        if not path:
            return
        try:
            write_csv(path, VIOLATION_HEADERS, violation_values(self._report_violations()))
            messagebox.showinfo("Report saved", "Violation report saved successfully.", parent=self)
        except Exception as exc:
            messagebox.showerror("Report failed", str(exc), parent=self)

    def _report_violations(self):
        self._load_violations()
        visible = set(self._viol_tree.get_children())
        return [r for r in self.database.get_all_violations_full() if str(r["id"]) in visible]

    def _violation_report(self, printing):
        try:
            rows = violation_values(self._report_violations())
            description = "Search: " + (self._viol_search.get().strip() or "All records")
            if printing:
                open_print_preview("Violation Report", VIOLATION_HEADERS, rows, description)
            else:
                path = filedialog.asksaveasfilename(parent=self, title="Download Violation Report",
                    defaultextension=".html", initialfile="violation_report.html",
                    filetypes=[("Printable HTML report", "*.html")])
                if path:
                    from pathlib import Path
                    Path(path).write_text(report_html("Violation Report", VIOLATION_HEADERS,
                                                     rows, description), encoding="utf-8")
                    messagebox.showinfo("Report saved", "Open the report in a browser to print or save as PDF.", parent=self)
        except Exception as exc:
            messagebox.showerror("Report failed", str(exc), parent=self)

    # ================================================================== TAB 2: Appeals

    def _build_appeals_tab(self):
        self._appeals_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        ctk.CTkLabel(self._appeals_frame, text="Review appeals in the dedicated Appeals workspace.").pack(pady=20)
        ctk.CTkButton(self._appeals_frame, text="Open Appeals",
            command=lambda: self.on_open_appeals() if self.on_open_appeals else None).pack()

    def _load_appeals(self):
        pass  # legacy tab is a navigation link, never a second decision editor

    # ================================================================== TAB 3: Evidence

    def _build_evidence_tab(self) -> None:
        self._evidence_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        self._evidence_frame.columnconfigure(0, weight=2)
        self._evidence_frame.columnconfigure(1, weight=3)
        self._evidence_frame.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self._evidence_frame, fg_color=COLOR_SURFACE,
                            corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._ev_tree = self._make_tree(left, [
            ("student",   "Student",    130),
            ("filename",  "File",       160),
            ("type",      "Type",        60),
            ("uploaded",  "Uploaded",   120),
        ])
        self._ev_tree.bind("<<TreeviewSelect>>", self._on_ev_select)

        right = ctk.CTkFrame(self._evidence_frame, fg_color=COLOR_SURFACE,
                             corner_radius=CORNER_RADIUS,
                             border_width=1, border_color=COLOR_BORDER)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ctk.CTkLabel(right, text="Evidence Preview",
                     font=heading_font(14), text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, 8))

        self._ev_preview = tk.Label(right, text="Select a file to preview",
                                    bg=COLOR_BG, fg=COLOR_TEXT_MUTED, bd=0,
                                    font=("Helvetica", 12))
        self._ev_preview.grid(row=1, column=0, sticky="nsew", padx=PADDING, pady=(0, 8))

        self._ev_meta = ctk.CTkLabel(right, text="", font=body_small_font(),
                                      text_color=COLOR_TEXT_MUTED, anchor="w",
                                      justify="left")
        self._ev_meta.grid(row=2, column=0, sticky="w", padx=PADDING)

        ctk.CTkButton(right, text="Download File", height=36, corner_radius=CORNER_RADIUS,
                      fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                      font=body_small_font(), command=self._download_evidence).grid(
            row=3, column=0, sticky="ew", padx=PADDING, pady=PADDING)

        self._current_evidence: dict | None = None

    def _load_evidence(self) -> None:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT ef.*, s.name AS student_name
                   FROM evidence_files ef
                   LEFT JOIN students s ON s.student_id = ef.student_id
                   ORDER BY ef.uploaded_at DESC""").fetchall()
        for item in self._ev_tree.get_children():
            self._ev_tree.delete(item)
        for r in rows:
            name = r["student_name"] or r["student_id"] or "—"
            self._ev_tree.insert("", "end", iid=str(r["id"]),
                                 values=(name, r["filename"], r["file_type"],
                                         _ts(r["uploaded_at"])))
        self._on_ev_select()

    def _on_ev_select(self, _e=None) -> None:
        self._current_evidence = None
        self._ev_preview.configure(image='', text='Supporting image unavailable')
        self._ev_preview._ref = None
        self._ev_preview._evidence_key = None
        self._ev_meta.configure(text='Select an evidence record')
        sel = self._ev_tree.selection()
        if not sel:
            return
        eid = int(sel[0])
        ev = self.database.get_evidence_file(eid)
        if ev is None:
            return
        self._current_evidence = ev
        self._ev_meta.configure(
            text=f"Appeal #{ev['appeal_id']} · Evidence #{ev['id']}\nFile: {ev['filename']}\n"
                 f"Type: {ev['file_type']}\n"
                 f"Size: {len(ev['file_data']) // 1024 + 1} KB\n"
                 f"Uploaded: {_ts(ev['uploaded_at'])}")
        source = supporting_evidence(ev, size=(480,360))
        self._ev_preview._evidence_key = source['key']
        if source['image']:
            ph = ImageTk.PhotoImage(source['image'])
            self._ev_preview.configure(image=ph, text='')
            self._ev_preview._ref = ph
        else:
            self._ev_preview.configure(image='', text=source['warning'], wraplength=460)

    def _download_evidence(self) -> None:
        if self._current_evidence is None:
            return
        ev = self._current_evidence
        path = filedialog.asksaveasfilename(
            initialfile=ev["filename"],
            title="Save Evidence File",
            filetypes=[("All files", "*.*")])
        if not path:
            return
        with open(path, "wb") as f:
            f.write(ev["file_data"])

    # ================================================================== TAB 4: History

    def _build_history_tab(self) -> None:
        self._history_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        self._history_frame.columnconfigure(0, weight=1)
        self._history_frame.rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(self._history_frame, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(hdr, text="Complete log of all appeal decisions made by admins.",
                     font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(side="left")
        ctk.CTkButton(hdr, text="Export CSV", width=100, height=30,
                      corner_radius=CORNER_RADIUS, fg_color=_SAFE,
                      hover_color="#0EA371", font=body_small_font(),
                      command=self._export_history).pack(side="right")

        card = ctk.CTkFrame(self._history_frame, fg_color=COLOR_SURFACE,
                            corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        card.grid(row=1, column=0, sticky="nsew")
        self._hist_tree = self._make_tree(card, [
            ("decided",   "Date & Time",  130),
            ("student",   "Student",      140),
            ("sid",       "ID",            90),
            ("vtype",     "Violation",    140),
            ("decision",  "Decision",      90),
            ("ai",        "AI Rec.",      140),
            ("notes",     "Admin Notes",  200),
            ("by",        "Decided By",   90),
        ])

    def _load_history(self) -> None:
        rows = self.database.get_decision_history()
        for item in self._hist_tree.get_children():
            self._hist_tree.delete(item)
        for r in rows:
            vtype = (r.get("violation_type") or "—").replace("_", " ").title()
            self._hist_tree.insert("", "end", values=(
                _ts(r.get("decided_at", "")),
                r.get("student_name") or "—",
                r.get("student_id") or "—",
                vtype,
                (r.get("decision") or "—").title(),
                r.get("ai_recommendation") or "—",
                (r.get("admin_notes") or "")[:60],
                r.get("decided_by") or "admin",
            ))

    def _export_history(self) -> None:
        import csv
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            title="Export Decision History")
        if not path:
            return
        rows = self.database.get_decision_history(limit=10000)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Student", "Student ID", "Violation",
                        "Decision", "AI Recommendation", "Admin Notes",
                        "Decided By", "Appeal ID"])
            for r in rows:
                w.writerow([
                    r.get("decided_at"), r.get("student_name"), r.get("student_id"),
                    r.get("violation_type"), r.get("decision"), r.get("ai_recommendation"),
                    r.get("admin_notes"), r.get("decided_by"), r.get("appeal_id"),
                ])

    # ------------------------------------------------------------------ public

    def on_show(self) -> None:
        self.refresh()

    def open_alert(self, category: str, record_id: int | None = None) -> None:
        """Navigate from an alert, clearing filters so its appeal can be selected."""
        if category == "appeals":
            if self.on_open_appeals:
                self.on_open_appeals(record_id)
        else:
            self._switch_tab("violations")

    def refresh(self):
        self.database.process_expired_deadlines()
        self._image_refs.clear()
        tab = self._tab_var.get()
        if tab == "violations":
            self._load_violations()
        elif tab == "attendance":
            self._attendance_frame.refresh()
        elif tab == "evidence":
            self._load_evidence()
        elif tab == "history":
            self._load_history()
