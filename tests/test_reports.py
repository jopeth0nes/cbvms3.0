"""Attendance persistence and safe downloadable report regression coverage."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from database.db_manager import CBVMSDatabase
from core.attendance import utc_stamp, manila_date
from core.reports import report_html, write_csv, attendance_values


class ReportTests(unittest.TestCase):
    def test_campus_sightings_are_daily_and_preserve_qualified_observation_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            db = CBVMSDatabase(Path(folder) / "test.db")
            db.initialize()
            db.insert_student(student_id="S-01", name="Student One", course="BSIT",
                year_and_section="3A", encoding=b"", photo=b"", gender="Male")
            first = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
            self.assertFalse(db.record_attendance("unknown", observed_at=first))
            self.assertTrue(db.record_attendance("S-01", observed_at=first + timedelta(minutes=10)))
            db.record_attendance("S-01", observed_at=first)
            db.record_attendance("S-01", observed_at=first + timedelta(minutes=5))
            rows = db.get_attendance_report()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["first_seen"], utc_stamp(first))
            self.assertEqual(rows[0]["last_seen"], utc_stamp(first + timedelta(minutes=10)))
            day = manila_date(first)
            self.assertEqual(rows[0]["attendance_date"], day)
            self.assertEqual(len(db.get_attendance_report(day, day, "Information Technology")), 1)
            self.assertEqual(db.get_attendance_report(search="another student"), [])
            self.assertEqual(db.get_attendance_report("2099-01-01"), [])
            db.record_attendance("S-01", observed_at=first + timedelta(days=1))
            self.assertEqual(len(db.get_attendance_report()), 2)
            self.assertEqual(attendance_values(rows)[0][-1], 3)

    def test_print_report_escapes_content_and_handles_empty_results(self):
        html = report_html("Violation Report", ["Student"], [["<script>alert(1)</script>"]], "<filter>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("window.print()", html)
        self.assertIn("No records match", report_html("Attendance", ["Student"], []))

    def test_csv_preserves_unicode_commas_and_blocks_formulas(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.csv"
            write_csv(path, ["Student", "ID"], [["王, Student", "=1+1"]])
            with path.open(encoding="utf-8-sig", newline="") as source:
                rows = list(csv.reader(source))
            self.assertEqual(rows[1], ["王, Student", "'=1+1"])
