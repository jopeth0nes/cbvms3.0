import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import numpy as np
from core.academic_calendar import instant, ENROLLED, OUTSIDE, UNVERIFIED, UPCOMING
from core.face_capture import FaceCapture
from database.db_manager import CBVMSDatabase
from database.term_migration import migrate, preview


class TermEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'campus.db';self.db=CBVMSDatabase(self.path)
        self.db.initialize(process_deadlines=False)
        self.clock=patch('core.academic_calendar.utc_now',return_value=instant('2026-08-01T00:00:00Z'))
        self.clock.start();self.addCleanup(self.clock.stop)
        with self.db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO users(username,password_hash,role) VALUES('root','test','superadmin')")
            conn.execute("INSERT OR IGNORE INTO users(username,password_hash,role) VALUES('staff','test','admin')")
            conn.execute("INSERT INTO students(student_id,name) VALUES('S1','Student One')")
        self.academics=dict(college_department='College of Computer Studies',course='BSIT',report_year_level='1st Year',report_section='A')
        self.first=self.term('semester_1','2026-06-01','2026-10-31')
        self.second=self.term('semester_2','2026-11-01','2027-03-31')

    def term(self,code,start,end,**kw):
        return self.db.save_academic_term(actor='root',semester_code=code,school_year='2026-2027',
            start_date=start,end_date=end,publish=True,**kw)

    def captures(self,seed=1):
        rng=np.random.default_rng(seed);emb=np.zeros(512,dtype=np.float32);emb[0]=1
        return [FaceCapture(('S1',),('camera',seed*10+i),time.monotonic(),(0,0,30,30),
            rng.integers(0,255,(30,30,3),dtype=np.uint8),emb,bytes(rng.integers(0,255,200,dtype=np.uint8))) for i in range(3)]

    def enroll(self,term=None,seed=1):
        return self.db.confirm_term_enrollment('S1',term or self.first,self.academics,self.captures(seed),('S1',),actor='root')

    def test_manila_boundaries_summer_and_gap(self):
        self.assertEqual(self.db.get_current_academic_term(now='2026-10-31T15:59:59.999999Z')['id'],self.first)
        self.assertEqual(self.db.get_current_academic_term(now='2026-10-31T16:00:00Z')['id'],self.second)
        self.assertEqual(self.db.get_current_academic_term(now='2027-04-01T00:00:00Z'),{})
        summer=self.term('summer','2027-04-15','2027-05-15')
        self.assertEqual(self.db.get_current_academic_term(now='2027-04-14T16:00:00Z')['id'],summer)

    def test_overlap_and_permissions(self):
        with self.assertRaises(sqlite3.IntegrityError):self.term('summer','2026-10-31','2026-11-10')
        with self.assertRaises(PermissionError):
            self.db.save_academic_term(actor='staff',semester_code='summer',school_year='x',start_date='2027-05-01',end_date='2027-05-31')
        with self.assertRaises(PermissionError):
            self.db.confirm_term_enrollment('S1',self.first,self.academics,self.captures(),('S1',),actor='staff')

    def test_upcoming_enrollment_keeps_current_and_identity(self):
        self.enroll()
        old=self.db.get_student_by_student_id('S1')['encoding']
        self.enroll(self.second,2)
        context=self.db.enrollment_context('S1')
        self.assertEqual(context['status'],ENROLLED)
        self.assertEqual(context['enrollment']['semester_id'],self.first)
        self.assertEqual(self.db.get_student_by_student_id('S1')['encoding'],old)
        self.assertEqual(len(self.db.term_history('S1')['enrollments']),2)

    def test_fresh_capture_reuse_and_cancellation(self):
        captures=self.captures()
        with self.assertRaises(ValueError):self.db.confirm_term_enrollment('S1',self.first,self.academics,[],('S1',),actor='root')
        self.db.confirm_term_enrollment('S1',self.first,self.academics,captures,('S1',),actor='root')
        with self.assertRaises(ValueError):self.db.confirm_term_enrollment('S1',self.second,self.academics,captures,('S1',),actor='root')
        with self.db.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM enrollment_captures').fetchone()[0],1)

    def test_duplicate_concurrent_confirmation(self):
        def run(seed):
            try:self.enroll(seed=seed);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(run,[1,2])),1)

    def test_new_identity_and_account_rollback_with_failed_confirmation(self):
        with self.db.connect() as conn:
            conn.execute("DELETE FROM students WHERE student_id='S1'")
        identity=dict(student_id='S1',name='Student One',course='BSIT',year_and_section='1st Year A',
                      encoding=None,photo=None,**{k:v for k,v in self.academics.items() if k!='course'})
        with self.assertRaises(ValueError):
            self.db.confirm_term_enrollment('S1',999999,self.academics,self.captures(),('S1',),
                                           actor='root',new_student=identity)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM students WHERE student_id='S1'").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_accounts WHERE student_id='S1'").fetchone()[0],0)
        self.db.confirm_term_enrollment('S1',self.first,self.academics,self.captures(2),('S1',),
                                       actor='root',new_student=identity)
        self.assertTrue(self.db.enrollment_context('S1')['eligible'])

    def test_preview_reads_committed_wal_without_mutating_source(self):
        with sqlite3.connect(self.path) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO students(student_id,name) VALUES('WAL','Committed WAL')")
            writer.commit()
            files=[self.path,Path(str(self.path)+'-wal')]
            before={p:p.read_bytes() for p in files}
            self.assertEqual(preview(self.path)['counts']['students'],2)
            self.assertEqual(before,{p:p.read_bytes() for p in files})

    def test_cli_copy_migrates_new_destination_and_preserves_source(self):
        import subprocess
        import sys
        with self.db.connect() as conn:
            conn.execute('DROP TABLE enrollment_review_events')
        before=self.path.read_bytes()
        target=Path(self.tmp.name)/'rehearsal.db'
        command=[sys.executable,'scripts/preview_term_migration.py','--db',str(self.path),
                 '--apply-to-new-copy',str(target)]
        result=subprocess.run(command,capture_output=True,text=True,check=True)
        report=json.loads(result.stdout)
        self.assertEqual(report['copy_foreign_key_errors'],[])
        self.assertEqual(before,self.path.read_bytes())
        with sqlite3.connect(target) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM enrollment_review_events').fetchone()[0],0)
        self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_review_retains_each_accepted_incident_image(self):
        first=self.db.record_enrollment_review('S1',observed_at='2026-08-02T00:00:00Z',source_id='gate',snapshot=b'first')
        second=self.db.record_enrollment_review('S1',observed_at='2026-08-02T00:10:00Z',source_id='gate',snapshot=b'second')
        self.assertEqual(first,second)
        with self.db.connect() as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT snapshot FROM enrollment_review_events ORDER BY id')],
                             [b'first',b'second'])

    def test_legacy_review_no_strike_and_actual_evidence(self):
        self.assertEqual(self.db.enrollment_context('S1')['status'],UNVERIFIED)
        image=b'incident-image'
        self.assertIsNone(self.db.log_violation('S1','Student One','wrong_uniform',image,detected_at='2026-08-02T00:00:00Z'))
        with self.db.connect() as conn:
            row=conn.execute('SELECT * FROM enrollment_reviews').fetchone()
            self.assertEqual(row['snapshot'],image)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM violations').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes').fetchone()[0],0)
        self.assertIsNone(self.db.record_enrollment_review('S1',observed_at='2026-08-02T00:00:01Z',source_id='manual',snapshot=b'later'))
        with self.assertRaises(PermissionError):self.db.resolve_enrollment_review(row['id'],actor='staff',reason='Checked')
        self.db.resolve_enrollment_review(row['id'],actor='root',reason='Requested fresh enrollment')
        self.assertFalse(self.db.enrollment_context('S1')['eligible'])

    def test_delayed_events_and_unknown_context(self):
        self.enroll()
        violation=self.db.log_violation('S1','Student One','wrong_uniform',detected_at='2026-08-02T00:00:00Z')
        self.assertIsNotNone(violation)
        event=self.db.log_security_event('unknown',observed_at='2026-11-03T00:00:00Z')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT semester_id FROM violations WHERE id=?',(violation,)).fetchone()[0],self.first)
            self.assertEqual(conn.execute('SELECT semester_id FROM security_events WHERE id=?',(event,)).fetchone()[0],self.second)
        self.db.record_attendance('S1',observed_at='2026-10-31T15:59:59Z')
        self.db.record_attendance('S1',observed_at='2026-10-31T16:00:00Z')
        with self.db.connect() as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT semester_id FROM attendance_events ORDER BY id')],[self.first,self.second])

    def test_offline_rollover_idempotent_preserves_suspension(self):
        with self.db.connect() as conn:
            conn.execute("INSERT INTO student_suspensions(student_id,reason,starts_at,ends_at,imposed_by,imposed_at) VALUES('S1','Original','2026-10-01','2027-04-01','root','2026-10-01')")
        self.db.reconcile_term_boundaries(now='2027-05-01')
        self.db.reconcile_term_boundaries(now='2027-05-01')
        with self.db.connect() as conn:
            row=conn.execute('SELECT * FROM student_suspensions').fetchone()
            self.assertEqual(row['ends_at'],'2027-04-01')
            self.assertEqual(row['effective_end_reason'],'Term rollover')
            self.assertEqual(row['effective_ended_at'],'2026-10-31T16:00:00+00:00')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM academic_audit WHERE action='effective_termination'").fetchone()[0],1)

    def test_date_correction_preview_and_audit(self):
        self.db.log_security_event('x',observed_at='2026-06-01T00:00:00Z')
        with self.assertRaises(ValueError):self.term('semester_1','2026-06-02','2026-10-31',term_id=self.first)
        impact=self.db.term_date_impact(self.first,'2026-06-02','2026-10-31')
        self.assertEqual(len(impact['affected']['security_events']),1)
        self.term('semester_1','2026-06-02','2026-10-31',term_id=self.first,reason='Approved correction',preview_token=impact['token'])
        with self.db.connect() as conn:self.assertEqual(conn.execute('SELECT semester_id FROM security_events').fetchone()[0],self.first)
        self.assertEqual(len(preview(self.path)['date_conflicts']['security_events']),1)

    def test_readonly_preview_repeat_migration_restore(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        report=preview(self.path)
        self.assertEqual(report['historical_enrollments_created'],0)
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        with self.db.connect() as conn:
            migrate(conn);migrate(conn);conn.commit()
            dest=sqlite3.connect(Path(self.tmp.name)/'restore.db');conn.backup(dest);dest.close()
        restored=preview(Path(self.tmp.name)/'restore.db')
        self.assertEqual(report['counts'],restored['counts'])
        self.assertEqual(restored['foreign_key_errors'],[])

    def test_historical_academics_survive_correction_and_delayed_write(self):
        self.enroll()
        with self.db.connect() as conn:
            enrollment_id=conn.execute('SELECT id FROM student_term_enrollments').fetchone()[0]
        with patch('core.academic_calendar.utc_now',return_value=instant('2026-09-01')):
            self.db.correct_enrollment(enrollment_id,actor='root',reason='Section correction',academics={'report_section':'B'})
        self.db.record_attendance('S1',observed_at='2026-08-15')
        self.db.record_attendance('S1',observed_at='2026-09-15')
        with self.db.connect() as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT report_section FROM attendance_events ORDER BY id')],['A','B'])

    def test_old_cases_resolved_in_new_term_cannot_suspend_new_term(self):
        self.enroll()
        ids=[self.db.log_violation('S1','Student One','wrong_uniform',detected_at=f'2026-08-0{i+2}') for i in range(3)]
        with self.db.connect() as conn:
            for vid in ids:
                row=conn.execute('SELECT * FROM violations WHERE id=?',(vid,)).fetchone()
                self.db._award_final_strike_conn(conn,row,'2026-11-05 00:00:00','appeal_rejected')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM student_suspensions').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes WHERE semester_id=?',(self.first,)).fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes WHERE semester_id=?',(self.second,)).fetchone()[0],0)
        self.assertEqual(self.db.term_history('S1')['lifetime_issued'],3)

    def test_outside_term_and_upcoming_only_are_not_unknown(self):
        self.enroll(self.second)
        self.assertEqual(self.db.enrollment_context('S1')['status'],UPCOMING)
        self.assertEqual(self.db.enrollment_context('S1',occurred_at='2027-04-04')['status'],OUTSIDE)
        self.db.record_enrollment_review('S1',observed_at='2027-04-04',source_id='gate',snapshot=b'actual')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM security_events').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes').fetchone()[0],0)
            self.assertIsNone(conn.execute('SELECT semester_id FROM enrollment_reviews').fetchone()[0])

    def test_unverified_roster_and_confirmed_roster_keep_one_identity(self):
        self.assertEqual(self.db.query_term_roster(semester_id=self.first)[0]['status'],'unverified')
        self.enroll()
        self.assertEqual(self.db.query_term_roster(semester_id=self.first)[0]['status'],'confirmed')
        self.assertEqual(self.db.query_term_roster(semester_id=self.second)[0]['status'],'unverified')
        with self.db.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM students').fetchone()[0],1)

    def test_student_history_isolation(self):
        from tests.auth_fixture import portal_session
        token=portal_session(self.db,'S1')
        self.db.student_session_ref={'token':token}
        self.assertEqual(self.db.term_history('S1')['enrollments'],[])
        with self.assertRaises(PermissionError):self.db.term_history('S2')
        with self.assertRaises(PermissionError):self.db.query_term_roster()

    def test_referenced_term_deletion_and_invalid_capture_owner(self):
        self.enroll()
        with self.db.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):conn.execute('DELETE FROM academic_terms WHERE id=?',(self.first,))
            capture=conn.execute('SELECT id FROM enrollment_captures').fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE student_term_enrollments SET semester_id=? WHERE capture_id=?',(self.second,capture))

if __name__=='__main__':unittest.main()
