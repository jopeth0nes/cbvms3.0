"""Disposable tests for explicit permanent deletion and re-registration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.db_manager import CBVMSDatabase


class StudentDeletionTests(unittest.TestCase):
    def setUp(self):
        connection = patch('database.student_deletion.connection_configured', return_value=False)
        connection.start()
        self.addCleanup(connection.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CBVMSDatabase(Path(self.temp.name) / 'test.db')
        self.db.initialize(process_deadlines=False)
        self.token = self.db.create_staff_session('superadmin', 'superadmin123')
        self.admin = self.db.create_staff_session('admin', 'admin123')
        self.pk = self.db.insert_student('S1', 'Synthetic', 'BSIT', '3A', b'face', b'photo',
            account_username='synthetic', account_password='sample-password')
        self.other = self.db.insert_student('S2', 'Other', 'BSIT', '3A', b'other', b'other')
        self.db.recycle_student(self.pk, 'S1', self.token, 0, 'Test recycle', '00000000-0000-4000-8000-000000000001')

    def delete(self, **kwargs):
        return self.db.permanently_delete_student(self.pk, 'S1', kwargs.get('token', self.token),
            kwargs.get('version', 1), 'Test permanent deletion')

    def test_delete_frees_identity_and_account_and_isolates_other_student(self):
        self.assertTrue(self.db.student_id_exists('S1'))
        self.assertTrue(self.db.username_exists('synthetic'))
        self.assertTrue(self.delete())
        self.assertFalse(self.db.student_id_exists('S1'))
        self.assertFalse(self.db.username_exists('synthetic'))
        fresh = self.db.insert_student('S1', 'Synthetic', 'BSIT', '3A', b'face', b'photo',
            account_username='synthetic', account_password='sample-password')
        self.assertNotEqual(fresh, self.pk)
        self.assertGreater(self.db.get_student(fresh)['archive_version'], 1)
        self.assertFalse(self.db.is_student_active('S1', 0))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT encoding FROM students WHERE id=?', (self.other,)).fetchone()[0], b'other')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute("SELECT count(*) FROM student_archive_audit WHERE action='permanently_deleted'").fetchone()[0], 1)

    def test_denies_wrong_role_stale_selection_and_active_student(self):
        with self.assertRaises(PermissionError):
            self.delete(token=self.admin)
        with self.assertRaises(ValueError):
            self.delete(version=99)
        with self.assertRaises(ValueError):
            self.db.permanently_delete_student(self.other, 'S2', self.token, 0, 'test')
        with self.assertRaises(ValueError):
            self.db.permanently_delete_student(self.pk, 'OTHER', self.token, 1, 'test')
        self.assertTrue(self.db.student_id_exists('S1'))

    def test_cloud_history_blocks_local_identity_reuse(self):
        with self.db.connect() as conn:
            conn.execute("INSERT INTO campus_sync_identity(singleton,source_id,writer_id) VALUES(1,'synthetic-source','synthetic-writer')")
        with self.assertRaisesRegex(ValueError, 'cloud identity history'):
            self.delete()
        self.assertTrue(self.db.student_id_exists('S1'))

    def test_deletes_enrollment_attendance_and_guards_survive(self):
        import sqlite3
        with self.db.connect() as conn:
            term = conn.execute('SELECT id FROM academic_terms LIMIT 1').fetchone()[0]
            conn.execute("INSERT INTO student_term_enrollments(student_id,semester_id,college_department,course,report_year_level,report_section,status) VALUES('S1',?,'College of Computer Studies','Information Technology','3rd Year','A','draft')", (term,))
            conn.execute("INSERT INTO attendance(student_id,student_name,attendance_date,first_seen,last_seen) VALUES('S1','Synthetic','2026-01-01','08:00','08:00')")
        self.delete()
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_term_enrollments WHERE student_id='S1'").fetchone()[0], 0)
            guards = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            self.assertIn('attendance_events_immutable_delete', guards)
            self.assertIn('preserve_recycled_student_account_delete', guards)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_failure_rolls_back_deleted_account_and_restores_guards(self):
        with self.db.connect() as conn:
            conn.execute("CREATE TRIGGER test_fail BEFORE DELETE ON students BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(Exception):
            self.delete()
        self.assertTrue(self.db.username_exists('synthetic'))
        with self.db.connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='preserve_recycled_student_account_delete'").fetchone())

    def test_related_violation_evidence_and_face_capture_are_removed(self):
        with self.db.connect() as conn:
            term = conn.execute('SELECT id FROM academic_terms LIMIT 1').fetchone()[0]
            vid = conn.execute("INSERT INTO violations(student_id,student_name,violation_type) VALUES('S1','Synthetic','Wrong Uniform')").lastrowid
            aid = conn.execute("INSERT INTO appeals(violation_id,student_id,reason) VALUES(?,'S1','synthetic appeal')", (vid,)).lastrowid
            conn.execute("INSERT INTO in_person_appeals(appeal_id,recorded_by,meeting_notes,recorded_at) VALUES(?,'superadmin','test','2026-01-01')", (aid,))
            eid = conn.execute("INSERT INTO evidence_files(appeal_id,student_id,filename,file_type,file_data) VALUES(?,'S1','test.png','image',?)", (aid, b'synthetic')).lastrowid
            conn.execute("INSERT INTO evidence_forensics_runs(evidence_id,appeal_id,source_sha256,status,created_at) VALUES(?,?,?,'pending','2026-01-01')", (eid, aid, 'synthetic'))
            conn.execute("INSERT INTO violation_sms(violation_id) VALUES(?)", (vid,))
            conn.execute("INSERT INTO enrollment_captures(id,student_id,semester_id,encoding,photo,photo_sha256,provenance,captured_at,actor) VALUES('capture','S1',?,?,?,'synthetic','{}','2026-01-01','superadmin')", (term, b'face', b'photo'))
        self.delete()
        with self.db.connect() as conn:
            for table in ('violations','appeals','evidence_files','evidence_forensics_runs','in_person_appeals','violation_sms','enrollment_captures'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_confirmed_ui_action_refreshes_face_gallery(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from ui.enrollment import EnrollmentPanel
        student = dict(self.db.get_student(self.pk, actor_token=self.token))
        panel = SimpleNamespace(_resolve_selected_student=lambda: student,
            _actor_role='superadmin', _purge_task=MagicMock(busy=False),
            _restore_task=MagicMock(busy=False), _purge_btn=MagicMock(),
            _restore_btn=MagicMock(), _set_status=MagicMock(),
            _reload_students=MagicMock(), winfo_toplevel=lambda: None,
            database=self.db, actor_token=self.token, recognizer=MagicMock())
        panel._purge_task.run.side_effect = lambda work, done, failed: done(work())
        with patch('ui.enrollment.simpledialog.askstring', side_effect=['S1', 'Synthetic test']):
            EnrollmentPanel._purge_selected(panel)
        panel.recognizer.load_known_faces.assert_called_once()
        panel._reload_students.assert_called_once()
        self.assertFalse(self.db.student_id_exists('S1'))
