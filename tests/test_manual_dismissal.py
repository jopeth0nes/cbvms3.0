import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from core.discipline import utc_now
from database.db_manager import CBVMSDatabase


class ManualDismissalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'test.db')
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('001', 'Student', 'BSIT', '3A', b'', b'')
        with self.db.connect() as conn:
            conn.execute("INSERT INTO users(username,password_hash,role) VALUES('reviewer','unused','admin')")
        self.vid = self.db.log_violation('001', 'Student', 'wrong_uniform', status='confirmed')

    def test_final_strike_removed_and_audit_retained_without_appeal(self):
        self.db.process_expired_deadlines(now=utc_now() + timedelta(days=6))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT is_active FROM strikes WHERE violation_id=?', (self.vid,)).fetchone()[0], 1)
        self.assertTrue(self.db.dismiss_violation(self.vid, decided_by='reviewer', reason='Verified exception', manual=True))
        with self.db.connect() as conn:
            row = conn.execute('SELECT status,reviewed_by,dismissal_reason FROM violations WHERE id=?', (self.vid,)).fetchone()
            self.assertEqual(tuple(row), ('dismissed', 'reviewer', 'Verified exception'))
            self.assertEqual(conn.execute('SELECT is_active FROM strikes WHERE violation_id=?', (self.vid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM appeals').fetchone()[0], 0)

    def test_requires_reason_and_authenticated_admin(self):
        with self.assertRaises(ValueError):
            self.db.dismiss_violation(self.vid, decided_by='reviewer', manual=True)
        with self.assertRaises(PermissionError):
            self.db.dismiss_violation(self.vid, decided_by='missing', reason='Exception', manual=True)

    def test_submitted_appeal_uses_dedicated_review(self):
        with self.db.connect() as conn:
            conn.execute('INSERT INTO appeals(violation_id,student_id,reason) VALUES(?,?,?)', (self.vid, '001', 'Review'))
        self.assertFalse(self.db.dismiss_violation(self.vid, decided_by='reviewer', reason='Exception', manual=True))
