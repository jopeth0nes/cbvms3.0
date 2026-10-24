"""Isolated native Reports checks; requires a GUI session (never opens campus data)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import customtkinter as ctk

from database.db_manager import CBVMSDatabase
from ui.components import apply_cbvms_theme
from ui.reports_panel import ReportsPanel


class ReportsNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CBVMSDatabase(Path(self.temp.name) / 'native-reports.db')
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('REPORT-TEST', 'Synthetic Student', 'BSIT', '2A', b'', b'')
        with self.db.connect() as conn:
            for index in range(205):
                conn.execute('''INSERT INTO violations(student_id,student_name,violation_type,timestamp,status)
                    VALUES(?,?,?,?,?)''', ('REPORT-TEST', 'Synthetic Student', 'wrong_uniform',
                                           '2026-10-24T00:00:00+00:00', 'pending_review'))
        apply_cbvms_theme()
        self.root = ctk.CTk()
        self.root.geometry('1280x800')
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        ctk.CTkFrame(self.root, width=220).grid(row=0, column=0, rowspan=2, sticky='ns')
        ctk.CTkLabel(self.root, text='Reports', height=60).grid(row=0, column=1)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.panel = ReportsPanel(self.root, database=self.db)
        self.panel.grid(row=1, column=1, sticky='nsew', padx=16, pady=12)
        self.pump_until(lambda: self.panel.snapshot is not None and self.panel.pending is None)

    def tearDown(self):
        self.panel.destroy()
        for job in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.tk.call('after', 'cancel', job)
        self.root.destroy()
        ctk.set_widget_scaling(1)
        self.assertEqual(self.errors, [])

    def pump_until(self, condition, timeout=8):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self.root.update()
            if condition():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for report operation')

    def select_sections(self, *names):
        for name, variable in self.panel.sections.items():
            variable.set(name in names)
        self.panel._update_filter_availability()
        self.panel.apply_filters()
        self.pump_until(lambda: self.panel.pending is None)

    def test_defaults_paging_sort_and_export_keep_applied_snapshot(self):
        self.assertEqual(self.panel.snapshot.selected_sections, ('Student Directory',))
        self.select_sections('Violations', 'Suspensions')
        self.panel.tabs.select(0)
        self.root.update()
        self.panel.show_page(1)
        self.assertEqual(len(self.panel.tables['Violations'].get_children()), 5)
        self.panel.search.set('Unapplied unmatched draft')
        self.panel.sort_by('record_id')
        self.root.update()
        self.assertEqual(self.panel.snapshot.sections[0].record_count, 205)
        self.assertTrue(self.panel.page_label.cget('text').startswith('Violations:'))
        target = Path(self.temp.name) / 'reports.zip'
        with patch('ui.reports_panel.filedialog.asksaveasfilename', return_value=str(target)):
            self.panel.export_csv()
        self.pump_until(lambda: not self.panel.output_busy)
        import csv
        import io
        import zipfile
        with zipfile.ZipFile(target) as archive:
            rows = list(csv.DictReader(io.StringIO(archive.read('violations.csv').decode('utf-8-sig'))))
        self.assertEqual([row['record_id'] for row in rows],
                         [str(row['record_id']) for row in self.panel.snapshot.sections[0].rows])

    def test_latest_request_wins_and_clear_is_empty(self):
        self.select_sections('Violations')
        self.panel.search.set('unmatched')
        self.panel.apply_filters()
        self.panel.search.set('REPORT-TEST')
        self.panel.apply_filters()
        self.pump_until(lambda: self.panel.pending is None)
        self.assertEqual(self.panel.snapshot.sections[0].record_count, 205)
        self.panel.checklists['departments'].set([])
        self.panel.apply_filters()
        self.pump_until(lambda: self.panel.pending is None)
        self.assertEqual(self.panel.snapshot.sections[0].record_count, 0)
        self.panel.reset_filters()
        self.pump_until(lambda: self.panel.pending is None)
        self.assertEqual(self.panel.snapshot.selected_sections, ('Student Directory',))
        self.assertEqual(self.panel.snapshot.sections[0].record_count, 1)

    def test_actions_fit_supported_window_and_scaling(self):
        for scaling in (1, 1.25):
            ctk.set_widget_scaling(scaling)
            self.root.update()
            for button in (self.panel.export_button, self.panel.print_button):
                self.assertGreater(button.winfo_width(), 80)
                self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),
                                     self.root.winfo_rootx()+self.root.winfo_width())
            self.assertGreater(self.panel.tabs.winfo_height(), 100)

    def test_checklist_popup_toggling_search_and_explicit_empty(self):
        control=self.panel.checklists['departments']
        control.set_options(['Alpha', 'Beta'])
        control.open()
        self.root.update()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        checks=[w for w in descendants(control._popup) if isinstance(w,ctk.CTkCheckBox)]
        self.assertEqual(len(checks),2)
        checks[0]._canvas.focus_force()
        checks[0]._canvas.event_generate('<space>')
        self.root.update()
        buttons={w.cget('text'):w for w in descendants(control._popup) if isinstance(w,ctk.CTkButton)}
        buttons['Apply'].invoke()
        self.assertEqual(control.get(),['Beta'])
        control.set_options([])
        control.open();self.root.update()
        buttons={w.cget('text'):w for w in descendants(control._popup) if isinstance(w,ctk.CTkButton)}
        buttons['Clear'].invoke();buttons['Apply'].invoke()
        self.assertEqual(control.get(),[])


if __name__ == '__main__':
    unittest.main()
