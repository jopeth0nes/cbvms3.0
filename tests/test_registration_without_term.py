"""Registration and later enrollment use isolated accounts and synthetic captures."""
import pickle
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from core.face_capture import FaceCapture
from core.cloud_sync import student_snapshot
from core.recognizer import FaceRecognizer
from database.db_manager import CBVMSDatabase


class RegistrationWithoutTermTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'registration.db')
        self.db.initialize(process_deadlines=False)
        self.academics = dict(college_department='College of Computer Studies', course='BSIT',
                              report_year_level='1st Year', report_section='A')
        self.identity = dict(student_id='S1', name='Synthetic Student', year_and_section='1A',
                             encoding=None, photo=None, account_password='synthetic-temporary',
                             **self.academics)

    def captures(self, seed=1, identity=0):
        rng = np.random.default_rng(seed)
        embedding = np.zeros(512, dtype=np.float32)
        embedding[identity] = 1
        return tuple(FaceCapture(('S1',), ('camera', seed * 10 + i), time.monotonic(),
                                 (0, 0, 30, 30), np.zeros((30, 30, 3), np.uint8),
                                 embedding, bytes(rng.integers(0, 255, 200, dtype=np.uint8)))
                     for i in range(3))

    def register(self, captures=None, actor='superadmin', identity=None):
        return self.db.register_student_with_capture(identity or self.identity,
            captures if captures is not None else self.captures(), ('S1',), actor=actor)

    def test_registers_face_and_login_without_confirming_any_term(self):
        captures = self.captures()
        self.register(captures)
        student = self.db.get_student_by_student_id('S1')
        self.assertEqual(student['photo'], captures[0].photo)
        self.assertEqual(len(pickle.loads(student['encoding'])), 3)
        self.assertFalse(student['registration_pending'])
        self.assertTrue(student_snapshot(self.db, student)['active'])
        account = self.db.verify_student_account('S1', 'synthetic-temporary')
        self.assertEqual(account['student_id'], 'S1')
        self.assertTrue(account['must_change_password'])
        self.assertFalse(self.db.enrollment_context('S1')['eligible'])
        self.assertEqual(self.db.term_history('S1')['enrollments'], [])
        recognizer = FaceRecognizer(self.db)
        self.assertEqual(recognizer._known[0][0]['student_id'], 'S1')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM enrollment_captures').fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM academic_audit WHERE action='register'").fetchone()[0], 1)

    def test_can_confirm_a_published_term_later_without_replacing_account(self):
        self.register()
        original = self.db.get_student_by_student_id('S1')['encoding']
        term = self.db.save_academic_term(actor='superadmin', semester_code='semester_1',
            school_year='2026-2027', start_date='2026-01-01', end_date='2099-12-31', publish=True)
        self.db.confirm_term_enrollment('S1', term, self.academics, self.captures(2), ('S1',), actor='superadmin')
        self.assertTrue(self.db.enrollment_context('S1')['eligible'])
        self.assertEqual(self.db.get_student_by_student_id('S1')['encoding'], original)
        self.assertIsNotNone(self.db.verify_student_account('S1', 'synthetic-temporary'))

    def test_rejects_staff_and_student_sessions(self):
        with self.assertRaises(PermissionError):
            self.register(actor='admin')
        self.db.insert_student_account('OTHER', 'synthetic-student', 'synthetic-other')
        with self.db.connect() as conn:
            conn.execute("UPDATE student_accounts SET must_change_password=0,first_login_completed_at=CURRENT_TIMESTAMP")
        account = self.db.verify_student_account('synthetic-student', 'synthetic-other')
        session = self.db.create_student_session(account)
        self.db.student_session_ref = {'token': session['session_token']}
        with self.assertRaises(PermissionError):
            self.register()
        self.db.student_session_ref = None
        self.assertFalse(self.db.student_id_exists('S1'))

    def test_rejects_missing_stale_and_mismatched_captures(self):
        with self.assertRaises(ValueError):
            self.register([])
        captures = self.captures()
        with patch('database.term_enrollment.time.monotonic', return_value=time.monotonic() + 601):
            with self.assertRaises(ValueError):
                self.register(captures)
        with self.assertRaises(ValueError):
            self.register(captures[:2] + (self.captures(2, identity=1)[2],))
        self.assertFalse(self.db.student_id_exists('S1'))

    def test_duplicate_identity_does_not_create_a_second_account(self):
        self.register()
        identity = dict(self.identity, student_id='S2')
        with self.assertRaisesRegex(ValueError, 'another permanent student'):
            self.register(self.captures(2), identity=identity)
        self.assertFalse(self.db.student_id_exists('S2'))
        self.assertIsNone(self.db.verify_student_account('S2', 'synthetic-temporary'))

    def test_account_conflict_rolls_back_details_and_face(self):
        self.db.insert_student_account('OTHER', 'S1', 'synthetic-other')
        with self.assertRaises(sqlite3.IntegrityError):
            self.register()
        self.assertFalse(self.db.student_id_exists('S1'))

    def test_existing_registration_cannot_be_overwritten(self):
        self.register()
        original = self.db.get_student_by_student_id('S1')['encoding']
        with self.assertRaises(ValueError):
            self.register(self.captures(2, identity=1))
        self.assertEqual(self.db.get_student_by_student_id('S1')['encoding'], original)


if __name__ == '__main__':
    unittest.main()
