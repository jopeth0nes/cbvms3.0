"""Native attendance views: async queries, stale responses, filters, exports and roles."""
import csv
import gc
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

import customtkinter as ctk
from database.db_manager import CBVMSDatabase
from core.academics import COLLEGES, courses_for
from ui.attendance_panel import AttendancePanel
from ui.records_panel import RecordsPanel
from ui.dashboard import CBVMSDashboard
from tests.test_live_dashboard import dashboard


class AttendanceUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=CBVMSDatabase(Path(self.tmp.name)/'ui.db',timeout=.2)
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('0001-A','One','BSIT','1A',b'',b'')
        self.db.set_attendance_cooldown(0,actor='admin')
        for i in range(61):
            self.db.record_attendance('0001-A',observed_at=datetime(2026,10,4,tzinfo=timezone.utc)+timedelta(seconds=i))
        self.root=ctk.CTk()
        self.root.geometry('700x820')
        self.errors=[]
        self.root.report_callback_exception=lambda *args:self.errors.append(args)
        self.root.columnconfigure(0,weight=1);self.root.rowconfigure(0,weight=1)
        self.panel=AttendancePanel(self.root,database=self.db,username='admin')
        self.panel.grid(row=0,column=0,sticky='nsew')

    def tearDown(self):
        for job in self.root.tk.splitlist(self.root.tk.call('after','info')):
            self.root.tk.call('after','cancel',job)
        self.root.destroy()
        # Retire Tk widget/font cycles on the owning thread before later worker tests.
        self.panel=None
        self.root=None
        gc.collect()
        self.tmp.cleanup()
        self.assertEqual(self.errors,[])

    def until(self,predicate):
        end=time.monotonic()+6
        while not predicate() and time.monotonic()<end:
            self.root.update();time.sleep(.01)
        self.assertTrue(predicate())

    def test_pagination_dependent_filters_exports_and_error_retry(self):
        panel=self.panel
        panel.view.set('Sighting Events');panel.page_size.set('25');panel.clear_filters()
        self.until(lambda:not panel.task.busy)
        self.assertEqual(panel.total,61)
        for width in (700,1100):
            self.root.geometry(f'{width}x820');self.root.update()
            self.assertGreater(panel.tree.winfo_height(),50)
            for widget in panel.winfo_children():
                self.assertLessEqual(widget.winfo_x()+widget.winfo_width(),panel.winfo_width()+2)
        self.assertEqual(len(panel.rows),25)
        first=set(panel.tree.get_children())
        panel.change_page(1);self.until(lambda:not panel.task.busy)
        self.assertFalse(first.intersection(panel.tree.get_children()))
        panel.tree.selection_set(panel.tree.get_children()[:2])
        for selected,expected in ((True,2),(False,61)):
            target=Path(self.tmp.name)/f'export-{selected}.csv'
            with patch('ui.attendance_panel.filedialog.asksaveasfilename',return_value=str(target)):
                panel.export(selected)
            self.until(lambda:not panel.export_task.busy)
            with target.open(encoding='utf-8-sig') as stream:
                self.assertEqual(len(list(csv.reader(stream)))-2,expected)
        for college in COLLEGES:
            panel.filters['college_department'].set(college);panel._college_changed(college)
            self.assertEqual(panel.menus['course'].cget('values'),['All',*courses_for(college)])
            panel.filters['course'].set(courses_for(college)[0])
        panel._college_changed(COLLEGES[0])
        self.assertEqual(panel.filters['course'].get(),'All')
        with patch.object(self.db,'query_attendance',side_effect=RuntimeError('locked fixture')):
            panel.clear_filters();self.until(lambda:not panel.task.busy)
        self.assertIn('locked fixture',panel.message.cget('text'))
        panel.refresh();self.until(lambda:not panel.task.busy)
        self.assertEqual(panel.total,61)
        panel.filters['student_id'].set('missing');panel.refresh();self.until(lambda:not panel.task.busy)
        self.assertIn('No matching',panel.message.cget('text'))
        panel.filters['start'].set('bad date');panel.refresh()
        self.assertIn('YYYY-MM-DD',panel.message.cget('text'))

    def test_queries_off_tk_thread_latest_filter_wins_and_heartbeat_runs(self):
        panel=self.panel
        entered,release=threading.Event(),threading.Event()
        original=self.db.query_attendance
        main=threading.get_ident()
        seen=[]
        def delayed(**kwargs):
            self.assertNotEqual(threading.get_ident(),main)
            seen.append(kwargs['filters'])
            if len(seen)==1:
                entered.set();release.wait(3)
            return original(**kwargs)
        with patch.object(self.db,'query_attendance',side_effect=delayed):
            panel.clear_filters();self.until(entered.is_set)
            panel.filters['student_id'].set('missing');panel.refresh()
            beats=[];self.root.after(1,lambda:beats.append(1));self.until(lambda:beats)
            release.set();self.until(lambda:len(seen)==2 and not panel.task.busy)
        self.assertEqual(panel.total,0)
        self.assertEqual(panel.applied[1]['student_id'],'missing')

    def test_both_staff_roles_records_share_queries_without_discipline_processing(self):
        self.panel.destroy()
        for role in ('admin','superadmin'):
            panel=RecordsPanel(self.root,database=self.db,username=role)
            panel.grid(row=0,column=0,sticky='nsew')
            with patch.object(self.db,'process_expired_deadlines') as discipline:
                panel._switch_tab('attendance')
                panel._attendance_frame.clear_filters()
                self.until(lambda:not panel._attendance_frame.task.busy)
                discipline.assert_not_called()
            self.assertEqual(panel._attendance_frame.total,1)
            panel._attendance_frame.cooldown.set('120');panel._attendance_frame.save_cooldown()
            self.until(lambda:not panel._attendance_frame.settings_task.busy)
            self.assertEqual(self.db.get_attendance_cooldown(),120)
            panel.destroy()
            # Exercise the actual dashboard navigation permission gate for each staff role.
            shell=dashboard();shell._is_superadmin=role=='superadmin'
            shell._views={'live':MagicMock(),'attendance':MagicMock()}
            shell._attendance_panel=MagicMock();shell._nav_buttons={};shell._center_title=MagicMock()
            shell._view_host=MagicMock();shell._fade_transition=lambda callback:callback()
            CBVMSDashboard._on_nav_select(shell,'attendance')
            shell._attendance_panel.refresh.assert_called_once()
            self.assertEqual(shell._active_nav,'attendance')


if __name__=='__main__':unittest.main()
