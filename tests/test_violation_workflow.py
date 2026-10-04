"""Publication-first appeal lifecycle, with isolated data and a controllable clock."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from core.discipline import parse_db_datetime, format_db_datetime, display_local_datetime
from core.portal_state import page_snapshot
from database.db_manager import CBVMSDatabase
from database.appeal_migration import VERSION
from tests.evidence_fixture import picture_evidence


class ViolationWorkflowTests(unittest.TestCase):
    SID = '2023-00883'
    OTHER = '2023-883'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.db = CBVMSDatabase(Path(tmp.name)/'test.db')
        self.now = datetime(2099,8,1,2,tzinfo=timezone.utc)
        clock = patch('database.db_manager.utc_now',return_value=self.now)
        self.clock = clock.start(); self.addCleanup(clock.stop)
        self.db.initialize()
        for sid in (self.SID,self.OTHER):
            self.db.insert_student(sid,'Fixture student','BSIT','3A',b'',b'')

    def detect(self, **kwargs):
        return self.db.log_violation(self.SID,'Fixture student','Wrong Uniform (82%)',
            snapshot_jpeg=picture_evidence()[2], **kwargs)

    def submit(self, vid, sid=None):
        return self.db.insert_appeal(vid,sid or self.SID,'Please review this explanation and image.',
                                   evidence=picture_evidence())

    def decide(self, aid, decision='rejected'):
        return self.db.update_appeal_decision(aid,decision,'Evidence reviewed by administrator.',decided_by='admin', decision_category_code=('approval.detection_error' if decision=='approved' else 'rejection.violation_confirmed'))

    def count(self, table):
        with self.db.connect() as conn:
            return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def record(self, vid):
        with self.db.connect() as conn:
            return dict(conn.execute('SELECT * FROM violations WHERE id=?',(vid,)).fetchone())

    def test_immediate_owner_visibility_appeal_evidence_and_publication_notice(self):
        vid=self.detect(detected_at=self.now-timedelta(days=20))
        row=self.db.get_visible_violations_for_student(self.SID)[0]
        self.assertEqual(row['id'],vid); self.assertTrue(row['can_appeal'])
        self.assertEqual(row['snapshot'],picture_evidence()[2])
        self.assertEqual(parse_db_datetime(row['appeal_opened_at']),self.now)
        self.assertEqual(parse_db_datetime(row['appeal_deadline']),self.now+timedelta(hours=120))
        self.assertEqual(row['appeal_window_status'],'eligible')
        self.assertEqual(self.count('strikes'),0)
        self.assertEqual(len(self.db.get_notifications_for_student(self.SID)),1)
        self.assertEqual(self.db.get_visible_violations_for_student(self.OTHER),[])
        self.assertFalse(self.db.get_appeal_eligibility(vid,self.OTHER)['eligible'])
        self.assertIsNone(self.submit(vid,self.OTHER))
        aid=self.submit(vid)
        self.assertEqual(self.db.get_student_appeal_evidence(aid,self.OTHER),[])
        self.assertEqual(len(self.db.get_student_appeal_evidence(aid,self.SID)),1)

    def test_confirmation_does_not_restart_deadline_or_award_strike(self):
        vid=self.detect(); before=self.record(vid)
        self.clock.return_value=self.now+timedelta(days=2)
        self.assertTrue(self.db.confirm_violation(vid))
        self.assertEqual(self.record(vid)['appeal_deadline'],before['appeal_deadline'])
        self.assertEqual(self.count('strikes'),0)
        self.assertEqual(self.count('student_notifications'),1)
        self.assertIsNotNone(self.submit(vid))

    def test_exact_boundary_accepts_and_microsecond_late_rejects(self):
        vid=self.detect(); second=self.detect()
        self.clock.return_value=self.now+timedelta(hours=120)
        self.assertEqual(self.db.process_expired_deadlines()['appeal_windows_expired'],0)
        self.assertIsNotNone(self.submit(vid))
        self.clock.return_value+=timedelta(microseconds=1)
        self.assertIsNone(self.submit(second))
        self.assertEqual(self.db.process_expired_deadlines()['appeal_windows_expired'],1)
        self.assertEqual(self.count('strikes'),1)

    def test_pending_appeal_protected_after_deadline_and_restart(self):
        vid=self.detect(); self.submit(vid)
        self.clock.return_value+=timedelta(days=40)
        self.db.initialize(); self.db.process_expired_deadlines()
        self.assertEqual(self.count('strikes'),0)
        self.assertEqual(self.db.admin_appeal_pending_count(),1)
        self.assertEqual(self.db.get_appeal_for_violation(vid)['status'],'pending')

    def test_unappealed_expiry_is_one_strike_and_one_notice_after_restart(self):
        vid=self.detect(); self.clock.return_value+=timedelta(days=6)
        self.db.initialize()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.db.process_expired_deadlines(),range(8)))
        self.db.initialize()
        self.assertEqual(self.count('strikes'),1)
        self.assertEqual(self.count('student_notifications'),2)
        row=self.db.get_visible_violations_for_student(self.SID)[0]
        self.assertFalse(row['can_appeal']);self.assertEqual(row['appeal_eligibility_reason'],'deadline_expired')
        self.assertFalse(self.db.delete_violation(vid))

    def test_approval_resolves_without_any_active_strike(self):
        vid=self.detect();aid=self.submit(vid)
        self.assertTrue(self.decide(aid,'approved'))
        self.clock.return_value+=timedelta(days=8);self.db.process_expired_deadlines()
        self.assertEqual(self.count('strikes'),0)
        self.assertEqual(self.record(vid)['status'],'resolved')
        self.assertEqual(page_snapshot(self.db,self.SID,'violations',group='Resolved')['_violations'][0]['id'],vid)
        self.assertFalse(self.db.get_appeal_eligibility(vid,self.SID)['eligible'])

    def test_rejection_and_competing_decisions_are_transactional(self):
        vid=self.detect();aid=self.submit(vid)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:self.decide(aid),range(3)))
        self.assertEqual(results.count(True),1)
        self.assertFalse(self.decide(aid,'approved'))
        self.assertEqual(self.count('strikes'),1);self.assertEqual(self.count('decision_history'),1)
        history=self.db.get_decision_history()[0]
        self.assertEqual(history['decided_by'],'admin')
        self.assertEqual(history['decided_at'],format_db_datetime(self.now))
        self.db.process_expired_deadlines(now=self.now+timedelta(days=8))
        self.assertEqual(self.count('strikes'),1)
        self.assertEqual(len([n for n in self.db.get_notifications_for_student(self.SID)
                              if n['event_key']==f'appeal:{aid}:rejected']),1)

    def test_decision_requires_real_admin_and_reason(self):
        aid=self.submit(self.detect())
        for actor,reason in [('', 'Reason'),(self.SID,'Reason'),('unknown','Reason'),('admin','  ')]:
            self.assertFalse(self.db.update_appeal_decision(aid,'rejected',reason,decided_by=actor, decision_category_code='rejection.violation_confirmed'))
        self.assertEqual(self.count('strikes'),0);self.assertEqual(self.count('decision_history'),0)
        with self.assertRaises(PermissionError):
            self.db.get_appeal_case(aid,username=self.SID)
        with self.assertRaises(PermissionError):
            self.db.get_appeal_inbox(username=self.SID)

    def test_decision_storage_failure_rolls_back_every_effect(self):
        aid=self.submit(self.detect())
        with self.db.connect() as conn:
            conn.execute("CREATE TRIGGER fail_decision BEFORE INSERT ON decision_history BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        self.assertFalse(self.decide(aid))
        self.assertEqual(self.count('strikes'),0)
        self.assertEqual(self.db.get_appeal_case(aid,username='admin')['status'],'pending')
        self.assertEqual(self.count('student_notifications'),1)

    def test_evidence_failure_rolls_back_appeal_alert_and_image(self):
        vid=self.detect()
        with self.db.connect() as conn:
            conn.execute("CREATE TRIGGER fail_image BEFORE INSERT ON evidence_files BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        self.assertIsNone(self.submit(vid))
        self.assertEqual(self.count('appeals'),0);self.assertEqual(self.count('evidence_files'),0)
        self.assertEqual(self.db.admin_appeal_unread_count(),0)
        self.assertTrue(self.db.get_appeal_eligibility(vid,self.SID)['eligible'])

    def test_concurrent_submissions_commit_exactly_one(self):
        vid=self.detect()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.submit(vid),range(4)))
        self.assertEqual(sum(a is not None for a in results),1)
        self.assertEqual(self.count('evidence_files'),1);self.assertEqual(self.count('appeals'),1)

    def test_ai_late_completion_never_changes_decision_or_strike(self):
        aid=self.submit(self.detect());self.decide(aid)
        self.db.update_appeal_ai_analysis(aid,'Approve','99%','Advisory')
        self.assertEqual(self.db.get_appeal_case(aid,username='admin')['status'],'rejected')
        self.assertEqual(self.count('strikes'),1);self.assertEqual(self.count('decision_history'),1)

    def test_dismissed_record_never_gains_appeal_or_strike(self):
        vid=self.detect();self.assertTrue(self.db.dismiss_violation(vid,reason='False detection'))
        self.assertIsNone(self.submit(vid));self.db.process_expired_deadlines(now=self.now+timedelta(days=8))
        self.assertEqual(self.count('strikes'),0)
        self.assertFalse(self.db.confirm_violation(vid))

    def test_resolved_legacy_case_cannot_gain_strike_from_pending_appeal(self):
        vid=self.detect();aid=self.submit(vid)
        with self.db.connect() as conn:
            conn.execute("UPDATE violations SET status='resolved' WHERE id=?",(vid,))
        self.assertFalse(self.decide(aid))
        self.assertEqual(self.count('strikes'),0)
        self.assertEqual(self.count('decision_history'),0)

    def test_dismissal_does_not_strand_pending_appeal(self):
        vid=self.detect();self.submit(vid)
        self.assertFalse(self.db.dismiss_violation(vid,reason='Use appeal decision'))
        self.assertEqual(self.record(vid)['status'],'pending_review')

    def test_third_finalized_strike_threshold_category_and_office_clearance(self):
        pending=self.detect();aid=self.submit(pending)
        for _ in range(3):
            self.decide(self.submit(self.detect()))
        self.assertEqual(self.db.get_strike_count(self.SID,'wrong_uniform'),3)
        self.assertEqual(self.count('strike_events'),1)
        self.assertTrue(self.db.get_strike_summary(self.SID)[0]['action_required'])
        automatic = self.db.get_active_suspension(self.SID, now=self.now)
        self.assertIsNotNone(automatic)
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            self.db.impose_suspension(self.SID, reason='Office reviewed', starts_at=self.now,
                ends_at=None, imposed_by='admin', violation_id=pending)
        with patch('database.student_management.utc_now', return_value=self.now):
            self.assertTrue(self.db.lift_suspension(automatic['id'], lifted_by='admin',
                reason='Office reviewed and replaced with manual suspension'))
        suspension=self.db.impose_suspension(self.SID,reason='Office reviewed',starts_at=self.now,
            ends_at=None,imposed_by='admin',violation_id=pending)
        self.decide(aid,'approved')
        self.assertEqual(self.db.get_active_suspension(self.SID,now=self.now)['id'],suspension)
        self.assertEqual(self.db.get_strike_count(self.SID,'earring'),0)
        self.db.set_current_academic_term('Semester 2','2099-2100')
        self.assertEqual(self.db.get_strike_count(self.SID,'wrong_uniform'),0)
        self.assertEqual(self.count('strikes'),3)

    def test_migration_is_versioned_fresh_and_preserves_historical_decisions_and_conflicts(self):
        historical=self.detect();aid=self.submit(historical);self.decide(aid)
        conflict=self.detect();self.submit(conflict)
        fresh=self.detect()
        with self.db.connect() as conn:
            conn.execute('DELETE FROM workflow_migrations WHERE version=?',(VERSION,))
            conn.execute("UPDATE violations SET status='unreviewed',appeal_opened_at=NULL,appeal_deadline=NULL,lifecycle_origin=NULL WHERE id=?",(fresh,))
            conn.execute("INSERT INTO strikes (violation_id,student_id,violation_code,semester_id,awarded_at) SELECT id,student_id,violation_code,semester_id,timestamp FROM violations WHERE id=?",(conflict,))
            before=tuple(conn.execute('SELECT * FROM decision_history').fetchone())
        self.clock.return_value+=timedelta(days=20)
        self.db.initialize(process_deadlines=False)
        first=self.record(fresh)
        self.assertEqual(parse_db_datetime(first['appeal_opened_at']),self.clock.return_value)
        self.assertEqual(first['lifecycle_origin'],VERSION)
        self.assertEqual(self.record(conflict)['lifecycle_origin'],'reconciliation_required')
        self.db.initialize(process_deadlines=False)
        self.assertEqual(self.record(fresh),first)
        self.assertEqual(self.count('strikes'),2)
        with self.db.connect() as conn:
            self.assertEqual(tuple(conn.execute('SELECT * FROM decision_history').fetchone()),before)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM workflow_migrations').fetchone()[0],1)

    def test_explicit_pending_strike_reconciliation_preserves_office_clearance_and_rejection_reuses_ledger(self):
        vid=self.detect();aid=self.submit(vid)
        with self.db.connect() as conn:
            conn.execute("INSERT INTO strikes (violation_id,student_id,violation_code,semester_id,awarded_at) SELECT id,student_id,violation_code,semester_id,timestamp FROM violations WHERE id=?",(vid,))
        suspension=self.db.impose_suspension(self.SID,reason='Office review',starts_at=self.now,
            ends_at=None,imposed_by='admin')
        self.assertTrue(self.db.reconcile_pending_appeal_strike(vid,username='admin',reason='Legacy pending appeal'))
        self.assertFalse(self.db.reconcile_pending_appeal_strike(vid,username='admin',reason='Retry'))
        self.assertEqual(self.db.get_strike_count(self.SID,'wrong_uniform'),0)
        self.assertEqual(self.db.get_active_suspension(self.SID,now=self.now)['id'],suspension)
        self.clock.return_value+=timedelta(days=20);self.db.process_expired_deadlines()
        self.assertEqual(self.db.get_strike_count(self.SID,'wrong_uniform'),0)
        self.assertTrue(self.decide(aid))
        self.assertEqual(self.count('strikes'),1)
        self.assertEqual(self.db.get_strike_count(self.SID,'wrong_uniform'),1)
        self.assertEqual(self.count('discipline_reconciliations'),1)

    def test_legacy_approval_recomputes_threshold_without_lifting_office_suspension(self):
        vid=self.detect();aid=self.submit(vid)
        for _ in range(2):self.decide(self.submit(self.detect()))
        with self.db.connect() as conn:
            conn.execute("INSERT INTO strikes (violation_id,student_id,violation_code,semester_id,awarded_at) SELECT id,student_id,violation_code,semester_id,timestamp FROM violations WHERE id=?",(vid,))
            row=conn.execute('SELECT * FROM violations WHERE id=?',(vid,)).fetchone()
            self.db._sync_third_strike_event_conn(conn,student_id=self.SID,violation_code='wrong_uniform',
                semester_id=row['semester_id'],changed_at=format_db_datetime(self.now))
        self.assertTrue(self.db.get_strike_summary(self.SID)[0]['action_required'])
        self.decide(aid,'approved')
        self.assertEqual(self.db.get_strike_count(self.SID,'wrong_uniform'),2)
        self.assertFalse(self.db.get_strike_summary(self.SID)[0]['action_required'])
        self.assertEqual(self.count('strikes'),3)

    def test_unknown_category_and_unmapped_identity_never_publish(self):
        for sid,code in [('unknown','wrong_uniform'),(self.SID,'unknown_person')]:
            vid=self.db.log_violation(sid,'Unknown',code)
            self.assertFalse(self.db.get_appeal_eligibility(vid,sid)['eligible'])
        self.assertEqual(self.count('student_notifications'),0)

    def test_inbox_search_pagination_status_and_pending_count_independent_of_unread(self):
        aids=[self.submit(self.detect()) for _ in range(13)]
        self.db.mark_admin_appeal_alert_read()
        self.assertEqual(self.db.admin_appeal_unread_count(),0)
        self.assertEqual(self.db.admin_appeal_pending_count(),13)
        page=self.db.get_appeal_inbox(username='admin',search=self.SID,limit=10)
        self.assertEqual(page['total'],13);self.assertEqual(len(page['rows']),10)
        self.assertEqual(len(self.db.get_appeal_inbox(username='admin',offset=10,limit=10)['rows']),3)
        self.assertEqual(self.db.get_appeal_inbox(username='admin',search='missing')['total'],0)
        self.decide(aids[0],'approved')
        self.assertEqual(self.db.get_appeal_inbox(username='admin',status='approved')['total'],1)
        self.assertEqual(self.db.admin_appeal_pending_count(),12)

    def test_timezone_display_matches_configured_local_timezone(self):
        import os,time
        prior=os.environ.get('TZ')
        try:
            os.environ['TZ']='Asia/Manila';time.tzset()
            from ui.student_portal import _display_ts
            self.assertEqual(_display_ts('2099-08-01 02:00:00'),display_local_datetime('2099-08-01 02:00:00'))
            self.assertIn('10:00',_display_ts('2099-08-01 02:00:00'))
        finally:
            if prior is None:os.environ.pop('TZ',None)
            else:os.environ['TZ']=prior
            time.tzset()

if __name__=='__main__':unittest.main()
