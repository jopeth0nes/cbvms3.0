"""Administrator suspension workspace using the existing discipline ledger."""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from core.discipline import parse_db_datetime, utc_now, violation_display_name
from core.academics import YEAR_LEVELS, legacy_year_section, resolve_pair, NEEDS_REVIEW, display_academics
from core.student_status import suspension_label
from core.report_worker import ReportWorker, latest
from ui.components import (
    COLOR_ACCENT, COLOR_ACCENT_HOVER, COLOR_BG, COLOR_BORDER, COLOR_DANGER,
    COLOR_SURFACE, COLOR_TEXT, COLOR_TEXT_MUTED, COLOR_WARNING, CORNER_RADIUS,
    PADDING, body_small_font, heading_font,
)


ALL_YEARS = "All Year Levels"
ALL_COURSES = "All Courses"
NO_STUDENT_MATCHES = "No students match your search and filters."
SELECT_STUDENT = "Select a student to view suspension details."


def saved_year_level(value):
    return legacy_year_section(value)[0] or None


def saved_course(value):
    pair = resolve_pair('', value)
    return pair[1] if pair else NEEDS_REVIEW


def strike_explanation(row, current_term_id):
    """Explain the stored ledger membership, without inventing historical strikes."""
    if row.get("strike_active"):
        if row.get("strike_semester_id") == current_term_id:
            return "Counts this semester"
        return "Active strike in a previous semester"
    if row.get("strike_id") is not None:
        reason = (row.get("strike_removal_reason") or "deactivated").replace("_", " ")
        return f"Removed: {reason}"
    return {
        "pending_review": "Pending review — no strike yet",
        "dismissed": "Dismissed — no strike",
        "unreviewed": "Legacy record — no retroactive strike",
        "reviewed": "Legacy record — no retroactive strike",
    }.get(row.get("status"), "No strike awarded")


def suspension_state(row):
    if row.get("lifted_at"):
        return "Lifted / Cancelled"
    now = utc_now()
    end = parse_db_datetime(row.get("ends_at"))
    if end and end <= now:
        return "Expired"
    start = parse_db_datetime(row.get("starts_at"))
    return "Scheduled" if start and start > now else "Active"


def configure_table_style():
    """Use scoped clam elements so macOS native headings remain legible on navy."""
    style = ttk.Style()
    for custom, original in (("Suspensions.heading", "Treeheading.cell"),
                             ("Suspensions.field", "Treeview.field")):
        if custom not in style.element_names():
            style.element_create(custom, "from", "clam", original)
    style.layout("Suspensions.Treeview", [("Suspensions.field", {"sticky": "nswe", "children": [
        ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
    style.layout("Suspensions.Treeview.Heading", [("Suspensions.heading", {"sticky": "nswe"}),
        ("Treeheading.padding", {"sticky": "nswe", "children": [("Treeheading.text", {"sticky": "we"})]})])
    style.configure("Suspensions.Treeview", background=COLOR_BG, foreground=COLOR_TEXT,
        fieldbackground=COLOR_BG, rowheight=32, bordercolor=COLOR_BORDER,
        font=("Segoe UI", 11), relief="flat", borderwidth=0)
    style.configure("Suspensions.Treeview.Heading", background=COLOR_SURFACE,
        foreground=COLOR_TEXT_MUTED, relief="flat", font=("Segoe UI", 10, "bold"), padding=(10, 8))
    style.map("Suspensions.Treeview", background=[("selected", COLOR_ACCENT)], foreground=[("selected", COLOR_TEXT)])


class SuspensionsPanel(ctk.CTkFrame):
    """Mounted only in the administrator dashboard, like Student Management.

    Workers perform database reads/deadline processing; only Tk's thread renders.
    Generation IDs discard old responses when the selection changes or closes.
    """

    def __init__(self, master, *, database, username, embedded=False, **kwargs):
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.embedded = embedded
        self._action_loading = False
        self.database = database
        self.username = username
        self.student_id = None
        self._students = []
        self._violations = {}
        self._suspensions = {}
        self._term = {}
        self._generation = 0
        self._results = queue.Queue(maxsize=4)
        self._read_worker = ReportWorker()
        self._poll_job = None
        self._loading = False
        self._ready = False
        self._updating_filters = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self._build_ui()
        self._clear_details()

    def _label(self, parent, text="", **kwargs):
        label = ctk.CTkLabel(parent, text=text, anchor="w", justify="left",
                             width=1, font=body_small_font(), **kwargs)
        label.pack(fill="x", pady=3)
        # CTkLabel.bind targets the inner text widget. Measuring that widget would
        # repeatedly shrink its own wrapping and can keep Tk's layout loop busy.
        self._wrap_to_parent(label, parent)
        return label

    def _wrap_to_parent(self, label, parent):
        pending = None
        def resize(event):
            nonlocal pending
            width = max(120, int(self._reverse_widget_scaling(event.width)) - 12)
            if pending is not None:
                label.after_cancel(pending)
            def apply_wrap():
                nonlocal pending
                pending = None
                if abs(label.cget("wraplength") - width) > 4:
                    label.configure(wraplength=width)
            pending = label.after(40, apply_wrap)
        parent.bind("<Configure>", resize, add="+")

    def _table(self, parent, columns, *, height=3):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                            height=height, selectmode="browse", style="Suspensions.Treeview")
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=70, stretch=True)
        tree.grid(row=0, column=0, sticky="nsew")
        for vertical in (True, False):
            bar = ttk.Scrollbar(frame, orient="vertical" if vertical else "horizontal",
                                command=tree.yview if vertical else tree.xview)
            bar.grid(row=0 if vertical else 1, column=1 if vertical else 0,
                     sticky="ns" if vertical else "ew")
            tree.configure(**{"yscrollcommand" if vertical else "xscrollcommand": bar.set})
        return tree

    def _build_ui(self):
        configure_table_style()
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(header, text="Suspensions", font=heading_font(24),
                     text_color=COLOR_TEXT).pack(side="left")
        ctk.CTkButton(header, text="Refresh records", width=128, height=34,
                      fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER,
                      command=self.refresh).pack(side="right")
        if self.embedded:
            header.grid_remove()
        search_tools = ctk.CTkFrame(self, fg_color=COLOR_SURFACE,
                                    corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER)
        search_tools.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        search_tools.grid_columnconfigure(0, weight=1)
        self._search_var = tk.StringVar()
        self._year_var = tk.StringVar(value=ALL_YEARS)
        self._course_var = tk.StringVar(value=ALL_COURSES)
        toolbar = ctk.CTkFrame(search_tools, fg_color="transparent")
        self._search_entry = ctk.CTkEntry(search_tools, textvariable=self._search_var,
            placeholder_text="Search students by name or student ID", height=36,
            fg_color=COLOR_BG, border_color=COLOR_BORDER, corner_radius=8)
        self._search_entry.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 8))
        toolbar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        toolbar.grid_columnconfigure((1, 3), weight=1)
        for column, text in ((0, "Year Level:"), (2, "Course:")):
            ctk.CTkLabel(toolbar, text=text, font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
                row=0, column=column, sticky="w", padx=(0 if column == 0 else 12, 6))
        menu_style = dict(height=34, width=140, dynamic_resizing=False,
                          fg_color=COLOR_SURFACE, button_color=COLOR_ACCENT,
                          button_hover_color=COLOR_ACCENT_HOVER, text_color=COLOR_TEXT,
                          font=body_small_font(), dropdown_fg_color=COLOR_SURFACE,
                          dropdown_text_color=COLOR_TEXT, dropdown_hover_color=COLOR_ACCENT_HOVER)
        self._year_filter = ctk.CTkOptionMenu(toolbar, variable=self._year_var,
                                             values=[ALL_YEARS, *YEAR_LEVELS], **menu_style)
        self._year_filter.grid(row=0, column=1, sticky="ew")
        self._course_filter = ctk.CTkOptionMenu(toolbar, variable=self._course_var,
                                               values=[ALL_COURSES], **menu_style)
        self._course_filter.grid(row=0, column=3, sticky="ew")
        self._clear_filters_btn = ctk.CTkButton(toolbar, text="Clear Filters", width=100, height=34,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, command=self._clear_filters)
        self._clear_filters_btn.grid(row=0, column=4, sticky="e", padx=(12, 0))
        for variable in (self._search_var, self._year_var, self._course_var):
            variable.trace_add("write", self._on_filters_changed)
        selector = ctk.CTkFrame(self, fg_color="transparent")
        selector.grid(row=2, column=0, sticky="ew")
        self._result_count = self._label(selector, "0 of 0 students", text_color=COLOR_TEXT_MUTED)
        self._student_tree = self._table(selector, [
            ("name", "Student name", 230), ("sid", "Student ID", 155),
            ("year", "Year Level", 110), ("course", "Course", 130)], height=2)
        self._student_tree.bind("<<TreeviewSelect>>", self._on_student_select)
        summary = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS)
        summary.grid(row=3, column=0, sticky="ew", pady=8)
        inner = ctk.CTkFrame(summary, fg_color="transparent")
        inner.pack(fill="x", padx=PADDING, pady=8)
        self._identity = self._label(inner, text_color=COLOR_TEXT)
        self._identity.configure(font=heading_font(16))
        metrics = ctk.CTkFrame(inner, fg_color="transparent", height=92)
        metrics.pack(fill="x", pady=(6, 4))
        metrics.grid_propagate(False)
        metrics.grid_rowconfigure(0, weight=1)
        metrics.grid_columnconfigure(0, weight=3, uniform="summary")
        metrics.grid_columnconfigure(1, weight=2, uniform="summary")
        for column, title in enumerate(("SEMESTER STRIKES", "SUSPENSION STATUS")):
            card = ctk.CTkFrame(metrics, fg_color=COLOR_BG, corner_radius=8)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else 0)
            content = ctk.CTkFrame(card, fg_color="transparent")
            content.pack(fill="both", expand=True, padx=10, pady=6)
            self._label(content, title, text_color=COLOR_TEXT_MUTED).configure(font=heading_font(10))
            value = self._label(content, text_color=COLOR_ACCENT if column == 0 else COLOR_TEXT)
            if column == 0:
                self._strike_summary = value
            else:
                self._suspension_status = value
        self._explanation = self._label(inner, text_color=COLOR_TEXT_MUTED)

        self._tabs = ctk.CTkTabview(self, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
            segmented_button_fg_color=COLOR_BG, segmented_button_selected_color=COLOR_ACCENT,
            segmented_button_selected_hover_color=COLOR_ACCENT_HOVER,
            segmented_button_unselected_color=COLOR_BG,
            segmented_button_unselected_hover_color=COLOR_BORDER)
        self._tabs.grid(row=4, column=0, sticky="nsew")
        violations, history = (self._tabs.add(name) for name in
                               ("Violation History", "Suspension History"))
        self._violation_empty = self._label(violations, text_color=COLOR_TEXT_MUTED)
        # Reserve the detail/action footers before the expanding tables, so the
        # filter toolbar cannot push those controls below a short window's edge.
        self._violation_detail = self._label(violations, text_color=COLOR_TEXT_MUTED)
        self._violation_detail.pack_configure(side="bottom")
        self._violation_tree = self._table(violations, [
            ("id", "Violation ID", 85), ("category", "Category", 140),
            ("strike", "Three-strike eligibility", 260), ("status", "Review status", 145),
            ("date", "Detected (UTC)", 155), ("term", "Semester", 200),
            ("appeal", "Appeal", 100)])
        self._violation_tree.bind("<<TreeviewSelect>>", self._on_violation_select)

        self._history_empty = self._label(history, text_color=COLOR_TEXT_MUTED)
        history_actions = ctk.CTkFrame(history, fg_color="transparent")
        history_actions.pack(side="bottom", fill="x")
        self._history_tree = self._table(history, [
            ("id", "Suspension ID", 100), ("status", "Status", 145),
            ("start", "Starts (UTC)", 155), ("end", "Ends (UTC)", 155),
            ("reason", "Reason", 240), ("violation", "Related violation", 130)], height=3)
        self._history_detail = self._label(history_actions, text_color=COLOR_TEXT_MUTED)
        self._label(history_actions, "Reason for lifting / cancelling suspension (required)",
                    text_color=COLOR_TEXT_MUTED)
        self._lift_reason = ctk.CTkEntry(history_actions, placeholder_text="Reason for lifting / cancelling (required)")
        self._lift_reason.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=(4, 8))
        self._lift_btn = ctk.CTkButton(history_actions, text="Lift / Cancel Selected", width=160, height=34,
                                       fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                                       command=self._lift_selected)
        self._lift_btn.pack(side="right", pady=(4, 8))
        self._history_tree.bind("<<TreeviewSelect>>", self._on_suspension_select)

        self._message = ctk.CTkLabel(self, text="", anchor="w", justify="left", width=1,
                                     font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._message.grid(row=5, column=0, sticky="ew", pady=(5, 0))
        self._wrap_to_parent(self._message, self)

    def _set_action_state(self):
        state = "normal" if self._ready and not self._loading and self.username.strip() else "disabled"
        self._lift_reason.configure(state=state)
        selected = self._history_tree.selection()
        row = self._suspensions.get(selected[0]) if selected else None
        self._lift_btn.configure(state=state if row and suspension_state(row) in
                                 ("Active", "Scheduled") else "disabled")

    def _clear_details(self):
        self._ready = False
        self._violations = {}
        self._suspensions = {}
        self._term = {}
        self._identity.configure(text=SELECT_STUDENT)
        for label in (self._strike_summary, self._suspension_status, self._explanation,
                      self._violation_detail, self._history_detail):
            label.configure(text="")
        for tree in (self._violation_tree, self._history_tree):
            tree.delete(*tree.get_children())
        self._violation_empty.configure(text="Select a student to view violation history.")
        self._history_empty.configure(text="Select a student to view suspension history.")
        self._set_action_state()

    def _reset_form(self):
        self._lift_reason.configure(state="normal")
        self._lift_reason.delete(0, "end")
        self._set_action_state()

    def select_student(self, student_id):
        if self._action_loading:
            return
        # Student numbers remain strings; tree item IDs are student numbers too.
        sid = str(student_id).strip() if student_id is not None else None
        changed = sid != self.student_id
        self.student_id = sid
        self._clear_details()
        if changed:
            self._reset_form()
        self.refresh()

    def refresh(self):
        if self._action_loading:
            if self._poll_job is None:
                self._poll_job = self.after(40, self._poll_results)
            return
        self._generation += 1
        generation, sid = self._generation, self.student_id
        self._loading = True
        self._clear_details()
        self._message.configure(text="Loading student records…", text_color=COLOR_TEXT_MUTED)
        self._read_worker.offer(generation, lambda: self._fetch(generation, sid))
        if self._poll_job is None:
            self._poll_job = self.after(40, self._poll_results)

    def _fetch(self, generation, sid):
        try:
            self.database.process_expired_deadlines()
            data = {"students": [dict(row) for row in self.database.get_all_students()]}
            if sid:
                student = self.database.get_student_by_student_id(sid)
                if not student:
                    raise ValueError("Student no longer exists. Select another student.")
                data.update(student=student, term=self.database.get_current_academic_term(),
                            strikes=self.database.get_strike_summary(sid),
                            violations=self.database.get_discipline_history_for_student(sid),
                            suspensions=self.database.get_suspension_history(sid),
                            active=self.database.get_active_suspension(sid))
            self._publish((generation, sid, data, None))
        except Exception as exc:
            self._publish((generation, sid, None, str(exc)))

    def _publish(self, item):
        try:
            self._results.put_nowait(item)
        except queue.Full:
            latest(self._results, item)

    def _poll_results(self):
        self._poll_job = None
        while True:
            try:
                generation, sid, data, error = self._results.get_nowait()
            except queue.Empty:
                break
            if data is not None and data.get("action"):
                self._action_loading = False
            if generation != self._generation:
                if data is not None and data.get("action"):
                    self.refresh()
                continue
            self._loading = False
            if error:
                self._clear_details()
                self._message.configure(text=f"Could not load records: {error} Use Refresh to retry.",
                                        text_color=COLOR_DANGER)
                continue
            if "lifted" in data:
                if data["lifted"]:
                    self._reset_form()
                    self.refresh()
                else:
                    self._set_action_state()
                    self._message.configure(text="Suspension already ended. Use Refresh to reload.", text_color=COLOR_WARNING)
                continue
            self._students = data["students"]
            self._sync_course_options()
            self._filter_students()
            if sid != self.student_id:
                continue
            if sid:
                self._render(data)
        if self._loading:
            self._poll_job = self.after(40, self._poll_results)

    def _sync_course_options(self):
        # Use the full reloaded student dataset, including rows hidden by filters.
        courses = sorted({display_academics(s)["course"] for s in self._students} - {""},
                         key=lambda value: (value.casefold(), value))
        distinct = {}
        for course in courses:
            distinct.setdefault(course.casefold(), course)
        self._course_filter.configure(values=[ALL_COURSES, *distinct.values()])
        years = {s.get('report_year_level') or saved_year_level(s.get('year_and_section')) for s in self._students}
        self._year_filter.configure(values=list(dict.fromkeys([ALL_YEARS, *YEAR_LEVELS, *sorted(years - {None, ''})])))
        # Keep the chosen value even if its last student was removed on Refresh.
        # It then correctly matches zero rows until the user changes/clears it.

    def _on_filters_changed(self, *_):
        if not self._updating_filters:
            self._filter_students()

    def _clear_filters(self):
        self._updating_filters = True
        try:
            self._year_var.set(ALL_YEARS)
            self._course_var.set(ALL_COURSES)
            self._search_var.set("")
        finally:
            self._updating_filters = False
        self._filter_students()

    def _filter_students(self):
        query = self._search_var.get().strip().casefold()
        year_filter = self._year_var.get()
        course_filter = self._course_var.get()
        tree = self._student_tree
        tree.delete(*tree.get_children())
        for student in self._students:
            sid = student["student_id"]
            name = student.get("name") or ""
            year = student.get("report_year_level") or saved_year_level(student.get("year_and_section"))
            course = display_academics(student)["course"]
            if query and query not in sid.casefold() and query not in name.casefold():
                continue
            if year_filter != ALL_YEARS and year != year_filter:
                continue
            if course_filter != ALL_COURSES and course.casefold() != course_filter.casefold():
                continue
            tree.insert("", "end", iid=sid, values=(name, sid,
                year or student.get("year_and_section") or "—", course or "—"))
        self._result_count.configure(text=f"{len(tree.get_children())} of {len(self._students)} students")
        if self.student_id and tree.exists(self.student_id):
            tree.selection_set(self.student_id)
            tree.see(self.student_id)
        elif self.student_id:
            self.student_id = None
            self._clear_details()
            self._reset_form()
        if not tree.get_children():
            self._message.configure(text=NO_STUDENT_MATCHES, text_color=COLOR_TEXT_MUTED)
        elif not self.student_id and not self._loading:
            self._message.configure(text=SELECT_STUDENT, text_color=COLOR_TEXT_MUTED)

    def _on_student_select(self, _event=None):
        selection = self._student_tree.selection()
        if selection and selection[0] != self.student_id:
            self.select_student(selection[0])

    def _render(self, data):
        self._term = data["term"] or {}
        self._ready = True
        self._identity.configure(text=f"{data['student']['name']} · {self.student_id}")
        term = f"{self._term.get('semester_name', '—')} · {self._term.get('school_year', '—')}"
        counts = "  |  ".join(f"{s['violation_label']}: {s['active_count']}/{s['strike_limit']}"
                              for s in data["strikes"])
        self._strike_summary.configure(text=f"Current Semester Strikes · {term}\n{counts}")
        self._suspension_status.configure(text=suspension_label(data["active"]),
                                         text_color=COLOR_DANGER if data["active"] else COLOR_TEXT)
        required = any(s["action_required"] for s in data["strikes"])
        scheduled = any(suspension_state(row) == "Scheduled" for row in data["suspensions"])
        explanation = "No-uniform policy: 3 strikes = 2 days; 5 = 7 days; 7 = 14 days and staff must call parents."
        if any(s["violation_code"] == "wrong_uniform" and s["active_count"] >= 7 for s in data["strikes"]):
            explanation += " PARENT CALL REQUIRED: Contact this student's parents."
        if scheduled:
            explanation += " A suspension is scheduled; see Suspension History."
        if not data["suspensions"]:
            explanation += " No suspension has been assigned."
        self._explanation.configure(text=explanation, text_color=COLOR_WARNING if required else COLOR_TEXT_MUTED)
        self._violations = {str(row["id"]): row for row in data["violations"]}
        for key, row in self._violations.items():
            status = row["status"].replace("_", " ").title()
            if row["status"] in ("reviewed", "unreviewed"):
                status = "Legacy " + status
            self._violation_tree.insert("", "end", iid=key, values=(row["id"],
                violation_display_name(row["violation_code"]), strike_explanation(row, self._term.get("id")),
                status, row["timestamp"], f"{row['semester_name'] or '—'} · {row['school_year'] or '—'}",
                (row["appeal_status"] or "None").title()))
        self._violation_empty.configure(text=f"{len(self._violations)} violations · Counts are per category and semester."
            if self._violations else "No violation history for this student.")
        self._suspensions = {str(row["id"]): row for row in data["suspensions"]}
        for key, row in self._suspensions.items():
            self._history_tree.insert("", "end", iid=key, values=(row["id"], suspension_state(row),
                row["starts_at"], row["ends_at"] or "Indefinite", row["reason"], row["violation_id"] or "—"))
        self._history_empty.configure(text=f"{len(self._suspensions)} assigned suspensions · Select a row for details."
            if self._suspensions else "No suspension history. Finalized no-uniform strikes automatically trigger suspensions at 3, 5 and 7 strikes.")
        self._set_action_state()
        self._message.configure(text="Records loaded. Use Refresh to check for changes.", text_color=COLOR_TEXT_MUTED)

    def _on_violation_select(self, _event=None):
        selection = self._violation_tree.selection()
        row = self._violations.get(selection[0]) if selection else None
        self._violation_detail.configure(text=(
            f"Violation #{row['id']} · {strike_explanation(row, self._term.get('id'))}\n"
            f"Review deadline: {row['review_deadline'] or '—'} UTC · "
            f"Appeal deadline: {row['appeal_deadline'] or '—'} UTC\n"
            "Review or dismiss detections in Violation Log; review appeals in Records → Appeals."
        ) if row else "")

    def _on_suspension_select(self, _event=None):
        selection = self._history_tree.selection()
        row = self._suspensions.get(selection[0]) if selection else None
        text = ""
        if row:
            text = f"{suspension_state(row)} · Assigned {row['imposed_at']} UTC by {row['imposed_by']}\n{row['reason']}"
            if row["lifted_at"]:
                text += f"\nLifted {row['lifted_at']} UTC by {row['lifted_by']}: {row['lift_reason']}"
        self._history_detail.configure(text=text)
        self._set_action_state()

    def _lift_selected(self):
        selection = self._history_tree.selection()
        row = self._suspensions.get(selection[0]) if selection else None
        if not self._ready or self._loading or not row or not self.username.strip():
            return
        reason = self._lift_reason.get()
        if not reason.strip():
            self._message.configure(text="Give a reason and administrator identity.", text_color=COLOR_DANGER)
            return
        self._generation += 1
        generation, sid = self._generation, self.student_id
        self._action_loading = True
        self._loading = True
        self._set_action_state()
        self._message.configure(text="Updating suspension…")
        def lift():
            try:
                result = self.database.lift_suspension(row["id"], lifted_by=self.username, reason=reason)
                self._publish((generation, sid, {"lifted": result, "action": True}, None))
            except Exception as exc:
                self._publish((generation, sid, {"action": True}, str(exc)))
        self._read_worker.offer(generation, lift)
        if self._poll_job is None:
            self._poll_job = self.after(40, self._poll_results)

    def on_show(self, student_id=None):
        if student_id is not None:
            self._clear_filters()
            self.select_student(student_id)
        else:
            self.refresh()

    def on_hide(self):
        self._generation += 1
        self._loading = False
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self._clear_details()

    def destroy(self):
        self._read_worker.close()
        self.on_hide()
        super().destroy()
