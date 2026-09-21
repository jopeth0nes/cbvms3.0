"""Student detail dialogs using the desktop application's existing visual system."""
from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

from core.discipline import parse_db_datetime, utc_now
from core.student_status import CONTACT_FIELDS, STUDENT_STATUSES, suspension_label
from ui.components import (COLOR_BG, COLOR_SURFACE, COLOR_TEXT, COLOR_TEXT_MUTED,
                           COLOR_ACCENT, COLOR_DANGER, CORNER_RADIUS, heading_font, body_font)


def _window(parent, title):
    win = ctk.CTkToplevel(parent)
    win.title(title)
    win.geometry("760x700")
    win.minsize(620, 500)
    win.configure(fg_color=COLOR_BG)
    win.transient(parent.winfo_toplevel())
    win.after(100, win.lift)
    return win


def _field(parent, label, value=""):
    ctk.CTkLabel(parent, text=label, font=body_font(12), text_color=COLOR_TEXT_MUTED,
                 anchor="w").pack(fill="x", pady=(8, 2))
    entry = ctk.CTkEntry(parent, height=34)
    entry.insert(0, value or "")
    entry.pack(fill="x")
    return entry


def open_student_details(parent, database, student_id, actor, on_saved, selected_tab="Details"):
    student = database.get_student_by_student_id(student_id)
    if not student:
        return
    win = _window(parent, "Student Details")
    tabs = ctk.CTkTabview(win, corner_radius=CORNER_RADIUS)
    tabs.pack(fill="both", expand=True, padx=16, pady=16)
    details, sanctions, history = (tabs.add(name) for name in ("Details", "Suspension", "History"))
    tabs.set(selected_tab)
    form = ctk.CTkScrollableFrame(details, fg_color=COLOR_SURFACE)
    form.pack(fill="both", expand=True)
    ctk.CTkLabel(form, text=f"{student['name']} · {student_id}", font=heading_font(16),
                 text_color=COLOR_TEXT).pack(anchor="w", pady=8)
    ctk.CTkLabel(form, text="Academic status", text_color=COLOR_TEXT_MUTED).pack(anchor="w")
    status = ctk.CTkOptionMenu(form, values=list(STUDENT_STATUSES))
    status.set(student["student_status"])
    status.pack(fill="x", pady=4)
    verify = tk.BooleanVar(value=False)
    if student.get("registration_pending"):
        ctk.CTkCheckBox(form, text="OSA verified current enrollment", variable=verify).pack(anchor="w", pady=8)
    reason = _field(form, "Reason for status change / verification")
    entries = {key: _field(form, label, student.get(key)) for label, key in CONTACT_FIELDS}
    error = ctk.CTkLabel(details, text="", text_color=COLOR_DANGER, wraplength=650)
    error.pack(fill="x")

    def save():
        try:
            database.update_student_details(student_id, student_status=status.get(),
                contacts={key: entry.get() for key, entry in entries.items()}, changed_by=actor,
                reason=reason.get(), verify_registration=verify.get())
        except ValueError as exc:
            error.configure(text=str(exc))
            return
        on_saved()
        win.destroy()
    ctk.CTkButton(details, text="Save Details", fg_color=COLOR_ACCENT,
                  corner_radius=CORNER_RADIUS, command=save).pack(fill="x", pady=8)

    panel = ctk.CTkScrollableFrame(sanctions, fg_color=COLOR_SURFACE)
    panel.pack(fill="both", expand=True)
    active = database.get_active_suspension(student_id)
    active_label = ctk.CTkLabel(panel, text=suspension_label(active), font=heading_font(15),
                 text_color=COLOR_DANGER if active else COLOR_TEXT)
    active_label.pack(anchor="w", pady=8)
    def refresh_active_tag():
        current = database.get_active_suspension(student_id)
        active_label.configure(text=suspension_label(current), text_color=COLOR_DANGER if current else COLOR_TEXT)
        win.after(5000, refresh_active_tag)
    win.after(5000, refresh_active_tag)
    ctk.CTkLabel(panel, text="Dates use this computer's local time (YYYY-MM-DD HH:MM).\n"
                 "A one-day suspension lasts 24 hours. Suspensions do not alter academic status.",
                 justify="left", wraplength=630, text_color=COLOR_TEXT_MUTED).pack(anchor="w")
    start = _field(panel, "Starts", datetime.now().strftime("%Y-%m-%d %H:%M"))
    end = _field(panel, "Ends", (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
    indefinite = tk.BooleanVar(value=False)
    ctk.CTkCheckBox(panel, text="Indefinite — OSA must lift it", variable=indefinite,
        command=lambda: end.configure(state="disabled" if indefinite.get() else "normal")).pack(anchor="w", pady=10)
    suspension_reason = _field(panel, "Suspension reason (required)")
    violation = _field(panel, "Related violation ID (optional)")
    result = ctk.CTkLabel(panel, text="", text_color=COLOR_DANGER, wraplength=620)
    result.pack(fill="x", pady=6)

    def refresh():
        selected = tabs.get()
        on_saved()
        win.destroy()
        open_student_details(parent, database, student_id, actor, on_saved, selected)

    def impose():
        try:
            start_dt = datetime.strptime(start.get().strip(), "%Y-%m-%d %H:%M").astimezone(timezone.utc)
            end_dt = None if indefinite.get() else datetime.strptime(end.get().strip(), "%Y-%m-%d %H:%M").astimezone(timezone.utc)
            database.impose_suspension(student_id, reason=suspension_reason.get(), starts_at=start_dt,
                ends_at=end_dt, imposed_by=actor, violation_id=int(violation.get()) if violation.get().strip() else None)
        except ValueError as exc:
            result.configure(text=str(exc))
            return
        refresh()
    ctk.CTkButton(panel, text="Assign Suspension", command=impose,
                  fg_color=COLOR_ACCENT, corner_radius=CORNER_RADIUS).pack(fill="x", pady=8)
    lift_reason = _field(panel, "Reason for lifting / cancelling suspension")
    for item in database.get_suspension_history(student_id):
        if item["lifted_at"] or (item["ends_at"] and parse_db_datetime(item["ends_at"]) <= utc_now()):
            continue
        def lift(sid=item["id"]):
            try:
                if not database.lift_suspension(sid, lifted_by=actor, reason=lift_reason.get()):
                    result.configure(text="Suspension already ended. Reopen to refresh.")
                    return
            except ValueError as exc:
                result.configure(text=str(exc))
                return
            refresh()
        ctk.CTkButton(panel, text=f"Lift / Cancel #{item['id']} · {item['starts_at']} UTC",
                      command=lift, fg_color=COLOR_DANGER).pack(fill="x", pady=4)

    log = ctk.CTkScrollableFrame(history, fg_color=COLOR_SURFACE)
    log.pack(fill="both", expand=True)
    messages = []
    for item in database.get_student_status_history(student_id):
        messages.append(f"{item['changed_at']} UTC · {item['changed_by']}\n"
                        f"{item['previous_status']} → {item['new_status']}\n{item['reason']}")
    for item in database.get_suspension_history(student_id):
        state = "Lifted" if item["lifted_at"] else (
            "Expired" if item["ends_at"] and parse_db_datetime(item["ends_at"]) <= utc_now()
            else "Scheduled" if parse_db_datetime(item["starts_at"]) > utc_now() else "Active")
        messages.append(f"Suspension #{item['id']} · {state} · assigned by {item['imposed_by']}\n"
            f"{item['starts_at']} to {item['ends_at'] or 'indefinite'} UTC\n{item['reason']}\n"
            f"Related violation: {item['violation_id'] or '—'}"
            + (f"\nLifted {item['lifted_at']} UTC by {item['lifted_by']}: {item['lift_reason']}" if item['lifted_at'] else ""))
    for message in messages or ["No status or suspension changes recorded."]:
        ctk.CTkLabel(log, text=message, justify="left", anchor="w", wraplength=620,
                     text_color=COLOR_TEXT).pack(fill="x", padx=10, pady=10)


def open_premises_log(parent, database):
    win = _window(parent, "Premises Entry Log")
    ctk.CTkLabel(win, text="Graduate and Unenrolled camera sightings · Times are UTC\n"
        "Latest 1,000 entries. Sightings less than five minutes apart are grouped; they do not verify a gate crossing.",
        text_color=COLOR_TEXT_MUTED, wraplength=690).pack(fill="x", padx=16, pady=12)
    filters = ctk.CTkFrame(win, fg_color="transparent")
    filters.pack(fill="x", padx=16, pady=8)
    search = ctk.CTkEntry(filters, placeholder_text="Name or student ID")
    search.pack(side="left", fill="x", expand=True, padx=(0, 8))
    status = ctk.CTkOptionMenu(filters, values=["All", "Graduate", "Unenrolled"])
    status.pack(side="left", padx=(0, 8))
    table = ctk.CTkFrame(win, fg_color=COLOR_SURFACE)
    table.pack(fill="both", expand=True, padx=16, pady=(0, 16))
    table.rowconfigure(0, weight=1)
    table.columnconfigure(0, weight=1)
    columns = ("Name", "Student ID", "Status at entry", "Entered (UTC)", "Last seen (UTC)", "Suspended at entry")
    tree = ttk.Treeview(table, columns=columns, show="headings", style="CBVMS.Treeview")
    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, width=150, minwidth=110)
    tree.grid(row=0, column=0, sticky="nsew")
    for orientation in ("vertical", "horizontal"):
        vertical = orientation == "vertical"
        bar = ttk.Scrollbar(table, orient=orientation, command=tree.yview if vertical else tree.xview)
        bar.grid(row=0 if vertical else 1, column=1 if vertical else 0, sticky="ns" if vertical else "ew")
        tree.configure(**{"yscrollcommand" if vertical else "xscrollcommand": bar.set})
    def reload():
        tree.delete(*tree.get_children())
        for item in database.get_premises_entries(search.get(), status.get()):
            tree.insert("", "end", values=(item["student_name"], item["student_id"], item["student_status"],
                item["entered_at"], item["last_seen"], f"Yes (#{item['suspension_id']})" if item['suspension_id'] else "No"))
    ctk.CTkButton(filters, text="Refresh", width=90, command=reload).pack(side="left")
    reload()
