"""Catalog boundaries, atomic persistence, legacy snapshots and complete CSV validation."""
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.academics import (CATALOG, COLLEGES, COURSES, NEEDS_REVIEW, academic_values,
    display_academics, resolve_pair, normalize_year)
from core.report_csv import import_roster, read_csv, write_csv, ROSTER
from database.academic_migration import migrate_academics
from database.db_manager import CBVMSDatabase


class AcademicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.db'
        self.db = CBVMSDatabase(self.path)
        self.db.initialize(process_deadlines=False)

    def insert(self, sid, college, course, year='Not applicable', **kwargs):
        return self.db.insert_student(sid, 'Student', course, '', b'face', b'photo',
            college_department=college, report_year_level=year, report_section='A', **kwargs)

    def test_all_25_programs_and_all_150_incompatible_pairs_at_persistence_boundary(self):
        self.assertEqual(len(COURSES), 25)
        for index, (college, course) in enumerate(COURSES.values()):
            sid = f'000-{index:03d}/A'
            with self.subTest(course=course):
                self.insert(sid, college, course)
                for other in COLLEGES:
                    if other != college:
                        with self.assertRaises(ValueError):
                            self.insert('invalid', other, course)
                stored = self.db.get_student_by_student_id(sid)
                self.assertEqual((stored['college_department'], stored['course']), (college, course))
                self.assertEqual(stored['report_year_level'], 'Not applicable')
        restarted = CBVMSDatabase(self.path)
        restarted.initialize(process_deadlines=False)
        self.assertEqual(len(restarted.get_all_students()), 25)
        self.assertFalse(restarted.student_id_exists('invalid'))

    def test_aliases_identifiers_and_years_have_no_duration_assumptions(self):
        self.assertEqual(resolve_pair('', 'BSIT'), COURSES['it'])
        self.assertEqual(resolve_pair('ccs', 'it'), COURSES['it'])
        self.assertIsNone(resolve_pair('', 'Unrecognized IT'))
        self.assertIsNone(resolve_pair('College of Accountancy', 'BSIT'))
        self.assertEqual(normalize_year('7'), '7th Year')
        self.assertEqual(normalize_year('11'), '11th Year')
        self.assertEqual(normalize_year('21'), '21st Year')
        for year in ('0', '-1', 'Unknown', '', 'Select year level'):
            with self.assertRaises(ValueError):
                normalize_year(year)

    def test_atomic_enrollment_and_edit_preserve_account_face_id_and_discipline(self):
        sid = '0001-02/A'
        self.insert(sid, *COURSES['it'], account_password='fixture-password')
        vid = self.db.log_violation(sid, 'Student', 'wrong_uniform')
        with self.db.connect() as conn:
            account = tuple(conn.execute('SELECT * FROM student_accounts').fetchone())
            violation = tuple(conn.execute('SELECT * FROM violations WHERE id=?', (vid,)).fetchone())
        self.db.update_student_details(sid, student_status='Enrolled', contacts={}, changed_by='admin',
            academics=academic_values(*COURSES['nursing'], '5', 'B'))
        row = self.db.get_student_by_student_id(sid)
        self.assertEqual(row['encoding'], b'face')
        self.assertEqual(row['photo'], b'photo')
        self.assertEqual(row['year_and_section'], '5th Year - B')
        with self.db.connect() as conn:
            self.assertEqual(tuple(conn.execute('SELECT * FROM student_accounts').fetchone()), account)
            self.assertEqual(tuple(conn.execute('SELECT * FROM violations WHERE id=?', (vid,)).fetchone()), violation)
        with self.assertRaises(ValueError):
            self.db.update_student_details(sid, student_status='Enrolled', contacts={'email': 'test@example.com'},
                changed_by='admin', academics={'college_department': COLLEGES[1]})
        self.assertEqual(self.db.get_student_by_student_id(sid)['email'], '')
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert('new', *COURSES['it'], account_username=sid, account_password='fixture-password')
        self.assertFalse(self.db.student_id_exists('new'))

    def test_legacy_migration_preserves_exact_raw_and_unknown_editability_and_repeats(self):
        with self.db.connect() as conn:
            conn.execute('DELETE FROM academic_migrations')  # Disposable pre-Step-1 fixture only.
            for sid, college, course, year in [('0001', '', ' BSIT ', '3A'),
                    ('0002', 'Old College', 'Mystery', 'special'),
                    ('0003', COLLEGES[1], 'BSIT', '1A')]:
                conn.execute('INSERT INTO students(student_id,name,college_department,course,year_and_section,encoding,photo) VALUES(?,?,?,?,?,?,?)',
                    (sid, 'Old', college, course, year, b'oldface', b'oldphoto'))
            migrate_academics(conn)
            before = [tuple(r) for r in conn.execute('SELECT * FROM students')]
            migrate_academics(conn)
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM students')], before)
            raw = conn.execute("SELECT * FROM student_academic_legacy WHERE student_id='0001'").fetchone()
            self.assertEqual(raw['course'], ' BSIT ')
            self.assertEqual(raw['year_and_section'], '3A')
        known = self.db.get_student_by_student_id('0001')
        self.assertEqual(known['course'], 'Information Technology')
        self.assertEqual(known['report_section'], 'A')
        for sid in ('0002', '0003'):
            old = self.db.get_student_by_student_id(sid)
            self.assertEqual(display_academics(old)['course'], NEEDS_REVIEW)
            self.db.update_student_details(sid, student_status='Enrolled', contacts={'email': 'old@example.com'}, changed_by='admin')
            new = self.db.get_student_by_student_id(sid)
            for key in ('course', 'college_department', 'year_and_section', 'photo', 'encoding'):
                self.assertEqual(new[key], old[key])
        self.db.initialize(process_deadlines=False)
        self.assertEqual(self.db.get_student_by_student_id('0001')['encoding'], b'oldface')

    def test_csv_validates_every_row_before_any_write_including_direct_callers(self):
        good = dict(zip(ROSTER, ['000-01/A', 'New', *COURSES['it'], 'A', 'Not applicable']))
        bad = dict(good, student_id='000-02', college_department=COLLEGES[1])
        path = Path(self.tmp.name) / 'import.csv'
        write_csv(path, 'Roster', [good, bad])
        with self.assertRaisesRegex(ValueError, 'Line 3:'):
            read_csv(path, 'Roster')
        with self.assertRaisesRegex(ValueError, 'Line 3:'):
            import_roster(self.db, [good, bad])
        self.assertEqual(self.db.get_all_students(), [])
        for college, course in COURSES.values():
            row = dict(good, college_department=college, course=course)
            write_csv(path, 'Roster', [row])
            import_roster(self.db, read_csv(path, 'Roster'))
            self.assertEqual(self.db.get_student_by_student_id('000-01/A')['course'], course)

    def test_csv_roundtrip_preserves_leading_zeros_and_punctuation(self):
        path = Path(self.tmp.name) / 'ids.csv'
        for sid in ('000-01/A', '-0001', '+001/A', "'001", '@001', '=001'):
            row = dict(zip(ROSTER, [sid, 'Name', *COURSES['it'], 'A', '1st Year']))
            write_csv(path, 'Roster', [row])
            self.assertEqual(read_csv(path, 'Roster')[0]['student_id'], sid)
            import_roster(self.db, read_csv(path, 'Roster'))
            self.assertIsNotNone(self.db.get_student_by_student_id(sid))

    def test_pre_report_schema_migrates_and_structured_fields_are_authoritative(self):
        path = Path(self.tmp.name) / 'legacy.db'
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE students(id INTEGER PRIMARY KEY, student_id TEXT UNIQUE, name TEXT, course TEXT, year_level TEXT, encoding BLOB, photo BLOB, enrolled_at TEXT)')
            conn.execute("INSERT INTO students VALUES(1,'0001','Old','BSIT','5A',X'0102',X'0304','2020-01-01')")
        db = CBVMSDatabase(path)
        db.initialize(process_deadlines=False)
        db.initialize(process_deadlines=False)
        row = db.get_student_by_student_id('0001')
        self.assertEqual(row['year_and_section'], '5th Year - A')
        self.assertEqual(row['encoding'], b'\x01\x02')
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT year_and_section FROM student_academic_legacy').fetchone()[0], '5A')
        with self.db.connect() as conn:
            conn.execute('DELETE FROM academic_migrations')
            conn.execute("INSERT INTO students(student_id,name,course,year_and_section,report_year_level,report_section) VALUES('0001','Old','BSIT','3A','2nd Year','B')")
            migrate_academics(conn)
        row = self.db.get_student_by_student_id('0001')
        self.assertEqual(row['year_and_section'], '2nd Year - B')



if __name__ == '__main__':
    unittest.main()
