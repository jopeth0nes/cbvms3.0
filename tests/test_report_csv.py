import csv
import tempfile
import unittest
from pathlib import Path

from core.report_csv import ROSTER, read_csv, write_csv, live_rows, import_roster
from database.db_manager import CBVMSDatabase


class ReportCSVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = CBVMSDatabase(self.path / 'test.db')
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('00001', 'Original', 'BSIT', '3A', b'face', b'photo')
        self.row = dict(zip(ROSTER, ['00001', 'Updated', 'Computing', 'BSCS', 'B', '2nd Year']))

    def test_roster_roundtrip_preserves_identity_and_face_data(self):
        target = self.path / 'roster.csv'
        write_csv(target, 'Roster', [self.row])
        rows = read_csv(target, 'Roster')
        import_roster(self.db, rows)
        student = self.db.get_student_by_student_id('00001')
        self.assertEqual(student['encoding'], b'face')
        self.assertEqual(student['photo'], b'photo')
        self.assertEqual(student['year_and_section'], '2nd Year - B')
        self.assertEqual(live_rows(self.db, 'Roster'), [self.row])

    def test_duplicate_and_invalid_year_are_rejected(self):
        target = self.path / 'bad.csv'
        write_csv(target, 'Roster', [self.row, self.row])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            read_csv(target, 'Roster')
        write_csv(target, 'Roster', [dict(self.row, year_level='Unknown')])
        with self.assertRaisesRegex(ValueError, 'year_level'):
            read_csv(target, 'Roster')
        self.assertEqual(self.db.get_student_by_student_id('00001')['name'], 'Original')

    def test_discipline_export_reads_real_records_and_escapes_formulas(self):
        self.db.log_violation('00001', 'Original', 'wrong_uniform')
        rows = live_rows(self.db, 'Discipline')
        self.assertEqual(rows[0]['record_type'], 'Violation')
        self.assertEqual(rows[0]['section'], 'A')
        target = self.path / 'discipline.csv'
        rows[0]['reason'] = '=HYPERLINK("example")'
        write_csv(target, 'Discipline', rows)
        with target.open(encoding='utf-8-sig', newline='') as f:
            self.assertTrue(next(csv.DictReader(f))['reason'].startswith("'="))
        self.assertEqual(read_csv(target, 'Discipline')[0]['student_id'], '00001')

    def test_migration_is_idempotent_and_blank_classification_is_not_guessed(self):
        self.db.initialize(process_deadlines=False)
        with self.db.connect() as conn:
            conn.execute("UPDATE students SET year_and_section='Unknown'")
        row = live_rows(self.db, 'Roster')[0]
        self.assertEqual(row['section'], '')
        self.assertEqual(row['college_department'], '')
        self.assertEqual(row['year_level'], '')
