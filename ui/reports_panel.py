"""Shared admin and superadmin CSV reporting workspace."""
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

from ui.background_task import BackgroundTask
from core.academics import COLLEGES, courses_for
from core.report_csv import ROSTER, DISCIPLINE, live_rows, read_csv, write_csv, import_roster
from ui.components import (COLOR_BG, COLOR_SURFACE, COLOR_BORDER, COLOR_ACCENT,
                           COLOR_TEXT, COLOR_TEXT_MUTED, heading_font, body_small_font)


class ReportsPanel(ctk.CTkFrame):
    def __init__(self, master, *, database, **kwargs):
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database = database
        self.task = BackgroundTask(self)
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
        tools.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkOptionMenu(tools, variable=self.kind, values=['Roster', 'Discipline'],
                          command=lambda _: self.refresh()).grid(row=0, column=0, sticky='ew', padx=10, pady=10)
        ctk.CTkOptionMenu(tools, variable=self.source, values=['Live database', 'Imported discipline snapshot'],
                          command=lambda _: self.refresh()).grid(row=0, column=1, columnspan=2, sticky='ew', padx=10, pady=10)
        self.filters = {}
        self.menus = {}
        for column, (key, title) in enumerate([('college_department', 'College / Department'),
                                               ('course', 'Course'), ('section', 'Section'), ('year_level', 'Year level')]):
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
        source = self.source.get()
        if source == 'Imported discipline snapshot':
            self.kind.set('Discipline')
        kind = self.kind.get()
        def read():
            if source == 'Imported discipline snapshot':
                with self.database.connect() as conn:
                    row = conn.execute('SELECT payload FROM report_csv_snapshot WHERE id=1').fetchone()
                return json.loads(row[0]) if row else []
            return live_rows(self.database, kind)
        def loaded(rows):
            if (source, kind) != (self.source.get(), self.kind.get()):
                self.refresh()
                return
            self.rows = rows
            for key, menu in self.menus.items():
                values = ['All', *sorted({r[key] or 'Unspecified' for r in rows}, key=str.casefold)]
                if key == 'college_department':
                    values = list(dict.fromkeys(['All', *COLLEGES, *values[1:]]))
                menu.configure(values=values)
                if self.filters[key].get() not in values:
                    self.filters[key].set('All')
            self.filter_rows()
            self.message.configure(text='Reports loaded. Unresolved classifications need review in Student Details.')
        self.task.run(read, loaded, lambda error: self.message.configure(text=f'Could not load reports: {error}'))

    def filter_rows(self):
        college = self.filters['college_department'].get()
        courses = courses_for(college) if college in COLLEGES else sorted({r['course'] for r in self.rows})
        self.menus['course'].configure(values=['All', *courses])
        if self.filters['course'].get() not in courses:
            self.filters['course'].set('All')
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
        kind, rows = self.kind.get(), list(self.filtered)
        self.task.run(lambda: write_csv(path, kind, rows),
            lambda _: self.message.configure(text=f'Exported {len(rows)} records to {path}'),
            lambda error: self.message.configure(text=f'Export failed: {error}'))

    def export_template(self):
        kind = self.kind.get()
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv',
            initialfile=f'{kind.lower()}_template.csv', filetypes=[('CSV files', '*.csv')])
        if path:
            self.task.run(lambda: write_csv(path, kind, []),
                lambda _: self.message.configure(text=f'Saved {kind.lower()} CSV template to {path}'),
                lambda error: self.message.configure(text=f'Template failed: {error}'))

    def import_csv(self):
        if self.task.busy:
            self.message.configure(text='An operation is still running. Wait before importing.')
            return
        path = filedialog.askopenfilename(parent=self, filetypes=[('CSV files', '*.csv')])
        if not path:
            return
        kind = self.kind.get()
        def read():
            rows = read_csv(path, kind)
            existing = {r['student_id'] for r in self.database.get_all_students()} if kind == 'Roster' else set()
            return rows, sum(r['student_id'] in existing for r in rows)
        def preview(result):
            if kind != self.kind.get():
                self.message.configure(text='Report type changed. Import again for the selected type.')
                return
            rows, updates = result
            if kind == 'Roster':
                detail = (f'{len(rows) - updates} new students; {updates} existing students updated.\n'
                          'Imports name, college, course, section and year level.\n'
                          'Existing photos, face data, accounts and standing are preserved.\n'
                          'New students have no face enrollment. Blank sections clear saved sections.')
            else:
                detail = (f'{len(rows)} discipline report rows.\n'
                          'Replaces the saved imported snapshot. Strikes and suspensions are unchanged.')
            sample = '\n'.join(f"{r['student_id']} · {r['name']} · {r['college_department']} · {r['course']}" for r in rows[:5])
            if not messagebox.askyesno('Import preview', detail + '\n\nFirst rows:\n' + sample + '\n\nImport this CSV?', parent=self):
                return
            def commit():
                if kind == 'Roster':
                    import_roster(self.database, rows)
                else:
                    with self.database.connect() as conn:
                        conn.execute('INSERT INTO report_csv_snapshot(id,payload) VALUES(1,?) '
                            'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(rows),))
            def imported(_):
                self.kind.set(kind)
                self.source.set('Live database' if kind == 'Roster' else 'Imported discipline snapshot')
                for var in self.filters.values():
                    var.set('All')
                self.refresh()
                self.message.configure(text=f'Imported {len(rows)} {kind.lower()} rows.')
            self.task.run(commit, imported, lambda error: self.message.configure(text=f'Import failed: {error}'))
        self.task.run(read, preview, lambda error: self.message.configure(text=f'Import failed: {error}'))
