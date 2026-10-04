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
from core.appeal_categories import BY_CODE, BY_OPTION, CATEGORIES, SELECT_CATEGORY, LEGACY_CATEGORY


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
                for widget in (self.panel.category,self.panel.reason,self.panel.approve,self.panel.reject,self.panel.back):
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
        self.panel.category.set(BY_CODE['approval.valid_exemption'].option)
        self.panel.back.invoke();self.until(lambda:self.panel.status.cget('text')=='')
        self.assertEqual(self.panel.offset,10);self.assertEqual(self.panel.filter.get(),'All')
        self.assertEqual(self.panel.search.get(),'2023-00883')
        self.open(self.ids[0]);self.assertEqual(self.panel.reason.get('1.0','end-1c'),'Draft decision reason')
        self.assertEqual(self.panel.category.get(),BY_CODE['approval.valid_exemption'].option)
        self.panel.refresh();self.until(lambda:self.panel.case.get('id')==self.ids[0])
        self.assertEqual(self.panel.category.get(),BY_CODE['approval.valid_exemption'].option)

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

    def test_history_available_to_both_admin_roles_and_survives_restart(self):
        aid = self.ids[0]
        self.assertTrue(self.db.update_appeal_decision(
            aid, 'approved', 'Uniform evidence verified', decided_by='admin', decision_category_code='approval.detection_error'))
        reopened = CBVMSDatabase(self.db.db_path)
        reopened.initialize(process_deadlines=False)
        for username in ('admin', 'superadmin'):
            history = reopened.get_appeal_inbox(username=username, status='history',
                                                search='2023-00883')
            self.assertEqual(history['total'], 1)
            row = history['rows'][0]
            self.assertEqual(row['id'], aid)
            self.assertEqual(row['decided_by'], 'admin')
            self.assertEqual(row['admin_notes'], 'Uniform evidence verified')
            self.assertTrue(row['decided_at'])
        self.panel.show_history()
        self.until(lambda: self.panel.listing is not None and self.panel.status.cget('text') == '')
        self.assertEqual(self.panel.heading.cget('text'), 'Appeal History')
        with self.assertRaises(PermissionError):
            reopened.get_appeal_inbox(username='student', status='history')

    def test_competing_decision_displays_winner_and_reason_required(self):
        aid=self.ids[0];self.open(aid)
        self.panel.category.set(BY_CODE['approval.detection_error'].option)
        self.panel.approve.invoke()
        self.assertIn('Enter a decision reason',self.panel.status.cget('text'))
        self.assertTrue(self.db.update_appeal_decision(aid,'rejected','Other admin decided',decided_by='admin', decision_category_code='rejection.violation_confirmed'))
        self.panel.reason.insert('1.0','Approve this evidence')
        with patch('ui.appeals_panel.messagebox.askyesno',return_value=True):self.panel.approve.invoke()
        self.until(lambda:self.panel.case.get('status')=='rejected')
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
        self.panel.category.set(BY_CODE['approval.exceptional_circumstance'].option)
        with patch.object(self.db,'get_appeal_case',side_effect=RuntimeError('database busy')):
            self.panel.refresh();self.until(lambda:'database busy' in self.panel.status.cget('text'))
        original=self.db.get_appeal_case;ui_thread=threading.get_ident()
        def checked(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),ui_thread)
            return original(*args,**kwargs)
        with patch.object(self.db,'get_appeal_case',side_effect=checked):
            self.panel.refresh();self.until(lambda:hasattr(self.panel,'reason') and self.panel.reason.winfo_exists())
        self.assertEqual(self.panel.reason.get('1.0','end-1c'),'Keep this draft')
        self.assertEqual(self.panel.category.get(),BY_CODE['approval.exceptional_circumstance'].option)

    def test_category_required_all_action_pairings_and_selection_never_submits(self):
        self.open(self.ids[0])
        self.assertEqual(self.panel.category.get(),SELECT_CATEGORY)
        self.panel.reason.insert('1.0','Written explanation')
        with patch.object(self.db,'update_appeal_decision') as save, patch('ui.appeals_panel.messagebox.askyesno',return_value=False) as confirm:
            self.panel._decide('approved')
            self.assertIn('Select a decision category',self.panel.status.cget('text'))
            confirm.assert_not_called()
            for category in CATEGORIES:
                self.panel.category.set(category.option)
                self.panel.category._command(category.option)
                save.assert_not_called();confirm.reset_mock()
                wrong='rejected' if category.decision=='approved' else 'approved'
                self.panel._decide(wrong)
                self.assertIn('must match',self.panel.status.cget('text'));confirm.assert_not_called()
                self.panel._decide(category.decision)
                confirm.assert_called_once()
            save.assert_not_called()
        self.assertEqual(self.db.get_appeal_case(self.ids[0],username='admin')['status'],'pending')

    def test_double_click_single_transaction_and_completed_case_read_only(self):
        aid=self.ids[0];self.open(aid)
        self.panel.category.set(BY_CODE['rejection.other'].option)
        self.panel.reason.insert('1.0','Reviewed the particular incident.')
        with patch.object(self.db,'update_appeal_decision',wraps=self.db.update_appeal_decision) as save, patch('ui.appeals_panel.messagebox.askyesno',return_value=True):
            self.panel._decide('rejected');self.panel._decide('rejected')
            self.until(lambda:self.panel.case.get('status')=='rejected')
            save.assert_called_once()
            self.panel._decide('approved');save.assert_called_once()
        self.assertEqual(self.panel.category.get(),'Other rejection reason')
        for widget in (self.panel.category,self.panel.reason,self.panel.approve,self.panel.reject):
            self.assertEqual((widget._textbox if isinstance(widget,ctk.CTkTextbox) else widget).cget('state'),'disabled')
        self.assertEqual(len(self.db.get_decision_history_for_appeal(aid)),1)

    def test_historical_case_displays_explicit_legacy_category_read_only(self):
        aid=self.ids[0]
        with self.db.connect() as conn:
            conn.execute("UPDATE appeals SET status='approved',admin_notes='Old decision' WHERE id=?",(aid,))
        self.open(aid)
        self.assertEqual(self.panel.category.get(),LEGACY_CATEGORY)
        self.assertEqual(self.panel.reason.get('1.0','end-1c'),'Old decision')
        self.assertEqual(self.panel.category.cget('state'),'disabled')

    def test_reports_history_displays_full_category_reason_and_exports_off_thread(self):
        import csv
        from ui.records_panel import RecordsPanel
        aid=self.ids[0];category=BY_CODE['approval.other'];reason=('=Specific explanation: '+('reviewed evidence '*12)).rstrip()
        self.assertTrue(self.db.update_appeal_decision(aid,'approved',reason,decided_by='admin',decision_category_code=category.code))
        records=RecordsPanel(self.root,database=self.db,username='admin')
        records.grid(row=0,column=0,sticky='nsew')
        records._switch_tab('history')
        self.until(lambda:not records._history_task.busy)
        values=records._hist_tree.item(records._hist_tree.get_children()[0])['values']
        self.assertIn(category.label,values);self.assertIn(reason,values)
        target=Path(self.tmp.name)/'history.csv';main=threading.get_ident();original=self.db.get_decision_history
        def checked(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),main)
            return original(*args,**kwargs)
        with patch.object(self.db,'get_decision_history',side_effect=checked), patch('ui.records_panel.filedialog.asksaveasfilename',return_value=str(target)):
            records._export_history();self.until(lambda:not records._history_export_task.busy)
        with target.open(encoding='utf-8-sig',newline='') as stream:row=next(csv.DictReader(stream))
        self.assertEqual(row['Decision Category'],category.label)
        self.assertEqual(row['Decision Category Code'],category.code)
        self.assertEqual(row['Administrator Decision Reason'],"'"+reason)
        # The root owns this persistent workspace, as it does in the dashboard;
        # tearDown restores scaling before destroying the complete widget tree.

if __name__=='__main__':unittest.main()
