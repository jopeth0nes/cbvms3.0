"""Real native workspace layout, stale response, retry and decision controls."""
import gc
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import customtkinter as ctk
from database.db_manager import CBVMSDatabase
from tests.evidence_fixture import picture_evidence
from ui.appeals_panel import AppealsPanel


class AppealsWorkspaceTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.tmp=tempfile.TemporaryDirectory()
        self.db=CBVMSDatabase(Path(self.tmp.name)/'ui.db');self.db.initialize()
        self.db.insert_student('2023-00883','Fixture Student','BSIT','3A',b'',b'')
        self.ids=[]
        for _ in range(13):
            vid=self.db.log_violation('2023-00883','Fixture Student','wrong_uniform',snapshot_jpeg=picture_evidence()[2])
            self.ids.append(self.db.insert_appeal(vid,'2023-00883','Please review this explanation. '*20,evidence=picture_evidence()))
        self.root=ctk.CTk();self.root.geometry('1050x720')
        self.root.grid_columnconfigure(0,weight=1);self.root.grid_rowconfigure(0,weight=1)
        self.errors=[];self.root.report_callback_exception=lambda *args:self.errors.append(args)
        self.panel=AppealsPanel(self.root,database=self.db,username='admin')
        self.panel.grid(row=0,column=0,sticky='nsew')

    def tearDown(self):
        ctk.set_widget_scaling(1)
        self.panel.destroy()
        for job in self.root.tk.splitlist(self.root.tk.call('after','info')):
            self.root.tk.call('after','cancel',job)
        self.root.destroy();gc.collect();self.tmp.cleanup()
        for error in self.errors:
            import traceback
            traceback.print_exception(*error)
        self.assertEqual(self.errors,[])

    def until(self,predicate):
        end=time.monotonic()+5
        while not predicate() and time.monotonic()<end:
            self.root.update();time.sleep(.01)
        self.root.update()
        self.assertTrue(predicate(),self.panel.status.cget('text'))

    def open(self,aid):
        self.panel.open_case(aid)
        self.until(lambda:getattr(self.panel,'case',{}).get('id')==aid)

    def test_controls_and_evidence_fit_desktop_scaling_and_small_fallback(self):
        self.open(self.ids[0])
        for width,height,scale in [(1050,720,1),(1200,900,1.25),(900,700,1.25),(590,480,1)]:
            with self.subTest(width=width,height=height,scale=scale):
                ctk.set_widget_scaling(scale);self.root.geometry(f'{width}x{height}');self.root.update()
                for widget in (self.panel.reason,self.panel.approve,self.panel.reject,self.panel.back):
                    self.assertTrue(widget.winfo_viewable())
                    self.assertGreaterEqual(widget.winfo_rootx(),self.root.winfo_rootx())
                    self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
                    self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())
                self.assertIsNotNone(self.panel.case['images'][0])
                self.assertIsNotNone(self.panel.case['images'][1])
        self.panel._enlarge(self.panel.case['images'][1],'Supporting image')
        self.root.update()
        self.assertTrue(any(isinstance(w,ctk.CTkToplevel) for w in self.panel.winfo_children()))

    def test_back_preserves_filter_search_page_and_reason_draft(self):
        self.panel.filter.set('All');self.panel.search.insert(0,'2023-00883');self.panel.offset=10
        self.open(self.ids[0]);self.panel.reason.insert('1.0','Draft decision reason')
        self.panel.back.invoke();self.until(lambda:self.panel.status.cget('text')=='')
        self.assertEqual(self.panel.offset,10);self.assertEqual(self.panel.filter.get(),'All')
        self.assertEqual(self.panel.search.get(),'2023-00883')
        self.open(self.ids[0]);self.assertEqual(self.panel.reason.get('1.0','end-1c'),'Draft decision reason')

    def test_back_restores_inbox_scroll_position(self):
        self.root.geometry('800x480')
        self.panel.show_inbox()
        self.until(lambda:self.panel.listing is not None and self.panel.status.cget('text')=='')
        self.panel.listing._parent_canvas.yview_moveto(.4)
        self.root.update()
        position=self.panel.listing._parent_canvas.yview()[0]
        self.assertGreater(position,0)
        self.open(self.ids[0])
        self.panel.back.invoke()
        self.until(lambda:self.panel.status.cget('text')=='')
        self.assertAlmostEqual(self.panel.listing._parent_canvas.yview()[0],position,delta=.02)

    def test_competing_decision_displays_winner_and_reason_required(self):
        aid=self.ids[0];self.open(aid)
        self.panel.approve.invoke()
        self.assertIn('Enter a decision reason',self.panel.status.cget('text'))
        self.assertTrue(self.db.update_appeal_decision(aid,'rejected','Other admin decided',decided_by='admin'))
        self.panel.reason.insert('1.0','Approve this evidence')
        with patch('ui.appeals_panel.messagebox.askyesno',return_value=True):self.panel.approve.invoke()
        self.until(lambda:self.panel.case['status']=='rejected')
        self.assertEqual(self.panel.reason.get('1.0','end-1c'),'Other admin decided')
        self.assertEqual(self.panel.approve.cget('state'),'disabled')
        self.assertEqual(len(self.db.get_decision_history()),1)

    def test_stale_case_response_cannot_replace_current_case(self):
        entered,release=threading.Event(),threading.Event()
        original=self.db.get_appeal_case
        def slow(aid,**kwargs):
            if aid==self.ids[0]:entered.set();release.wait(3)
            return original(aid,**kwargs)
        with patch.object(self.db,'get_appeal_case',side_effect=slow):
            self.panel.open_case(self.ids[0]);self.assertTrue(entered.wait(1))
            self.panel.open_case(self.ids[1]);release.set()
            self.until(lambda:getattr(self.panel,'case',{}).get('id')==self.ids[1])
        self.assertEqual(self.panel.case_id,self.ids[1])

    def test_error_retry_keeps_draft_and_database_work_off_tk(self):
        self.open(self.ids[0]);self.panel.reason.insert('1.0','Keep this draft')
        with patch.object(self.db,'get_appeal_case',side_effect=RuntimeError('database busy')):
            self.panel.refresh();self.until(lambda:'database busy' in self.panel.status.cget('text'))
        original=self.db.get_appeal_case;ui_thread=threading.get_ident()
        def checked(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),ui_thread)
            return original(*args,**kwargs)
        with patch.object(self.db,'get_appeal_case',side_effect=checked):
            self.panel.refresh();self.until(lambda:hasattr(self.panel,'reason') and self.panel.reason.winfo_exists())
        self.assertEqual(self.panel.reason.get('1.0','end-1c'),'Keep this draft')

if __name__=='__main__':unittest.main()
