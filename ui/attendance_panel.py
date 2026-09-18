"""Daily attendance captured by the live monitor, with downloadable reports."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import date
from pathlib import Path

import customtkinter as ctk
from core.reports import (ATTENDANCE_HEADERS, attendance_values, write_csv,
                          report_html, open_print_preview)
from ui.components import COLOR_BG, COLOR_TEXT_MUTED, body_small_font


class AttendancePanel(ctk.CTkFrame):
    def __init__(self, master, *, database):
        super().__init__(master, fg_color="transparent")
        self.database = database
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        ctk.CTkLabel(self, text="Attendance is recorded when the live monitor recognizes an enrolled student. "
            "One Present record per day; first and last seen are camera sightings, not check-in/out.",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED, wraplength=950,
            anchor="w", justify="left").grid(row=0, column=0, sticky="ew", pady=(0, 8))
        filters = ctk.CTkFrame(self, fg_color="transparent")
        filters.grid(row=1, column=0, sticky="ew")
        self.start = tk.StringVar(value=date.today().isoformat())
        self.end = tk.StringVar(value=date.today().isoformat())
        self.search = tk.StringVar()
        for label, variable, width in (("From (YYYY-MM-DD)", self.start, 140),
                                        ("To (YYYY-MM-DD)", self.end, 140),
                                        ("Student / course", self.search, 220)):
            ctk.CTkLabel(filters, text=label).pack(side="left", padx=(0, 5))
            ctk.CTkEntry(filters, textvariable=variable, width=width).pack(side="left", padx=(0, 10))
        ctk.CTkButton(filters, text="Apply", width=75, command=self.refresh).pack(side="left")
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", pady=8)
        for text, action in (("All Dates", self._all_dates), ("Download CSV", lambda: self._export("csv")),
                             ("Download Report", lambda: self._export("html")),
                             ("Print Report", lambda: self._export("print"))):
            ctk.CTkButton(actions, text=text, width=140, command=action).pack(side="left", padx=(0, 8))
        self.summary = ctk.CTkLabel(actions, text="")
        self.summary.pack(side="right")
        table = ctk.CTkFrame(self, fg_color=COLOR_BG)
        table.grid(row=3, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=ATTENDANCE_HEADERS, show="headings", style="Rec.Treeview")
        for column in ATTENDANCE_HEADERS:
            self.tree.heading(column, text=column)
            self.tree.column(column, width=150 if "Seen" in column else 110, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        for orient in ("vertical", "horizontal"):
            scroll = ttk.Scrollbar(table, orient=orient,
                                  command=self.tree.yview if orient == "vertical" else self.tree.xview)
            scroll.grid(row=0 if orient == "vertical" else 1,
                        column=1 if orient == "vertical" else 0,
                        sticky="ns" if orient == "vertical" else "ew")
            self.tree.configure(**{"yscrollcommand" if orient == "vertical" else "xscrollcommand": scroll.set})

    def _all_dates(self):
        self.start.set("")
        self.end.set("")
        self.refresh()

    def _rows(self):
        start, end = self.start.get().strip(), self.end.get().strip()
        for value in (start, end):
            if value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError("Enter dates as YYYY-MM-DD, or leave them blank for all dates.")
        if start and end and start > end:
            raise ValueError("From date must be on or before To date.")
        return attendance_values(self.database.get_attendance_report(start, end, self.search.get()))

    def refresh(self):
        try:
            rows = self._rows()
        except Exception as exc:
            messagebox.showerror("Attendance report", str(exc), parent=self)
            return
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=row)
        self.summary.configure(text=f"{len(rows)} attendance records")

    def _export(self, kind):
        try:
            rows = self._rows()
            description = (f"Dates: {self.start.get().strip() or 'All'} to {self.end.get().strip() or 'All'} · "
                           f"Search: {self.search.get().strip() or 'All'} · Camera-observed presence only")
            if kind == "print":
                open_print_preview("Attendance Report", ATTENDANCE_HEADERS, rows, description)
                return
            path = filedialog.asksaveasfilename(parent=self, title="Download Attendance Report",
                defaultextension="." + kind, initialfile="attendance_report." + kind,
                filetypes=[("CSV" if kind == "csv" else "Printable HTML report", "*." + kind)])
            if not path:
                return
            if kind == "csv":
                write_csv(path, ATTENDANCE_HEADERS, rows)
            else:
                Path(path).write_text(report_html("Attendance Report", ATTENDANCE_HEADERS, rows,
                                                 description), encoding="utf-8")
            messagebox.showinfo("Report saved", "Attendance report saved successfully.", parent=self)
        except Exception as exc:
            messagebox.showerror("Report failed", str(exc), parent=self)
