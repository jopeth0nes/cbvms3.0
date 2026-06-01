"""Database & Record Management panel for CBVMS admin dashboard.

Four tabs:
  1. Violation Reports  — searchable list of all logged violations
  2. Appeals Management — review, approve / reject student appeals + evidence viewer
  3. Evidence Files     — browse all uploaded evidence files
  4. Decision History   — chronological log of every appeal decision
"""

from __future__ import annotations

import io
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

from database.db_manager import CBVMSDatabase
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
    if not raw:
        return "—"
    return str(raw)[:16].replace("T", " ")


def _status_color(status: str) -> str:
    s = (status or "").lower()
    if s == "approved":  return _SAFE
    if s == "rejected":  return _DANGER
    if s == "pending":   return _WARN
    return _MUTED


class RecordsPanel(ctk.CTkFrame):
    """Admin records management panel."""

    def __init__(self, master, *, database: CBVMSDatabase, **kwargs) -> None:
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database = database
        self._image_refs: list = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_ui()
        self.grid_remove()

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 0))
        hdr.columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Database & Record Management",
                     font=heading_font(20), text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(hdr,
                     text="Violation reports · evidence files · appeal decisions · history",
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
            ("violations", "📋  Violation Reports"),
            ("appeals",    "📝  Appeals Management"),
            ("evidence",   "📎  Evidence Files"),
            ("history",    "🕒  Decision History"),
        ]
        for key, label in tabs:
            btn = ctk.CTkButton(
                tab_row, text=label, height=34, corner_radius=CORNER_RADIUS,
                fg_color=COLOR_ACCENT if key == "violations" else COLOR_SURFACE,
                hover_color=COLOR_ACCENT_HOVER,
                border_width=1, border_color=COLOR_BORDER,
                text_color=COLOR_TEXT, font=body_small_font(),
                command=lambda k=key: self._switch_tab(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._tab_btns[key] = btn

        # Content area
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.grid(row=2, column=0, sticky="nsew", padx=PADDING, pady=PADDING)
        self._content.columnconfigure(0, weight=1)
        self._content.rowconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._configure_tree_style()
        self._build_violations_tab()
        self._build_appeals_tab()
        self._build_evidence_tab()
        self._build_history_tab()
        self._switch_tab("violations")

    # ------------------------------------------------------------------ tab switch

    def _switch_tab(self, key: str) -> None:
        self._tab_var.set(key)
        for k, btn in self._tab_btns.items():
            btn.configure(fg_color=COLOR_ACCENT if k == key else COLOR_SURFACE)
        for frame in (self._viol_frame, self._appeals_frame,
                      self._evidence_frame, self._history_frame):
            frame.grid_remove()
        {
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
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        return tree

    # ================================================================== TAB 1: Violations

    def _build_violations_tab(self) -> None:
        self._viol_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        self._viol_frame.columnconfigure(0, weight=3)
        self._viol_frame.columnconfigure(1, weight=2)
        self._viol_frame.rowconfigure(1, weight=1)

        # Search bar
        sbar = ctk.CTkFrame(self._viol_frame, fg_color="transparent")
        sbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        sbar.columnconfigure(0, weight=1)
        self._viol_search = tk.StringVar()
        self._viol_search.trace_add("write", lambda *_: self._load_violations())
        ctk.CTkEntry(sbar, textvariable=self._viol_search,
                     placeholder_text="Search by student name, ID, or violation type…",
                     height=34, corner_radius=CORNER_RADIUS,
                     fg_color=COLOR_BG, border_color=COLOR_BORDER).grid(
            row=0, column=0, sticky="ew")
        ctk.CTkButton(sbar, text="Export CSV", width=100, height=34,
                      corner_radius=CORNER_RADIUS, fg_color=_SAFE,
                      hover_color="#0EA371", font=body_small_font(),
                      command=self._export_violations).grid(row=0, column=1, padx=(8, 0))

        # Left: list
        left = ctk.CTkFrame(self._viol_frame, fg_color=COLOR_SURFACE,
                            corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self._viol_tree = self._make_tree(left, [
            ("idx",        "#",           40),
            ("student",    "Student",     150),
            ("sid",        "Student ID",  100),
            ("type",       "Violation",   140),
            ("timestamp",  "Date & Time", 130),
            ("status",     "Status",       80),
            ("appeal",     "Appeal",       80),
        ])
        self._viol_tree.bind("<<TreeviewSelect>>", self._on_viol_select)

        # Right: detail card
        self._viol_detail = ctk.CTkScrollableFrame(
            self._viol_frame, fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_BORDER)
        self._viol_detail.grid(row=1, column=1, sticky="nsew")
        self._viol_detail.columnconfigure(0, weight=1)
        self._vd_title    = ctk.CTkLabel(self._viol_detail, text="Select a violation",
                                          font=heading_font(14), text_color=COLOR_TEXT,
                                          anchor="w")
        self._vd_title.grid(row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, 4))
        self._vd_snapshot = tk.Label(self._viol_detail, text="No snapshot",
                                     bg=COLOR_BG, fg=COLOR_TEXT_MUTED, bd=0)
        self._vd_snapshot.grid(row=1, column=0, padx=PADDING, pady=(0, 8))
        self._vd_info     = ctk.CTkLabel(self._viol_detail, text="", font=body_small_font(),
                                          text_color=COLOR_TEXT_MUTED, anchor="w",
                                          justify="left", wraplength=280)
        self._vd_info.grid(row=2, column=0, sticky="w", padx=PADDING)

    def _load_violations(self) -> None:
        q = (self._viol_search.get() or "").strip().lower()
        rows = self.database.get_all_violations_full()
        for item in self._viol_tree.get_children():
            self._viol_tree.delete(item)
        appeals_map = {a["violation_id"]: a["status"]
                       for a in self.database.get_all_appeals_full()}
        for i, r in enumerate(rows, 1):
            name = r.get("student_name") or "—"
            sid  = r.get("student_id") or "—"
            vtype = (r.get("violation_type") or "—").replace("_", " ").title()
            ts   = _ts(r.get("timestamp", ""))
            stat = (r.get("status") or "unreviewed").title()
            ap   = (appeals_map.get(r["id"]) or "None").title()
            if q and q not in name.lower() and q not in sid.lower() \
                    and q not in vtype.lower():
                continue
            self._viol_tree.insert("", "end", iid=str(r["id"]),
                                   values=(i, name, sid, vtype, ts, stat, ap))

    def _on_viol_select(self, _e=None) -> None:
        sel = self._viol_tree.selection()
        if not sel:
            return
        vid = int(sel[0])
        rows = self.database.get_all_violations_full()
        r = next((x for x in rows if x["id"] == vid), None)
        if r is None:
            return
        vtype = (r.get("violation_type") or "—").replace("_", " ").title()
        self._vd_title.configure(text=vtype)
        info = (
            f"Student: {r.get('student_name') or '—'}\n"
            f"ID: {r.get('student_id') or '—'}\n"
            f"Course: {r.get('course') or '—'}  |  {r.get('year_and_section') or '—'}\n"
            f"Date: {_ts(r.get('timestamp', ''))}\n"
            f"Status: {(r.get('status') or '').title()}"
        )
        self._vd_info.configure(text=info)
        snap = r.get("snapshot") or r.get("violation_snapshot")
        if snap:
            try:
                img = Image.open(io.BytesIO(snap)).convert("RGB")
                img.thumbnail((280, 210), Image.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                self._image_refs.append(ph)
                self._vd_snapshot.configure(image=ph, text="")
                self._vd_snapshot._ref = ph
            except Exception:
                self._vd_snapshot.configure(image="", text="Snapshot unavailable")
        else:
            self._vd_snapshot.configure(image="", text="No snapshot")

    def _export_violations(self) -> None:
        import csv
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            title="Export Violation Reports")
        if not path:
            return
        rows = self.database.get_all_violations_full()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ID", "Student Name", "Student ID", "Course",
                        "Year & Section", "Violation Type", "Timestamp", "Status"])
            for r in rows:
                w.writerow([r.get("id"), r.get("student_name"), r.get("student_id"),
                            r.get("course"), r.get("year_and_section"),
                            r.get("violation_type"), r.get("timestamp"), r.get("status")])

    # ================================================================== TAB 2: Appeals

    def _build_appeals_tab(self) -> None:
        self._appeals_frame = ctk.CTkFrame(self._content, fg_color="transparent")
        self._appeals_frame.columnconfigure(0, weight=5)
        self._appeals_frame.columnconfigure(1, weight=4)
        self._appeals_frame.rowconfigure(1, weight=1)

        # Filter bar
        fbar = ctk.CTkFrame(self._appeals_frame, fg_color="transparent")
        fbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(fbar, text="Filter:", font=body_small_font(),
                     text_color=COLOR_TEXT_MUTED).pack(side="left", padx=(0, 8))
        self._appeal_filter = tk.StringVar(value="All")
        for val in ("All", "Pending", "Approved", "Rejected"):
            ctk.CTkRadioButton(fbar, text=val, variable=self._appeal_filter,
                               value=val, font=body_small_font(),
                               command=self._load_appeals).pack(side="left", padx=6)

        # Left: appeals list
        left = ctk.CTkFrame(self._appeals_frame, fg_color=COLOR_SURFACE,
                            corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self._appeal_tree = self._make_tree(left, [
            ("student",   "Student",    140),
            ("sid",       "ID",          90),
            ("vtype",     "Violation",  130),
            ("submitted", "Submitted",  120),
            ("status",    "Status",      80),
            ("ai",        "AI",         120),
        ])
        self._appeal_tree.bind("<<TreeviewSelect>>", self._on_appeal_select)

        # Right: appeal detail + action panel
        right = ctk.CTkScrollableFrame(
            self._appeals_frame, fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_BORDER)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        self._ap_right = right

        self._ap_title = ctk.CTkLabel(right, text="Select an appeal",
                                       font=heading_font(14), text_color=COLOR_TEXT,
                                       anchor="w")
        self._ap_title.grid(row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, 4))

        self._ap_info = ctk.CTkLabel(right, text="", font=body_small_font(),
                                      text_color=COLOR_TEXT_MUTED, anchor="w",
                                      justify="left", wraplength=300)
        self._ap_info.grid(row=1, column=0, sticky="w", padx=PADDING)

        # AI recommendation
        self._ap_ai_frame = ctk.CTkFrame(right, fg_color=COLOR_BG, corner_radius=8)
        self._ap_ai_frame.grid(row=2, column=0, sticky="ew", padx=PADDING, pady=(8, 0))
        self._ap_ai_lbl = ctk.CTkLabel(self._ap_ai_frame, text="",
                                        font=body_small_font(), text_color=COLOR_TEXT_MUTED,
                                        anchor="w", justify="left", wraplength=280)
        self._ap_ai_lbl.pack(anchor="w", padx=10, pady=8)

        # Student reasoning
        ctk.CTkLabel(right, text="Student Reasoning:", font=heading_font(12),
                     text_color=COLOR_TEXT_MUTED, anchor="w").grid(
            row=3, column=0, sticky="w", padx=PADDING, pady=(10, 2))
        self._ap_reason = ctk.CTkLabel(right, text="", font=body_small_font(),
                                        text_color=COLOR_TEXT, anchor="w",
                                        justify="left", wraplength=300)
        self._ap_reason.grid(row=4, column=0, sticky="w", padx=PADDING)

        # Evidence thumbnail
        ctk.CTkLabel(right, text="Evidence:", font=heading_font(12),
                     text_color=COLOR_TEXT_MUTED, anchor="w").grid(
            row=5, column=0, sticky="w", padx=PADDING, pady=(10, 2))
        self._ap_ev_lbl = ctk.CTkLabel(right, text="No evidence attached",
                                        font=body_small_font(), text_color=COLOR_TEXT_MUTED,
                                        anchor="w")
        self._ap_ev_lbl.grid(row=6, column=0, sticky="w", padx=PADDING)
        self._ap_ev_img = tk.Label(right, text="", bg=COLOR_SURFACE, bd=0)
        self._ap_ev_img.grid(row=7, column=0, sticky="w", padx=PADDING, pady=(4, 0))

        # Admin notes + action
        ctk.CTkLabel(right, text="Admin Notes:", font=heading_font(12),
                     text_color=COLOR_TEXT_MUTED, anchor="w").grid(
            row=8, column=0, sticky="w", padx=PADDING, pady=(12, 2))
        self._ap_notes = ctk.CTkTextbox(right, height=80, corner_radius=8,
                                         fg_color=COLOR_BG,
                                         border_color=COLOR_BORDER, border_width=1,
                                         text_color=COLOR_TEXT)
        self._ap_notes.grid(row=9, column=0, sticky="ew", padx=PADDING)

        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.grid(row=10, column=0, sticky="ew", padx=PADDING, pady=(10, PADDING))
        btn_row.columnconfigure((0, 1), weight=1, uniform="ab")
        self._ap_approve_btn = ctk.CTkButton(
            btn_row, text="✓  Approve", height=38, corner_radius=CORNER_RADIUS,
            fg_color=_SAFE, hover_color="#0EA371", state="disabled",
            command=lambda: self._decide_appeal("approved"))
        self._ap_approve_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._ap_reject_btn = ctk.CTkButton(
            btn_row, text="✗  Reject", height=38, corner_radius=CORNER_RADIUS,
            fg_color=_DANGER, hover_color="#DC2626", state="disabled",
            command=lambda: self._decide_appeal("rejected"))
        self._ap_reject_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self._ap_decision_lbl = ctk.CTkLabel(right, text="", font=body_small_font(),
                                              text_color=_SAFE, anchor="w")
        self._ap_decision_lbl.grid(row=11, column=0, sticky="w", padx=PADDING, pady=(4, 0))

        self._current_appeal: dict | None = None

    def _load_appeals(self) -> None:
        flt = self._appeal_filter.get()
        rows = self.database.get_all_appeals_full()
        for item in self._appeal_tree.get_children():
            self._appeal_tree.delete(item)
        for r in rows:
            status = (r.get("status") or "pending")
            if flt != "All" and status.lower() != flt.lower():
                continue
            name  = r.get("student_name_full") or r.get("student_id") or "—"
            sid   = r.get("student_id") or "—"
            vtype = (r.get("violation_type") or "—").replace("_", " ").title()
            sub   = _ts(r.get("submitted_at", ""))
            ai    = (r.get("ai_recommendation") or "Pending")
            self._appeal_tree.insert("", "end", iid=str(r["id"]),
                                     values=(name, sid, vtype, sub, status.title(), ai))

    def _on_appeal_select(self, _e=None) -> None:
        sel = self._appeal_tree.selection()
        if not sel:
            return
        aid = int(sel[0])
        rows = self.database.get_all_appeals_full()
        r = next((x for x in rows if x["id"] == aid), None)
        if r is None:
            return
        self._current_appeal = r

        vtype = (r.get("violation_type") or "—").replace("_", " ").title()
        self._ap_title.configure(text=f"Appeal: {vtype}")
        info = (
            f"Student: {r.get('student_name_full') or r.get('student_id') or '—'}\n"
            f"ID: {r.get('student_id') or '—'}\n"
            f"Violation on: {_ts(r.get('violation_ts', ''))}\n"
            f"Appeal submitted: {_ts(r.get('submitted_at', ''))}\n"
            f"Current status: {(r.get('status') or 'pending').title()}"
        )
        self._ap_info.configure(text=info)

        ai_rec  = (r.get("ai_recommendation") or "").strip()
        ai_conf = (r.get("ai_confidence") or "").strip()
        ai_text = (r.get("ai_analysis") or "").strip()
        if ai_rec:
            rec_color = _SAFE if "Valid" in ai_rec else _DANGER if "Invalid" in ai_rec else _MUTED
            ai_str = f"🤖  {ai_rec}"
            if ai_conf and ai_conf != "—":
                ai_str += f"  ·  {ai_conf} confidence"
            if ai_text:
                ai_str += f"\n\n{ai_text}"
            self._ap_ai_lbl.configure(text=ai_str, text_color=rec_color)
        else:
            self._ap_ai_lbl.configure(text="🤖  AI analysis pending…",
                                       text_color=_MUTED)

        self._ap_reason.configure(text=r.get("reason") or "—")

        # Evidence
        evidence = self.database.get_evidence_for_appeal(aid)
        self._ap_ev_img.configure(image="", text="")
        if evidence:
            ev = evidence[0]
            self._ap_ev_lbl.configure(
                text=f"📎 {ev['filename']}  ({len(ev['file_data']) // 1024 + 1} KB)")
            if ev.get("file_type") == "image":
                try:
                    img = Image.open(io.BytesIO(ev["file_data"])).convert("RGB")
                    img.thumbnail((280, 200), Image.LANCZOS)
                    ph = ImageTk.PhotoImage(img)
                    self._image_refs.append(ph)
                    self._ap_ev_img.configure(image=ph)
                    self._ap_ev_img._ref = ph
                except Exception:
                    pass
        else:
            self._ap_ev_lbl.configure(text="No evidence attached")

        # Admin notes + buttons
        self._ap_notes.delete("1.0", "end")
        existing_notes = (r.get("admin_notes") or "").strip()
        if existing_notes:
            self._ap_notes.insert("1.0", existing_notes)

        status = (r.get("status") or "pending").lower()
        is_pending = status == "pending"
        self._ap_approve_btn.configure(state="normal" if is_pending else "disabled")
        self._ap_reject_btn.configure(state="normal" if is_pending else "disabled")
        self._ap_decision_lbl.configure(
            text="" if is_pending else f"Decision: {status.title()}",
            text_color=_SAFE if status == "approved" else _DANGER)

    def _decide_appeal(self, decision: str) -> None:
        if self._current_appeal is None:
            return
        notes = self._ap_notes.get("1.0", "end-1c").strip()
        aid = self._current_appeal["id"]
        ok = self.database.update_appeal_decision(aid, decision, notes)
        if ok:
            self._ap_approve_btn.configure(state="disabled")
            self._ap_reject_btn.configure(state="disabled")
            color = _SAFE if decision == "approved" else _DANGER
            self._ap_decision_lbl.configure(
                text=f"✓ Marked as {decision.title()} successfully.",
                text_color=color)
            # Notify the student
            student_id = self._current_appeal.get("student_id", "")
            vtype = (self._current_appeal.get("violation_type") or "violation").replace("_", " ")
            msg = (f"Your appeal for '{vtype}' has been {decision}."
                   + (f" Admin note: {notes}" if notes else ""))
            self.database.insert_notification(student_id,
                f"Appeal {decision.title()}",
                msg,
                self._current_appeal.get("violation_id"))
            self._load_appeals()
        else:
            self._ap_decision_lbl.configure(text="Error saving decision.", text_color=_DANGER)

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

    def _on_ev_select(self, _e=None) -> None:
        sel = self._ev_tree.selection()
        if not sel:
            return
        eid = int(sel[0])
        ev = self.database.get_evidence_file(eid)
        if ev is None:
            return
        self._current_evidence = ev
        self._ev_meta.configure(
            text=f"File: {ev['filename']}\n"
                 f"Type: {ev['file_type']}\n"
                 f"Size: {len(ev['file_data']) // 1024 + 1} KB\n"
                 f"Uploaded: {_ts(ev['uploaded_at'])}")
        if ev.get("file_type") == "image":
            try:
                img = Image.open(io.BytesIO(ev["file_data"])).convert("RGB")
                img.thumbnail((480, 360), Image.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                self._image_refs.append(ph)
                self._ev_preview.configure(image=ph, text="")
                self._ev_preview._ref = ph
                return
            except Exception:
                pass
        self._ev_preview.configure(image="", text=f"[{ev['file_type'].upper()}]\n{ev['filename']}")

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

    def refresh(self) -> None:
        self._image_refs.clear()
        tab = self._tab_var.get()
        if tab == "violations":
            self._load_violations()
        elif tab == "appeals":
            self._load_appeals()
        elif tab == "evidence":
            self._load_evidence()
        elif tab == "history":
            self._load_history()
