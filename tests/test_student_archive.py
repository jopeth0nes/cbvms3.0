"""Disposable SQLite coverage for authenticated student archival."""
import sqlite3
import json
import tempfile
import unittest
from pathlib import Path

from auth.auth_manager import AuthManager
from database.db_manager import CBVMSDatabase
from database.student_credentials import SessionExpired
from tests.evidence_fixture import picture_evidence
from tests.term_fixture import verified_term
from core.report_snapshot import create_snapshot


class StudentArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'archive.db')
        self.db.initialize(process_deadlines=False)
        auth = AuthManager(self.db)
        self.staff = auth.authenticate('superadmin', 'superadmin123')
        self.assertIsNotNone(self.staff)
        self.token = self.staff['staff_session_token']
        self.admin_token = auth.authenticate('admin', 'admin123')['staff_session_token']

    def test_reproduces_fk_delete_and_rollback_restores_student_account(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        self.db.insert_student_account('S1', 'student-one', 'sample-password')
        with self.db.connect() as conn:
            term = conn.execute("SELECT id FROM academic_terms LIMIT 1").fetchone()['id']
            conn.execute("INSERT INTO student_term_enrollments(student_id,semester_id,college_department,course,report_year_level,report_section,status) VALUES('S1',?,'College of Computer Studies','Information Technology','3rd Year','A','draft')", (term,))
            conn.commit()
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("DELETE FROM student_accounts WHERE student_id='S1'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
                conn.execute('DELETE FROM students WHERE id=?', (pk,))
            conn.rollback()
            self.assertIsNotNone(conn.execute("SELECT 1 FROM student_accounts WHERE student_id='S1'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM students WHERE id=?", (pk,)).fetchone())

    def test_legacy_archive_schema_upgrades_repeatably_without_authorizing_old_operations(self):
        path = Path(self.tmp.name) / 'legacy-archive.db'
        legacy = CBVMSDatabase(path)
        legacy.initialize(process_deadlines=False)
        old_pk = legacy.insert_student('OLD', 'Old Student', 'BSIT', '1A', b'', b'')
        with legacy.connect() as conn:
            conn.execute('UPDATE students SET is_archived=1,archive_version=1 WHERE id=?', (old_pk,))
            conn.execute('DROP TRIGGER immutable_student_archive_audit_update')
            conn.execute('DROP TRIGGER immutable_student_archive_audit_delete')
            conn.execute('DROP TRIGGER immutable_student_lifecycle_operation')
            conn.execute('DROP TRIGGER immutable_student_lifecycle_operation_delete')
            conn.execute('ALTER TABLE student_archive_operations RENAME TO archive_operations_new')
            conn.execute('''CREATE TABLE student_archive_operations (
                operation_id TEXT PRIMARY KEY,student_id TEXT NOT NULL,expected_version INTEGER NOT NULL,
                state TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
            conn.execute("INSERT INTO student_archive_operations VALUES('legacy-op','OLD',1,'pending',NULL,'t','t')")
            conn.execute('DROP TABLE archive_operations_new')
            conn.execute('ALTER TABLE student_archive_audit RENAME TO archive_audit_new')
            conn.execute('''CREATE TABLE student_archive_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,student_id TEXT NOT NULL,operation_id TEXT NOT NULL,
                actor TEXT NOT NULL,action TEXT NOT NULL,occurred_at TEXT NOT NULL)''')
            conn.execute("INSERT INTO student_archive_audit(student_id,operation_id,actor,action,occurred_at) VALUES('OLD','legacy-op','admin','archived','t')")
            conn.execute('DROP TABLE archive_audit_new')
            conn.commit()
        legacy.initialize(process_deadlines=False)
        legacy.initialize(process_deadlines=False)
        with legacy.connect() as conn:
            operation_columns = {row['name'] for row in conn.execute('PRAGMA table_info(student_archive_operations)')}
            audit_columns = {row['name'] for row in conn.execute('PRAGMA table_info(student_archive_audit)')}
        self.assertTrue({'operation_type','lifecycle_version','actor_id','actor_role','reason','account_id','account_username'} <= operation_columns)
        self.assertTrue({'actor_id','actor_role','operation_type','lifecycle_version','reason','account_id','account_username'} <= audit_columns)
        self.assertIsNone(legacy.get_lifecycle_operation_for_provider('legacy-op'))

    def test_archive_preserves_history_disables_login_revokes_sessions_and_queues_cloud(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        self.db.insert_student_account('S1', 'student-one', 'sample-password')
        account = self.db.verify_student_account('student-one', 'sample-password')
        student_session = self.db.create_student_session(account)['session_token']
        self.db.update_student_details('S1', student_status='Graduate', contacts={},
                                       changed_by='admin', reason='Historical test status')
        with self.db.connect() as conn:
            term = conn.execute("SELECT id FROM academic_terms LIMIT 1").fetchone()['id']
            conn.execute("INSERT INTO student_term_enrollments(student_id,semester_id,college_department,course,report_year_level,report_section,status) VALUES('S1',?,'College of Computer Studies','Information Technology','3rd Year','A','draft')", (term,))
            conn.execute("INSERT INTO attendance(student_id,student_name,attendance_date,first_seen,last_seen,sighting_count,legacy_summary) VALUES('S1','Student One','2026-01-01','08:00','08:00',1,1)")
            conn.execute("INSERT INTO student_suspensions(student_id,reason,starts_at,imposed_by,imposed_at) VALUES('S1','historical','2026-01-01','admin','2026-01-01')")
        result = self.db.archive_student(pk, 'S1', self.token, 0)
        self.assertEqual(result['state'], 'pending')
        self.assertEqual(self.db.get_archive_operation(self.token, result['operation_id'])['state'], 'pending')
        self.assertEqual(len(self.db.get_all_students()), 0)
        self.assertEqual(len(self.db.get_all_students(include_archived=True, actor_token=self.token)), 1)
        self.assertEqual(self.db.get_management_students(self.token), [])
        archived = self.db.get_management_students(self.token, include_archived=True)
        self.assertEqual(archived[0]['student_id'], 'S1')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_term_enrollments WHERE student_id='S1'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attendance WHERE student_id='S1'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_suspensions WHERE student_id='S1'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT is_disabled FROM student_accounts WHERE student_id='S1'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_sessions").fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertIsNone(self.db.verify_student_account('student-one', 'sample-password'))
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(student_session)
        self.assertFalse(self.db.record_attendance('S1'))
        self.assertFalse(self.db.record_premises_entry('S1'))
        self.assertIsNone(self.db.log_violation('S1', 'Student One', 'wrong_uniform'))
        self.assertIsNone(self.db.record_enrollment_review('S1', observed_at='2026-01-01T00:00:00Z',
            source_id='queued-camera-review', snapshot=b'old frame'))
        reopened = CBVMSDatabase(self.db.db_path)
        reopened.initialize(process_deadlines=False)
        self.assertEqual(reopened.get_archive_operation(self.token, result['operation_id'])['state'], 'pending')
        self.assertEqual(reopened.get_student(pk, actor_token=self.token)['is_archived'], 1)
        self.assertTrue(self.db.set_archive_cloud_result(result['operation_id'], 'verified'))
        self.assertEqual(self.db.get_archive_operation(self.token, result['operation_id'])['state'], 'verified')

    def test_actor_and_stale_identity_are_required_and_history_remains_archive_only(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        with self.assertRaises(PermissionError):
            self.db.create_staff_session('admin', 'wrong-password')
        self.db.insert_student_account('S1', 'student-user', 'student-password')
        with self.assertRaises(PermissionError):
            self.db.create_staff_session('student-user', 'student-password')
        with self.assertRaises(PermissionError):
            self.db.archive_student(pk, 'S1', 'forged-token')
        with self.assertRaises(PermissionError):
            self.db.archive_student(pk, 'S1', self.admin_token)
        with self.assertRaises(PermissionError):
            self.db.get_management_students(self.admin_token, include_archived=True)
        with self.assertRaises(PermissionError):
            self.db.get_recycle_bin(self.admin_token)
        with self.assertRaises(PermissionError):
            self.db.get_archive_operation(self.admin_token, 'missing')
        with self.assertRaises(ValueError):
            self.db.archive_student(pk, 'OTHER', self.token)
        with self.assertRaises(ValueError):
            self.db.archive_student(pk, 'S1', self.token, expected_version=42)
        with self.db.connect() as conn:
            conn.execute("INSERT INTO attendance(student_id,student_name,attendance_date,first_seen,last_seen,sighting_count,legacy_summary) VALUES('S1','Student One','2026-01-01','08:00','08:00',1,1)")
        with self.assertRaisesRegex(ValueError, 'no trusted cloud identity provenance'):
            self.db.permanently_delete_unused_student(pk, 'S1', self.token)
        request_id = '00000000-0000-4000-8000-000000000001'
        operation = self.db.archive_student(pk, 'S1', self.token, 0, operation_id=request_id)
        self.assertEqual(operation['state'], 'pending')
        repeated = self.db.archive_student(pk, 'S1', self.token, 0, operation_id=request_id)
        self.assertEqual(repeated['operation_id'], operation['operation_id'])
        self.assertIsNotNone(self.db.get_student(pk, actor_token=self.token))
        with self.assertRaises(PermissionError):
            self.db.get_student(pk)
        self.assertIsNone(self.db.get_student_by_student_id('S1'))
        with self.assertRaises(PermissionError):
            self.db.get_all_students(include_archived=True, actor_token=self.admin_token)

    def test_staff_password_change_invalidates_the_actor_proof(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        with self.db.connect() as conn:
            conn.execute("UPDATE users SET password_hash='replaced' WHERE username='superadmin'")
        with self.assertRaises(PermissionError):
            self.db.archive_student(pk, 'S1', self.token)

    def test_preverified_student_cannot_mint_session_after_archive_commits(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        self.db.insert_student_account('S1', 'student-one', 'sample-password')
        preverified = self.db.verify_student_account('student-one', 'sample-password')
        self.assertIsNotNone(preverified)
        self.db.archive_student(pk, 'S1', self.token)
        self.assertIsNone(self.db.verify_student_account('student-one', 'sample-password'))
        with self.assertRaises(SessionExpired):
            self.db.create_student_session(preverified)

    def test_superadmin_recycle_restore_is_versioned_preserves_restrictions_and_requires_fresh_login(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        self.db.insert_student_account('S1', 'student-one', 'sample-password')
        account = self.db.verify_student_account('student-one', 'sample-password')
        old_session = self.db.create_student_session(account)['session_token']
        before_enrollments = self.db.enrollment_context('S1')['enrollment']
        recycle_id = '00000000-0000-4000-8000-000000000002'
        recycled = self.db.recycle_student(pk, 'S1', self.token, 0, 'Student left program', recycle_id)
        self.assertEqual((recycled['expected_version'], recycled['lifecycle_version']), (0, 1))
        self.assertEqual(recycled['operation_type'], 'recycle')
        with self.assertRaises(PermissionError):
            self.db.restore_student(pk, 'S1', self.admin_token, 1, 'Return', '00000000-0000-4000-8000-000000000003')
        self.assertIsNone(self.db.verify_student_account('student-one', 'sample-password'))
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(old_session)
        restored = self.db.restore_student(pk, 'S1', self.token, 1, 'Returned to campus', '00000000-0000-4000-8000-000000000004')
        self.assertEqual((restored['expected_version'], restored['lifecycle_version']), (1, 2))
        self.assertEqual(restored['operation_type'], 'restore')
        self.assertFalse(self.db.get_student(pk)['is_archived'])
        self.assertEqual(self.db.get_student(pk)['archive_version'], 2)
        self.assertEqual(self.db.enrollment_context('S1')['enrollment'], before_enrollments)
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(old_session)
        fresh = self.db.verify_student_account('student-one', 'sample-password')
        self.assertIsNotNone(fresh)
        new_session = self.db.create_student_session(fresh)['session_token']
        self.assertIsNotNone(self.db.get_student_session(new_session))
        self.assertFalse(self.db.is_student_active('S1', 1))
        self.assertTrue(self.db.is_student_active('S1', 2))
        queue = self.db.list_pending_lifecycle_operations()
        self.assertEqual([row['lifecycle_version'] for row in queue], [1, 2])
        proof = self.db.get_lifecycle_operation_for_provider(recycled['operation_id'])
        self.assertEqual((proof['actor_id'], proof['actor_role'], proof['reason']),
                         (self.db.get_staff_session_info(self.token)['id'], 'superadmin', 'Student left program'))
        self.assertEqual(self.db.get_recycle_bin(self.token)['total'], 0)
        with self.db.connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'lifecycle operation identity is immutable'):
                conn.execute("UPDATE student_archive_operations SET reason='tampered' WHERE operation_id=?",
                             (recycled['operation_id'],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'archive audit is immutable'):
                conn.execute("UPDATE student_archive_audit SET reason='tampered' WHERE operation_id=?",
                             (recycled['operation_id'],))
        self.assertTrue(self.db.acknowledge_lifecycle(recycled['operation_id'], 'verified'))
        self.assertTrue(self.db.acknowledge_lifecycle(recycled['operation_id'], 'failed', 'late failure'))
        self.assertEqual(self.db.get_archive_operation(self.token, recycled['operation_id'])['state'], 'verified')

    def test_restore_never_clears_independent_account_disable_and_stale_lifecycle_is_rejected(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        self.db.insert_student_account('S1', 'student-one', 'sample-password')
        with self.db.connect() as conn:
            conn.execute("UPDATE student_accounts SET is_disabled=1,disabled_at='admin-block' WHERE student_id='S1'")
        recycled = self.db.recycle_student(pk, 'S1', self.token, 0, 'Lifecycle test', '00000000-0000-4000-8000-000000000005')
        with self.assertRaises(ValueError):
            self.db.restore_student(pk, 'S1', self.token, 0, 'stale', '00000000-0000-4000-8000-000000000006')
        self.db.restore_student(pk, 'S1', self.token, recycled['lifecycle_version'],
                                'Restriction remains', '00000000-0000-4000-8000-000000000007')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT is_disabled,disabled_at FROM student_accounts WHERE student_id='S1'").fetchone()[:],
                             (1, 'admin-block'))
        self.assertIsNone(self.db.verify_student_account('student-one', 'sample-password'))

    def test_recycled_account_mapping_cannot_be_deleted_reassigned_or_restored_to_a_conflict(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        self.db.insert_student_account('S1', 'student-one', 'sample-password')
        recycled = self.db.recycle_student(pk, 'S1', self.token, 0, 'Identity test',
            '00000000-0000-4000-8000-000000000008')
        with self.db.connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'in the Recycle Bin'):
                conn.execute("DELETE FROM student_accounts WHERE student_id='S1'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'in the Recycle Bin'):
                conn.execute("UPDATE student_accounts SET username='replacement' WHERE student_id='S1'")
            conn.execute('DROP TRIGGER preserve_recycled_student_account_identity')
            conn.execute("UPDATE student_accounts SET username='replacement' WHERE student_id='S1'")
        with self.assertRaisesRegex(ValueError, 'account identity changed'):
            self.db.restore_student(pk, 'S1', self.token, recycled['lifecycle_version'], 'Conflict',
                                    '00000000-0000-4000-8000-000000000009')
        self.assertEqual(self.db.get_student(pk, actor_token=self.token)['archive_version'], 1)

    def test_local_archive_rollback_keeps_student_account_and_sessions_usable(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        self.db.insert_student_account('S1', 'student-one', 'sample-password')
        account = self.db.verify_student_account('student-one', 'sample-password')
        session = self.db.create_student_session(account)['session_token']
        with self.db.connect() as conn:
            conn.execute("""CREATE TRIGGER fail_archive_audit BEFORE INSERT ON student_archive_audit
                BEGIN SELECT RAISE(ABORT,'synthetic archive rollback'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic archive rollback'):
            self.db.archive_student(pk, 'S1', self.token)
        self.assertEqual(self.db.get_student(pk)['is_archived'], 0)
        self.assertIsNotNone(self.db.verify_student_account('student-one', 'sample-password'))
        self.assertEqual(self.db.get_student_session(session)['student_id'], 'S1')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM student_archive_operations').fetchone()[0], 0)

    def test_unused_record_moves_to_recycle_bin_without_delete(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        operation = self.db.archive_student(pk, 'S1', self.token)
        self.assertEqual(operation['state'], 'pending')
        self.assertEqual(self.db.get_student(pk, actor_token=self.token)['is_archived'], 1)
        with self.assertRaisesRegex(ValueError, 'cloud identity provenance'):
            self.db.delete_student(pk)

    def test_full_semester_discipline_appeal_and_evidence_history_is_unchanged(self):
        pk = self.db.insert_student('S1', 'Student One', 'BSIT', '3A', b'', b'')
        term = verified_term(self.db, 'S1', year=2026)
        violation_id = self.db.log_violation('S1', 'Student One', 'wrong_uniform')
        self.assertIsNotNone(violation_id)
        appeal_id = self.db.insert_appeal(violation_id, 'S1',
            'Please review the preserved historical evidence and decision.', evidence=picture_evidence())
        self.assertIsNotNone(appeal_id)
        self.assertTrue(self.db.update_appeal_decision(appeal_id, 'rejected', 'Evidence reviewed and confirmed.',
            decided_by='admin', decision_category_code='rejection.violation_confirmed'))
        self.db.update_student_details('S1', student_status='Graduate', contacts={}, changed_by='admin',
                                       reason='Synthetic historical status')
        suspension_id = self.db.impose_suspension('S1', reason='Synthetic historical suspension',
            starts_at='2026-10-01T00:00:00Z', ends_at='2026-10-02T00:00:00Z', imposed_by='admin')
        with self.db.connect() as conn:
            enrollment = conn.execute("SELECT * FROM student_term_enrollments WHERE student_id='S1'").fetchone()
            conn.execute("INSERT INTO enrollment_revisions(enrollment_id,student_id,semester_id,effective_at,snapshot) VALUES(?,?,?,?,?)",
                (enrollment['id'], 'S1', term, '2026-01-02T00:00:00Z', json.dumps(dict(enrollment))))
            review_id = conn.execute("""INSERT INTO enrollment_reviews
                (student_id,semester_id,enrollment_status,observed_at,last_seen,source_id,snapshot)
                VALUES('S1',?,'Unverified','2026-03-01T00:00:00Z','2026-03-01T00:00:00Z','fixture-review',X'01')""",
                (term,)).lastrowid
            conn.execute("INSERT INTO enrollment_review_events(review_id,observed_at,snapshot) VALUES(?,?,X'01')",
                         (review_id, '2026-03-01T00:00:00Z'))
            conn.execute("""INSERT INTO academic_audit(entity,entity_id,action,actor,reason,created_at)
                VALUES('enrollment','S1','fixture','admin','preserve history','2026-03-01T00:00:00Z')""")
            conn.execute("""INSERT INTO strike_events(student_id,violation_code,semester_id,triggered_at)
                VALUES('S1','wrong_uniform',?,'2026-03-01T00:00:00Z')""", (term,))
            conn.commit()
        self.assertIsNotNone(suspension_id)

        queries = {
            'enrollment_captures': "SELECT * FROM enrollment_captures WHERE student_id='S1' ORDER BY id",
            'student_term_enrollments': "SELECT * FROM student_term_enrollments WHERE student_id='S1' ORDER BY id",
            'enrollment_revisions': "SELECT * FROM enrollment_revisions WHERE student_id='S1' ORDER BY id",
            'enrollment_reviews': "SELECT * FROM enrollment_reviews WHERE student_id='S1' ORDER BY id",
            'enrollment_review_events': "SELECT * FROM enrollment_review_events WHERE review_id IN (SELECT id FROM enrollment_reviews WHERE student_id='S1') ORDER BY id",
            'violations': "SELECT * FROM violations WHERE student_id='S1' ORDER BY id",
            'appeals': "SELECT * FROM appeals WHERE student_id='S1' ORDER BY id",
            'evidence_files': "SELECT * FROM evidence_files WHERE student_id='S1' ORDER BY id",
            'strikes': "SELECT * FROM strikes WHERE student_id='S1' ORDER BY id",
            'strike_events': "SELECT * FROM strike_events WHERE student_id='S1' ORDER BY id",
            'student_suspensions': "SELECT * FROM student_suspensions WHERE student_id='S1' ORDER BY id",
            'decision_history': "SELECT * FROM decision_history WHERE student_id='S1' ORDER BY id",
            'student_status_history': "SELECT * FROM student_status_history WHERE student_id='S1' ORDER BY id",
            'academic_audit': "SELECT * FROM academic_audit WHERE entity_id='S1' ORDER BY id",
        }
        with self.db.connect() as conn:
            before = {table: [tuple(row) for row in conn.execute(query)] for table, query in queries.items()}
        report_before = create_snapshot(self.db, {'term_ids': [term]},
                                        ['Semester Enrollment Roster']).sections[0].rows
        operation = self.db.archive_student(pk, 'S1', self.token)
        with self.db.connect() as conn:
            after = {table: [tuple(row) for row in conn.execute(query)] for table, query in queries.items()}
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        report_after = create_snapshot(self.db, {'term_ids': [term]},
                                       ['Semester Enrollment Roster']).sections[0].rows
        self.assertEqual(after, before)
        self.assertEqual(report_after, report_before)
        self.assertEqual(operation['state'], 'pending')


if __name__ == '__main__':
    unittest.main()
