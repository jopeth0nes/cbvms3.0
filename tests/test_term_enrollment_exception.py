import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import numpy as np

from core.academic_calendar import ENROLLED, UNVERIFIED, instant
from core.face_capture import FaceCapture
from database.db_manager import CBVMSDatabase
from database.term_migration import migrate_enrollment_exceptions


class EnrollmentExceptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=CBVMSDatabase(Path(self.temp.name)/'exception.db')
        self.db.initialize(process_deadlines=False)
        self.clock=patch('core.academic_calendar.utc_now',return_value=instant('2026-08-01T00:00:00Z'))
        self.clock.start();self.addCleanup(self.clock.stop)
        with self.db.connect() as conn:
            conn.execute("INSERT INTO users(username,password_hash,role) VALUES('root','fixture','superadmin')")
            conn.execute("INSERT INTO users(username,password_hash,role) VALUES('staff','fixture','admin')")
            for sid in ('S1','S2','S3'):
                conn.execute('INSERT INTO students(student_id,name) VALUES(?,?)',(sid,f'Student {sid}'))
                conn.execute('INSERT INTO student_accounts(student_id,username,password_hash) VALUES(?,?,?)',
                             (sid,sid,'fixture-password-hash'))
        self.term=self.db.save_academic_term(actor='root',semester_code='semester_1',school_year='2026-2027',
            start_date='2026-06-01',end_date='2026-12-31',publish=True)
        self.academics={'college_department':'College of Computer Studies','course':'Information Technology',
                        'report_year_level':'4th Year','report_section':'A'}
        self.operation_id=str(uuid.uuid4())

    def approve(self, student='S1', **kwargs):
        return self.db.approve_enrollment_exception(student,self.term,self.academics,
            actor=kwargs.pop('actor','root'),reason=kwargs.pop('reason','Owner-approved enrollment exception'),
            operation_id=kwargs.pop('operation_id',self.operation_id))

    def test_exception_grants_term_eligibility_without_fabricating_capture(self):
        self.assertEqual(self.db.enrollment_context('S1')['status'],UNVERIFIED)
        result=self.approve()
        context=self.db.enrollment_context('S1')
        self.assertTrue(context['eligible'])
        self.assertEqual(context['status'],ENROLLED)
        self.assertEqual(context['eligibility_method'],'administrative_exception')
        self.assertNotIn('exception_reason',context['enrollment'])
        self.assertNotIn('exception_actor',context['enrollment'])
        self.assertEqual(result['eligibility_method'],'administrative_exception')
        with self.db.connect() as conn:
            enrollment=conn.execute('SELECT * FROM student_term_enrollments WHERE student_id=?',( 'S1',)).fetchone()
            self.assertEqual(enrollment['status'],'draft')
            self.assertIsNone(enrollment['capture_id'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM enrollment_captures').fetchone()[0],0)
            audit=conn.execute("SELECT * FROM academic_audit WHERE action='approve'").fetchone()
            self.assertEqual(audit['actor'],'root')
            self.assertEqual(audit['reason'],'Owner-approved enrollment exception')

    def test_exception_does_not_apply_before_its_effective_time(self):
        with patch('core.academic_calendar.utc_now',return_value=instant('2026-08-01T00:00:00Z')):
            self.approve()
        self.assertFalse(self.db.enrollment_context('S1',occurred_at='2026-07-31T23:59:59Z')['eligible'])
        self.assertEqual(self.db.enrollment_context('S1',occurred_at='2026-08-01T00:00:00Z')['eligibility_method'],
                         'administrative_exception')

    def test_exception_for_upcoming_published_term_is_reported_as_upcoming(self):
        upcoming=self.db.save_academic_term(actor='root',semester_code='semester_1',school_year='2027-2028',
            start_date='2027-01-01',end_date='2027-06-30',publish=True)
        self.db.approve_enrollment_exception('S1',upcoming,self.academics,actor='root',
            reason='Owner-approved upcoming term exception',operation_id=str(uuid.uuid4()))
        context=self.db.enrollment_context('S1',occurred_at='2026-08-15T00:00:00Z')
        self.assertFalse(context['eligible'])
        self.assertEqual(context['status'],'Enrolled for upcoming term only')

    def test_admin_denied_and_reason_required(self):
        with self.assertRaises(PermissionError):self.approve(actor='staff')
        with self.assertRaises(ValueError):self.approve(reason='  ')
        with self.assertRaises(ValueError):self.approve(operation_id='not-a-uuid')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM enrollment_administrative_exceptions').fetchone()[0],0)

    def test_archived_pending_and_disabled_accounts_are_rejected(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE students SET is_archived=1 WHERE student_id='S1'")
            conn.execute("UPDATE students SET registration_pending=1 WHERE student_id='S2'")
            conn.execute("UPDATE student_accounts SET is_disabled=1 WHERE student_id='S3'")
        for sid in ('S1','S2','S3'):
            with self.subTest(student=sid),self.assertRaises(ValueError):self.approve(student=sid)

    def test_replay_is_idempotent_but_changed_payload_rejected(self):
        first=self.approve()
        replay=self.approve()
        self.assertEqual(replay['exception_id'],first['exception_id'])
        self.assertTrue(replay['replayed'])
        with self.assertRaises(ValueError):
            self.db.approve_enrollment_exception('S1',self.term,
                dict(self.academics,report_section='B'),actor='root',reason='Changed payload',
                operation_id=self.operation_id)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM enrollment_administrative_exceptions').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM academic_audit WHERE action=\'approve\'').fetchone()[0],1)

    def test_withdrawal_ends_future_exception_effect_but_preserves_prior_occurrence(self):
        self.approve()
        with patch('core.academic_calendar.utc_now',return_value=instant('2026-09-01T00:00:00Z')):
            with self.db.connect() as conn:
                enrollment_id=conn.execute('SELECT id FROM student_term_enrollments WHERE student_id=\'S1\'').fetchone()[0]
            self.db.correct_enrollment(enrollment_id,actor='root',reason='Student withdrew',
                                       status='withdrawn',academics=self.academics)
        self.assertTrue(self.db.enrollment_context('S1',occurred_at='2026-08-15T00:00:00Z')['eligible'])
        self.assertFalse(self.db.enrollment_context('S1',occurred_at='2026-09-02T00:00:00Z')['eligible'])
        history=self.db.term_history('S1')['enrollments'][0]
        roster=self.db.query_term_roster(semester_id=self.term)
        self.assertEqual(history['status'],'withdrawn')
        self.assertEqual(history['eligibility_method'],'administrative_exception')
        self.assertEqual(roster[0]['status'],'withdrawn')
        self.assertEqual(roster[0]['eligibility_method'],'administrative_exception')

    def test_later_camera_confirmation_takes_projection_precedence(self):
        self.approve()
        rng=np.random.default_rng(71)
        embedding=np.zeros(512,dtype=np.float32);embedding[0]=1
        captures=[FaceCapture(('S1',),('camera',i),time.monotonic(),(0,0,30,30),
            rng.integers(0,255,(30,30,3),dtype=np.uint8),embedding,
            bytes(rng.integers(0,255,200,dtype=np.uint8))) for i in range(3)]
        self.db.confirm_term_enrollment('S1',self.term,self.academics,captures,('S1',),actor='root')
        history=self.db.term_history('S1')['enrollments'][0]
        roster=self.db.query_term_roster(semester_id=self.term)[0]
        self.assertEqual(history['status'],'confirmed')
        self.assertEqual(history['eligibility_method'],'camera_capture')
        self.assertEqual(roster['status'],'confirmed')
        self.assertEqual(roster['eligibility_method'],'camera_capture')
        self.assertEqual(roster['capture'],'Capture recorded')

    def test_history_and_roster_disclose_exception_method_without_biometrics(self):
        self.approve()
        history=self.db.term_history('S1')['enrollments'][0]
        self.assertEqual(history['eligibility_method'],'administrative_exception')
        self.assertEqual(history['status'],'administrative_exception')
        self.assertNotIn('exception_reason',history)
        self.assertNotIn('exception_actor',history)
        serialized=json.dumps(history)
        self.assertNotIn('encoding',serialized)
        self.assertNotIn('photo',serialized)
        roster=self.db.query_term_roster(semester_id=self.term)
        self.assertEqual(roster[0]['status'],'administrative_exception')
        self.assertEqual(roster[0]['eligibility_method'],'administrative_exception')
        self.assertEqual(roster[0]['capture'],'Administrative exception — no camera capture')

    def test_targeted_migration_is_repeat_safe_on_existing_schema(self):
        with self.db.connect() as conn:
            migrate_enrollment_exceptions(conn)
            migrate_enrollment_exceptions(conn)
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM campus_schema_versions WHERE version='academic-enrollment-exception-v1'").fetchone()[0],1)
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrollment_administrative_exceptions'").fetchone())


if __name__=='__main__':unittest.main()
