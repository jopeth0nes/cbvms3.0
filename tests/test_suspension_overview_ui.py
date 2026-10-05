"""Native Tk reporting navigation, layout, stale reads, errors and cleanup."""
import gc
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import customtkinter as ctk

from core.suspension_analytics import ReportFilter, load_report
from database.db_manager import CBVMSDatabase
from test_suspension_analytics import seed, NOW
from ui.components import apply_cbvms_theme
from ui.suspension_overview import SuspensionsWorkspace


class OverviewUITests(unittest.TestCase):
    def setUp(self):
        gc.collect(); apply_cbvms_theme()
        self.root=ctk.CTk(); self.root.geometry('1080x850+20+40')
        self.root.grid_columnconfigure(0,weight=1); self.root.grid_rowconfigure(0,weight=1)
        self.temp=tempfile.TemporaryDirectory()
        self.db=CBVMSDatabase(Path(self.temp.name)/'ui.db'); seed(self.db)
        self.panel=SuspensionsWorkspace(self.root,database=self.db,username='admin')
        self.panel.grid(row=0,column=0,sticky='nsew')
        self.errors=[]; self.root.report_callback_exception=lambda *args:self.errors.append(args)
        self.clock=patch('core.suspension_analytics.utc_now',return_value=NOW); self.clock.start()

    def tearDown(self):
        worker=self.panel.worker
        self.panel.destroy(); self.root.destroy(); worker.thread.join(1)
        self.clock.stop(); self.temp.cleanup()
        self.assertFalse(worker.thread.is_alive()); self.assertEqual(self.errors,[])

    def pump(self,seconds=.15):
        self.root.after(int(seconds*1000),self.root.quit); self.root.mainloop()

    def until(self,predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline: self.pump(.03)
        self.assertTrue(predicate(),'UI load timed out')
        self.pump(.1)

    def show(self):
        self.panel.on_show(); self.until(lambda:self.panel.report is not None)

    def test_default_overview_read_only_and_shortcut_opens_records(self):
        with self.db.connect() as c: before=list(c.iterdump())
        self.show()
        self.assertEqual(self.panel.nav.get(),'Overview')
        self.assertIsNone(self.panel.records)
        with self.db.connect() as c: self.assertEqual(before,list(c.iterdump()))
        self.panel.on_show('S1')
        self.until(lambda:self.panel.records is not None and self.panel.records._ready)
        self.assertEqual(self.panel.nav.get(),'Student Records')
        self.assertEqual(self.panel.records.student_id,'S1')
        self.panel.records._search_var.set('Alex')
        self.panel._navigate('Overview'); self.panel._navigate('Student Records')
        self.assertEqual(self.panel.records._search_var.get(),'Alex')
        self.assertEqual(self.panel.records.student_id,'S1')

    def test_drills_and_back_preserve_report_scope_and_missing_rows(self):
        self.show(); self.panel.period_var.set('All Data'); self.panel._changed()
        self.until(lambda:self.panel.report is not None and self.panel.report['filters'].period=='all')
        snapshot=self.panel.report
        self.panel._drill_open('recorded','course','Information Technology')
        self.assertEqual(len(self.panel.drill_tree.get_children()),4)
        self.panel.drill_tree.selection_set('0'); self.panel._open_drill_student()
        self.until(lambda:self.panel.records._ready)
        self.assertEqual(self.panel.records.student_id,'S1')
        self.panel.back.invoke()
        self.assertIs(self.panel._drill[3],snapshot)
        self.assertEqual(self.panel.drill_tree.selection(),('0',))
        self.assertEqual(len(self.panel.drill_tree.get_children()),4)
        self.panel._directory(); self.pump(); self.assertTrue(self.panel.records.winfo_ismapped())
        self.panel._navigate('Overview'); self.assertEqual(self.panel.period_var.get(),'All Data')
        from core.suspension_analytics import UNAVAILABLE
        self.panel._drill_open('recorded','course',UNAVAILABLE)
        self.panel.drill_tree.selection_set('0'); self.panel._drill_selection()
        self.assertEqual(self.panel.open_record.cget('state'),'disabled')

    def test_filters_dependent_courses_empty_invalid_dates_and_retry(self):
        self.show()
        from core.academics import COLLEGES
        self.panel.variables['college'].set(COLLEGES[0]); self.panel._changed('college')
        self.assertIn('Information Technology',self.panel.menus['course'].cget('values'))
        self.assertNotIn('Nursing',self.panel.menus['course'].cget('values'))
        self.until(lambda:self.panel.report is not None)
        self.panel.variables['year'].set('6th Year'); self.panel._changed()
        self.until(lambda:self.panel.report is not None)
        self.assertEqual(self.panel.report['counts']['recorded'],0)
        self.assertIn('No matching',self.panel.status.cget('text'))
        self.panel.period_var.set('Custom date range'); self.panel._changed()
        self.pump(.4); self.assertIn('YYYY-MM-DD',self.panel.status.cget('text'))
        self.panel.reset_filters(); self.until(lambda:self.panel.report is not None)
        with patch('ui.suspension_overview.load_report',side_effect=RuntimeError('database locked')):
            self.panel.refresh(); self.until(lambda:'database locked' in self.panel.status.cget('text'))
        self.assertIsNone(self.panel.report)
        self.panel.refresh(); self.until(lambda:self.panel.report is not None)

    def test_stale_result_hide_and_rapid_refresh_are_bounded(self):
        self.show()
        entered=threading.Event(); release=threading.Event()
        original=load_report
        def slow(*args,**kwargs):
            entered.set(); release.wait(2); return original(*args,**kwargs)
        with patch('ui.suspension_overview.load_report',side_effect=slow):
            self.panel.refresh(); self.assertTrue(entered.wait(1))
            for _ in range(30):self.panel.refresh()
            self.assertLessEqual(self.panel.worker.requests.qsize(),1)
            self.panel.on_hide(); generation=self.panel._generation
            release.set(); self.pump(.2)
            self.assertIsNone(self.panel._poll_job)
            self.assertEqual(self.panel._generation,generation)
        self.panel.on_show(); self.until(lambda:self.panel._allowed)

    def test_permission_denied_does_not_mount_student_records(self):
        self.panel.username='student'
        self.panel.on_show('S1')
        self.until(lambda:'authenticated administrator' in self.panel.status.cget('text'))
        self.panel._navigate('Student Records')
        self.assertEqual(self.panel.nav.get(),'Overview'); self.assertIsNone(self.panel.records)

    def test_layout_resize_scaling_and_chart_widgets_are_reused(self):
        self.show(); charts=self.panel._charts()
        self.panel._toggle_filters()
        for width,scale in ((1080,1),(700,1),(560,1),(900,1.25)):
            ctk.set_widget_scaling(scale); self.root.geometry(f'{width}x850'); self.pump(.3)
            self.panel._layout(force=True); self.pump(.1)
            for widget in (self.panel.period_menu,self.panel.filter_button,self.panel.nav):
                self.assertGreaterEqual(widget.winfo_rootx(),self.root.winfo_rootx())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width()+2)
            for cell in self.panel.filter_controls:
                self.assertLessEqual(cell.winfo_rootx()+cell.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width()+2)
            self.panel._navigate('Analytics'); self.panel._navigate('Overview')
            self.assertEqual(self.panel._charts(),charts)
        ctk.set_widget_scaling(1)
        for _ in range(4): self.panel._render()
        self.assertEqual(self.panel._charts(),charts)

    def test_student_history_scroll_and_lift_stay_responsive(self):
        self.show(); self.panel.select_student('S1')
        self.until(lambda:self.panel.records._ready)
        records=self.panel.records
        records._tabs.set('Suspension History'); self.pump()
        self.panel.records_canvas.yview_moveto(1); self.pump()
        self.assertTrue(records._lift_btn.winfo_ismapped())
        self.assertLess(records._lift_btn.winfo_rooty()+records._lift_btn.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())
        records._history_tree.selection_set('2'); records._on_suspension_select()
        records._lift_reason.insert(0,'Fixture administrative clearance')
        entered=threading.Event(); release=threading.Event()
        original=self.db.lift_suspension
        threads=[]
        def slow(*args,**kwargs):
            threads.append(threading.get_ident()); entered.set(); release.wait(2)
            return original(*args,**kwargs)
        with patch.object(self.db,'lift_suspension',side_effect=slow) as lift:
            records._lift_selected(); self.assertTrue(entered.wait(1))
            for _ in range(10): records.refresh()
            tick=[]; self.root.after(15,lambda:tick.append(True)); self.pump(.1)
            self.assertEqual(tick,[True]); self.assertNotEqual(threads[0],threading.get_ident())
            release.set(); self.until(lambda:records._ready and not records._loading)
            lift.assert_called_once()
        self.assertIsNotNone(next(r for r in self.db.get_suspension_history('S1') if r['id']==2)['lifted_at'])
