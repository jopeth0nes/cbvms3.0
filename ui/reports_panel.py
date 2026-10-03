"""Shared admin and superadmin CSV reporting workspace."""
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

from core.report_csv import ROSTER, DISCIPLINE, live_rows, read_csv, write_csv, import_roster
from ui.components import (COLOR_BG, COLOR_SURFACE, COLOR_BORDER, COLOR_ACCENT,
                           COLOR_TEXT, COLOR_TEXT_MUTED, heading_font, body_small_font)


class ReportsPanel(ctk.CTkFrame):
    def __init__(self, master, *, database, **kwargs):
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database = database
        self.rows = []
        self.filtered = []
        self.kind = tk.StringVar(value='Roster')
        self.source = tk.StringVar(value='Live database')
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        header = ctk.CTkFrame(self, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        ctk.CTkLabel(header, text='Reports', font=heading_font(24)).pack(side='left')
        for title, command in [('Export CSV', self.export_csv), ('Import CSV', self.import_csv),
                               ('CSV template', self.export_template),
                               ('Refresh', self.refresh)]:
            ctk.CTkButton(header, text=title, width=100, command=command).pack(side='right', padx=4)
        tools = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=12)
        tools.grid(row=1, column=0, sticky='ew')
        tools.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkOptionMenu(tools, variable=self.kind, values=['Roster', 'Discipline'],
                          command=lambda _: self.refresh()).grid(row=0, column=0, sticky='ew', padx=10, pady=10)
        ctk.CTkOptionMenu(tools, variable=self.source, values=['Live database', 'Imported discipline snapshot'],
                          command=lambda _: self.refresh()).grid(row=0, column=1, columnspan=2, sticky='ew', padx=10, pady=10)
        self.filters = {}
        self.menus = {}
        for column, (key, title) in enumerate([('college_department', 'College / Department'),
                                               ('section', 'Section'), ('year_level', 'Year level')]):
            ctk.CTkLabel(tools, text=title, anchor='w', text_color=COLOR_TEXT_MUTED,
                         font=body_small_font()).grid(row=1, column=column, sticky='ew', padx=10)
            var = tk.StringVar(value='All')
            menu = ctk.CTkOptionMenu(tools, variable=var, values=['All'], command=lambda _: self.filter_rows())
            menu.grid(row=2, column=column, sticky='ew', padx=10, pady=(4, 10))
            self.filters[key], self.menus[key] = var, menu
        self.count = ctk.CTkLabel(self, text='', anchor='w', text_color=COLOR_TEXT_MUTED)
        self.count.grid(row=2, column=0, sticky='ew', pady=8)
        table = ctk.CTkFrame(self, fg_color=COLOR_SURFACE)
        table.grid(row=3, column=0, sticky='nsew')
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        style = ttk.Style()
        style.configure('Reports.Treeview', background=COLOR_BG, fieldbackground=COLOR_BG,
                        foreground=COLOR_TEXT, rowheight=32, font=('Segoe UI', 10))
        style.configure('Reports.Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        style.map('Reports.Treeview', background=[('selected', COLOR_ACCENT)])
        self.tree = ttk.Treeview(table, show='headings', style='Reports.Treeview')
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.message = ctk.CTkLabel(self, text='Export uses the current filters. Blank classifications appear as Unspecified.',
                                   anchor='w', text_color=COLOR_TEXT_MUTED, font=body_small_font())
        self.message.grid(row=4, column=0, sticky='ew', pady=8)

    def refresh(self):
        try:
            if self.source.get() == 'Imported discipline snapshot':
                self.kind.set('Discipline')
                with self.database.connect() as conn:
                    row = conn.execute('SELECT payload FROM report_csv_snapshot WHERE id=1').fetchone()
                self.rows = json.loads(row[0]) if row else []
            else:
                self.rows = live_rows(self.database, self.kind.get())
            for key, menu in self.menus.items():
                values = ['All', *sorted({r[key] or 'Unspecified' for r in self.rows}, key=str.casefold)]
                menu.configure(values=values)
                if self.filters[key].get() not in values:
                    self.filters[key].set('All')
            self.filter_rows()
        except Exception as exc:
            self.message.configure(text=f'Could not load reports: {exc}')

    def filter_rows(self):
        self.filtered = [r for r in self.rows if all(var.get() == 'All' or
                         (r[key] or 'Unspecified') == var.get() for key, var in self.filters.items())]
        columns = ROSTER if self.kind.get() == 'Roster' else DISCIPLINE
        self.tree.delete(*self.tree.get_children())
        self.tree.configure(columns=columns)
        for key in columns:
            self.tree.heading(key, text=key.replace('_', ' ').title())
            self.tree.column(key, width=160 if key in ('name', 'college_department', 'reason') else 120,
                             minwidth=100, stretch=False)
        for index, row in enumerate(self.filtered):
            self.tree.insert('', 'end', iid=str(index), values=[row.get(key, '') for key in columns])
        self.count.configure(text=f'{len(self.filtered)} of {len(self.rows)} records · {self.kind.get()} · {self.source.get()}')

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv',
            initialfile=f'{self.kind.get().lower()}_report.csv', filetypes=[('CSV files', '*.csv')])
        if not path:
            return
        try:
            write_csv(path, self.kind.get(), self.filtered)
            self.message.configure(text=f'Exported {len(self.filtered)} records to {path}')
        except Exception as exc:
            messagebox.showerror('Export failed', str(exc), parent=self)

    def export_template(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv',
            initialfile=f'{self.kind.get().lower()}_template.csv', filetypes=[('CSV files', '*.csv')])
        if path:
            try:
                write_csv(path, self.kind.get(), [])
                self.message.configure(text=f'Saved {self.kind.get().lower()} CSV template to {path}')
            except Exception as exc:
                messagebox.showerror('Template failed', str(exc), parent=self)

    def import_csv(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('CSV files', '*.csv')])
        if not path:
            return
        try:
            kind = self.kind.get()
            rows = read_csv(path, kind)
            if kind == 'Roster':
                existing = {r['student_id'] for r in self.database.get_all_students()}
                updates = sum(r['student_id'] in existing for r in rows)
                detail = (f'{len(rows) - updates} new students; {updates} existing students updated.\n'
                          'Imports name, course, college/department, section and year level.\n'
                          'Existing photos, face data, accounts and standing are preserved.\n'
                          'New students have no face enrollment. Blank CSV fields clear those roster fields.')
            else:
                detail = (f'{len(rows)} discipline report rows.\n'
                          'Replaces the saved imported snapshot. Strikes and suspensions are unchanged.')
            preview = '\n'.join(f"{r['student_id']} · {r['name']} · {r['college_department']} · {r['year_level']} · {r['section']}"
                                for r in rows[:5])
            if not messagebox.askyesno('Import preview', detail + '\n\nFirst rows:\n' + preview + '\n\nImport this CSV?', parent=self):
                return
            if kind == 'Roster':
                import_roster(self.database, rows)
                self.source.set('Live database')
            else:
                with self.database.connect() as conn:
                    conn.execute('INSERT INTO report_csv_snapshot(id,payload) VALUES(1,?) '
                                 'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(rows),))
                self.source.set('Imported discipline snapshot')
            for var in self.filters.values():
                var.set('All')
            self.refresh()
            self.message.configure(text=f'Imported {len(rows)} {kind.lower()} rows.')
        except Exception as exc:
            messagebox.showerror('Import failed', str(exc), parent=self)
