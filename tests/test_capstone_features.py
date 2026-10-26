"""Synthetic evidence and disposable databases for the capstone additions."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from core.evidence_forensics import _metadata, check_photo_dates
from core.report_snapshot import create_snapshot
from core.uniform_references import save_reference, list_references
from core.trainer import ViolationTrainer
from database.db_manager import CBVMSDatabase
from database.in_person_appeals import migrate_in_person_appeals
from tests.term_fixture import verified_term


class PhotoDateTests(unittest.TestCase):
    def test_nested_phone_exif_is_read(self):
        class Phone:
            info = {}
            def getexif(self):
                class Exif(dict):
                    def get_ifd(self, tag):
                        return {36867: '2026:10:27 08:00:00', 36881: '+08:00'}
                return Exif({271: 'Synthetic Camera', 34665: 30})
        self.assertEqual(_metadata(Phone())['datetime_original'], '2026:10:27 08:00:00')

    def test_missing_metadata_is_inconclusive(self):
        result = check_photo_dates({}, '2026-10-27 00:00:00', '2026-10-27 00:01:00')
        self.assertEqual(result['status'], 'unavailable')

    def test_timezone_is_required_for_comparison(self):
        result = check_photo_dates({'datetime_original': '2026:10:27 08:00:00'}, None, None)
        self.assertEqual(result['status'], 'timezone_unknown')

    def test_extreme_capture_date_does_not_break_case_review(self):
        result = check_photo_dates({'datetime_original': '0001:01:01 00:00:00', 'offset_original': '+08:00'}, None, None)
        self.assertEqual(result['status'], 'review')
        self.assertNotIn('capture_utc', result)

    def test_offset_conversion_and_future_capture_review(self):
        metadata = {'datetime_original': '2026:10:27 08:10:00', 'offset_original': '+08:00'}
        result = check_photo_dates(metadata, '2026-10-27 00:00:00', '2026-10-27 00:01:00')
        self.assertEqual(result['status'], 'review')
        self.assertEqual(result['detection_difference_seconds'], 600)
        self.assertEqual(result['upload_difference_seconds'], 540)

    def test_invalid_dates_and_offsets_do_not_raise(self):
        self.assertEqual(check_photo_dates({'datetime_original': 'bad'}, None, None)['status'], 'review')
        result = check_photo_dates({'datetime_original': '2026:10:27 08:00:00', 'offset_original': '+25:00'}, None, None)
        self.assertEqual(result['status'], 'timezone_unknown')


class UniformReferenceTests(unittest.TestCase):
    def test_transparent_artwork_and_audit_hash_are_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'design.png'
            Image.new('RGBA', (700, 900), (0, 50, 255, 0)).save(source)
            out = save_reference(source, 'shirt', root=Path(temp) / 'library')
            with Image.open(out) as image:
                self.assertEqual(image.size, (700, 900))
                self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertTrue(out.with_suffix('.json').exists())
            self.assertEqual(list_references(root=Path(temp) / 'library'), [('shirt', out)])
            with self.assertRaises(ValueError):
                save_reference(source, '../escape', root=Path(temp) / 'library')

    def test_uniform_samples_keep_logo_resolution(self):
        with tempfile.TemporaryDirectory() as temp, patch('core.trainer.TRAIN_DIR', Path(temp)):
            trainer = ViolationTrainer()
            photo = np.zeros((900, 700, 3), dtype=np.uint8)
            sample = trainer.add_sample('uniform', 'correct_uniform', photo)
            with Image.open(sample) as image:
                self.assertEqual(image.size, (700, 900))
            earring = trainer.add_sample('earring', 'no_earring', photo)
            with Image.open(earring) as image:
                self.assertEqual(image.size, (224, 224))


class InPersonAppealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CBVMSDatabase(Path(self.temp.name) / 'test.db')
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('S1', 'Synthetic One', 'BSIT', '3A', b'', b'')
        self.db.insert_student('S2', 'Synthetic Two', 'BSIT', '3A', b'', b'')
        with self.db.connect() as conn:
            conn.execute("INSERT INTO users(username,password_hash) VALUES('reviewer','synthetic')")
        self.now = datetime(2099, 8, 4, tzinfo=timezone.utc)
        clock = patch('database.db_manager.utc_now', return_value=self.now)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.term = verified_term(self.db, 'S1', 2099)
        self.vid = self.db.log_violation('S1', 'Synthetic One', 'wrong_uniform', detected_at=self.now)

    def submit(self, **changes):
        args = dict(violation_id=self.vid, student_id='S1',
            reason='I attended the office to request review of this uniform detection.',
            meeting_notes='Student attended the office. Reviewed the detection photograph.', username='reviewer')
        args.update(changes)
        return self.db.record_in_person_appeal(**args)

    def test_pending_appeal_records_staff_and_meeting_without_photo(self):
        aid = self.submit()
        case = self.db.get_appeal_case(aid, username='reviewer')
        self.assertEqual(case['status'], 'pending')
        self.assertEqual(case['evidence'], [])
        self.assertEqual(case['in_person']['recorded_by'], 'reviewer')
        self.assertIn('Reviewed', case['in_person']['meeting_notes'])
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_unknown_staff_wrong_owner_and_duplicate_are_rejected(self):
        with self.assertRaises(PermissionError):
            self.submit(username='student-login')
        with self.assertRaises(ValueError):
            self.submit(student_id='S2')
        self.submit()
        with self.assertRaises(ValueError):
            self.submit()

    def test_student_scoped_database_cannot_impersonate_staff(self):
        self.db.student_session_ref = {'token': 'synthetic'}
        with self.assertRaises(PermissionError):
            self.submit()

    def test_expired_appeals_are_not_backdated(self):
        self.clock.return_value = self.now + timedelta(days=6)
        with self.assertRaises(ValueError):
            self.submit()

    def test_repeat_migration_preserves_records(self):
        aid = self.submit()
        with self.db.connect() as conn:
            migrate_in_person_appeals(conn)
            migrate_in_person_appeals(conn)
            self.assertEqual(conn.execute('SELECT appeal_id FROM in_person_appeals').fetchone()[0], aid)

    def test_face_to_face_appeal_uses_existing_decision_workflow(self):
        aid = self.submit()
        self.assertTrue(self.db.update_appeal_decision(aid, 'approved',
            'Office review confirmed a valid exemption.', decided_by='reviewer',
            decision_category_code='approval.valid_exemption'))
        self.assertEqual(self.db.get_appeal_case(aid, username='reviewer')['status'], 'approved')

    def test_new_report_filters_combine_with_semester(self):
        snap = create_snapshot(self.db, {'term_ids': [self.term]}, ['Violations'])
        row = snap.sections[0].rows[0]
        filters = {'term_ids': [self.term], 'categories': [row['category']],
                   'enrollment_statuses': [row['enrollment_status']]}
        self.assertEqual(create_snapshot(self.db, filters, ['Violations']).sections[0].record_count, 1)
        filters['categories'] = ['No matching category']
        self.assertEqual(create_snapshot(self.db, filters, ['Violations']).sections[0].record_count, 0)
        filters['categories'] = []
        self.assertEqual(create_snapshot(self.db, filters, ['Violations']).sections[0].record_count, 0)


if __name__ == '__main__':
    unittest.main()
