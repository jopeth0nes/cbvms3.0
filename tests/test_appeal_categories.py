"""Category/action invariants at the existing transactional decision boundary."""
import csv
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.appeal_categories import CATEGORIES, BY_CODE, VERSION, LEGACY_CATEGORY, category_display
from core.portal_state import page_snapshot
from core.report_csv import live_rows, write_csv, read_csv, DISCIPLINE_BASE
from core.reports import violation_values, VIOLATION_HEADERS
from database.appeal_category_migration import migrate_appeal_categories
from database.db_manager import CBVMSDatabase
from tests.evidence_fixture import picture_evidence


class AppealCategoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'categories.db'
        self.db=CBVMSDatabase(self.path);self.db.initialize(process_deadlines=False)
        self.serial=0

    def pending(self):
        self.serial+=1
        sid=f'000{self.serial}-A/B'
        self.db.insert_student(sid,'Student','BSIT','3A',b'face',b'photo')
        vid=self.db.log_violation(sid,'Student','wrong_uniform',snapshot_jpeg=picture_evidence()[2])
        aid=self.db.insert_appeal(vid,sid,'Please review the original incident.',evidence=picture_evidence())
        return sid,vid,aid

    def decide(self,aid,code,decision=None,reason='Individually reviewed evidence and written explanation.',actor='admin'):
        return self.db.update_appeal_decision(aid,decision or BY_CODE[code].decision,reason,
            decided_by=actor,decision_category_code=code)

    def protected(self):
        with self.db.connect() as conn:
            return {table:list(map(tuple,conn.execute(f'SELECT * FROM {table}'))) for table in
                ('appeals','decision_history','violations','strikes','strike_events','student_suspensions',
                 'student_notifications','evidence_files','students')}

    def test_exact_catalog_and_all_24_category_action_combinations(self):
        self.assertEqual(len(CATEGORIES),12)
        self.assertEqual(sum(c.decision=='approved' for c in CATEGORIES),7)
        self.assertEqual(len(BY_CODE),12)
        for category in CATEGORIES:
            for action in ('approved','rejected'):
                with self.subTest(category=category.code,action=action):
                    sid,vid,aid=self.pending();before=self.protected()
                    saved=self.decide(aid,category.code,action)
                    self.assertEqual(saved,category.decision==action)
                    if not saved:
                        self.assertEqual(self.protected(),before)
                        continue
                    case=self.db.get_appeal_case(aid,username='admin')
                    history=self.db.get_decision_history_for_appeal(aid)
                    self.assertEqual(len(history),1)
                    for row in (case,history[0],self.db.get_appeals_for_student(sid)[0]):
                        self.assertEqual(row['decision_category_code'],category.code)
                        self.assertEqual(row['decision_category_label'],category.label)
                        self.assertEqual(row['decision_category_version'],VERSION)
                        self.assertEqual(row['decision_category_display'],category.label)
                        self.assertEqual(row['decided_by'],'admin')
                        self.assertEqual(row['decided_at'],case['decided_at'])
                        self.assertEqual(row['admin_notes'],'Individually reviewed evidence and written explanation.')
                    with self.db.connect() as conn:
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes WHERE violation_id=? AND is_active=1',(vid,)).fetchone()[0],int(action=='rejected'))
                    self.assertFalse(self.decide(aid,category.code,action))
                    self.assertEqual(len(self.db.get_decision_history_for_appeal(aid)),1)

    def test_missing_invalid_values_and_student_session_do_not_mutate(self):
        sid,vid,aid=self.pending();before=self.protected()
        for code in (None,'','unknown','Other approval reason',[],123):
            self.assertFalse(self.decide(aid,code,'approved'))
        for reason in ('','   ','\n\t'):
            self.assertFalse(self.decide(aid,'approval.other',reason=reason))
        for actor in ('','student','nonexistent'):
            self.assertFalse(self.decide(aid,'approval.other',actor=actor))
        self.db.student_session_ref={'token':'student'}
        self.assertFalse(self.decide(aid,'approval.other',actor='admin'))
        self.db.student_session_ref=None
        self.assertEqual(self.protected(),before)

    def test_competing_administrators_commit_one_matching_snapshot_and_effect(self):
        for left,right in (('rejection.violation_confirmed','rejection.other'),
                           ('approval.other','rejection.other'),('approval.other','approval.detection_error')):
            sid,vid,aid=self.pending();barrier=threading.Barrier(2)
            def review(code,actor):
                db=CBVMSDatabase(self.path,timeout=2)
                barrier.wait()
                return db.update_appeal_decision(aid,BY_CODE[code].decision,actor+' explanation',
                    decided_by=actor,decision_category_code=code)
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(review,left,'admin'),pool.submit(review,right,'superadmin')]
                self.assertEqual(sum(job.result() for job in jobs),1)
            row=self.db.get_appeal_case(aid,username='admin')
            self.assertEqual(row['admin_notes'],row['decided_by']+' explanation')
            self.assertEqual(row['decision_category_code'],left if row['decided_by']=='admin' else right)
            self.assertEqual(len(self.db.get_decision_history_for_appeal(aid)),1)
            with self.db.connect() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes WHERE violation_id=?',(vid,)).fetchone()[0],int(row['status']=='rejected'))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_notifications WHERE event_key LIKE ?",(f'appeal:{aid}:%',)).fetchone()[0],1)

    def test_integrity_blocks_every_rejection_and_duplicate_approval_never_deletes(self):
        for category in (c for c in CATEGORIES if c.decision=='rejected'):
            sid,vid,aid=self.pending()
            with self.db.connect() as conn:conn.execute("UPDATE violations SET snapshot=X'0102' WHERE id=?",(vid,))
            before=self.protected()
            self.assertFalse(self.decide(aid,category.code))
            self.assertEqual(self.protected(),before)
            self.assertTrue(self.decide(aid,'approval.invalid_original_evidence'))
        sid,vid,aid=self.pending();_,other,_=self.pending()
        with self.db.connect() as conn:original=list(map(tuple,conn.execute('SELECT * FROM violations WHERE id=?',(other,))))
        self.assertTrue(self.decide(aid,'approval.duplicate_record'))
        with self.db.connect() as conn:
            self.assertEqual(original,list(map(tuple,conn.execute('SELECT * FROM violations WHERE id=?',(other,)))))
            self.assertEqual(conn.execute('SELECT snapshot FROM violations WHERE id=?',(vid,)).fetchone()[0],picture_evidence()[2])

    def test_transaction_failure_rolls_back_category_reason_history_and_strike(self):
        sid,vid,aid=self.pending();before=self.protected()
        with self.db.connect() as conn:
            conn.execute("CREATE TRIGGER reject_history BEFORE INSERT ON decision_history BEGIN SELECT RAISE(ABORT,'fixture disk failure'); END")
        self.assertFalse(self.decide(aid,'rejection.other'))
        self.assertEqual(self.protected(),before)
        with self.db.connect() as conn:conn.execute('DROP TRIGGER reject_history')
        self.assertTrue(self.decide(aid,'rejection.other'))

    def test_restart_consistent_history_portal_reports_and_csv(self):
        sid,vid,aid=self.pending();category=BY_CODE['approval.detection_error'];reason='Manual <review> = explanation'
        self.assertTrue(self.decide(aid,category.code,reason=reason))
        self.db=CBVMSDatabase(self.path);self.db.initialize(process_deadlines=False)
        for row in (self.db.get_appeal_case(aid,username='superadmin'),
                    self.db.get_appeal_inbox(username='admin',status='history')['rows'][0],
                    self.db.get_all_appeals_full()[0],page_snapshot(self.db,sid,'appeals')['_appeals'][0],
                    self.db.get_decision_history()[0]):
            self.assertEqual(row['decision_category_display'],category.label)
            self.assertEqual(row['admin_notes'],reason)
        row=next(r for r in live_rows(self.db,'Discipline') if r['record_id']==str(vid))
        self.assertEqual(row['decision_category_label'],category.label)
        self.assertEqual(row['decision_reason'],reason)
        target=Path(self.tmp.name)/'report.csv';write_csv(target,'Discipline',[row])
        self.assertEqual(read_csv(target,'Discipline')[0]['decision_reason'],reason)
        values=violation_values(self.db.get_all_violations_full())[0]
        self.assertEqual(len(values),len(VIOLATION_HEADERS))
        self.assertIn(category.label,values);self.assertIn(reason,values)
        # Existing report CSVs remain readable; category is not invented on import.
        with target.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=DISCIPLINE_BASE,extrasaction='ignore');writer.writeheader();writer.writerow(row)
        self.assertEqual(read_csv(target,'Discipline')[0]['decision_category_code'],'')

    def test_legacy_migration_preserves_original_fields_and_never_guesses(self):
        path=Path(self.tmp.name)/'legacy.db'
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE appeals(id INTEGER PRIMARY KEY,status TEXT,admin_notes TEXT)')
            conn.execute('CREATE TABLE decision_history(id INTEGER PRIMARY KEY,decision TEXT,admin_notes TEXT)')
            conn.execute("INSERT INTO appeals VALUES(1,'approved','Uniform compliant / detection error')")
            conn.execute("INSERT INTO decision_history VALUES(1,'approved','Old explanation')")
            for _ in range(2):migrate_appeal_categories(conn)
            conn.row_factory=sqlite3.Row
            for table in ('appeals','decision_history'):
                row=dict(conn.execute(f'SELECT * FROM {table}').fetchone())
                self.assertIsNone(row['decision_category_code']);self.assertIsNone(row['decision_category_version'])
                self.assertEqual(category_display(row),LEGACY_CATEGORY)
            self.assertEqual(conn.execute('SELECT admin_notes FROM appeals').fetchone()[0],'Uniform compliant / detection error')
    def test_legacy_completed_views_remain_explicit_after_restart(self):
        sid,vid,aid=self.pending()
        with self.db.connect() as conn:
            conn.execute("UPDATE appeals SET status='approved',admin_notes='Old handwritten explanation',decided_by='former reviewer',decided_at='2020-01-02 03:04:05' WHERE id=?",(aid,))
            conn.execute("UPDATE violations SET status='resolved' WHERE id=?",(vid,))
            conn.execute("INSERT INTO decision_history(appeal_id,violation_id,student_id,decision,admin_notes) VALUES(?,?,?,'approved','Old handwritten explanation')",(aid,vid,sid))
        before=self.protected()
        self.db.initialize(process_deadlines=False)
        self.assertEqual(self.protected(),before)
        for row in (self.db.get_appeal_case(aid,username='admin'),self.db.get_decision_history()[0],
                    page_snapshot(self.db,sid,'appeals')['_appeals'][0]):
            self.assertEqual(row['decision_category_display'],LEGACY_CATEGORY)
        self.assertEqual(live_rows(self.db,'Discipline')[0]['decision_category_label'],LEGACY_CATEGORY)
        self.assertFalse(self.decide(aid,'approval.other',reason='Attempted historical rewrite'))
        self.assertEqual(self.protected(),before)

    def test_compatibility_entry_points_cannot_bypass_required_category(self):
        sid,vid,aid=self.pending()
        self.assertFalse(self.db.decide_appeal(aid,'approved','Written explanation',decided_by='admin'))
        args=(aid,vid,sid,'Student','wrong_uniform','approved','pending','Written explanation')
        self.assertFalse(self.db.log_decision(*args,decided_by='admin'))
        self.assertTrue(self.db.log_decision(*args,decided_by='admin',decision_category_code='approval.other'))
        self.assertEqual(len(self.db.get_decision_history_for_appeal(aid)),1)


if __name__=='__main__':unittest.main()
