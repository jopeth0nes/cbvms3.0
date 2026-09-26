"""Student detail dialogs using the desktop application's existing visual system."""
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

from core.student_status import CONTACT_FIELDS, STUDENT_STATUSES
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


def open_student_details(parent, database, student_id, actor, on_saved):
    student = database.get_student_by_student_id(student_id)
    if not student:
        return
    win = _window(parent, "Student Details")
    tabs = ctk.CTkTabview(win, corner_radius=CORNER_RADIUS)
    tabs.pack(fill="both", expand=True, padx=16, pady=16)
    details, history = (tabs.add(name) for name in ("Details", "Status History"))
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

    log = ctk.CTkScrollableFrame(history, fg_color=COLOR_SURFACE)
    log.pack(fill="both", expand=True)
    messages = []
    for item in database.get_student_status_history(student_id):
        messages.append(f"{item['changed_at']} UTC · {item['changed_by']}\n"
                        f"{item['previous_status']} → {item['new_status']}\n{item['reason']}")
    for message in messages or ["No academic status changes recorded."]:
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
