import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import sms_sender
from database import sms_notifications as sms
from database.db_manager import CBVMSDatabase


class SmsTests(unittest.TestCase):
    def test_setup_detects_invalid_token_without_sending_sms(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        opener = MagicMock()
        opener.open.return_value = response
        with patch('urllib.request.build_opener',return_value=opener):
            response.__enter__.return_value.read.return_value = b'{"status":500,"message":"Invalid Token"}'
            valid, reason = sms_sender.check_token('synthetic-token')
            self.assertFalse(valid)
            self.assertIn('invalid',reason)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_method(),'GET')
            self.assertTrue(request.full_url.endswith('/account/sms_credits'))
            self.assertNotIn('synthetic-token',request.full_url)
            response.__enter__.return_value.read.return_value = b'{"status":"success","data":{"load_balance":4}}'
            self.assertTrue(sms_sender.check_token('synthetic-token')[0])

    def test_phone_formats(self):
        for value in ('09171234567', '+63 917 123 4567', '639171234567'):
            self.assertEqual(sms_sender.normalize_phone(value), '639171234567')
        for value in ('', '123', '639171234567,639181234567'):
            self.assertIsNone(sms_sender.normalize_phone(value))

    def test_transport(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":200,"message_id":"test-id"}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.dict(os.environ, IPROG_API_TOKEN='synthetic-token'), patch(
                'urllib.request.build_opener', return_value=opener):
            self.assertEqual(sms_sender.send_sms('09171234567', 'test'), ('accepted', 'test-id'))
            request = opener.open.call_args.args[0]
            self.assertNotIn('synthetic-token', request.full_url)
            opener.open.side_effect = TimeoutError
            self.assertEqual(sms_sender.send_sms('09171234567', 'test'), ('unknown', None))

    def test_queue_commit_rollback_and_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, IPROG_API_TOKEN='synthetic-token'), patch.object(sms, 'wake') as wake:
            db = CBVMSDatabase(Path(tmp) / 'test.db')
            with patch.object(sms_sender, 'configured', return_value=False):
                db.initialize(process_deadlines=False)
            wake.reset_mock()
            with db.connect() as conn:
                conn.execute("INSERT INTO students(student_id,name,mobile_number) VALUES('S1','Synthetic','09171234567')")
                conn.execute("INSERT INTO violations(student_id,student_name,violation_type) VALUES('S1','Synthetic','Wrong Uniform')")
            with db.connect() as conn:
                row = conn.execute('SELECT * FROM violations').fetchone()
                sms.queue(conn, db, row)
                conn.rollback()
            wake.assert_not_called()
            with db.connect() as conn:
                sms.queue(conn, db, row)
                sms.queue(conn, db, row)
            wake.assert_called_once()
            with patch.object(sms_sender, 'send_sms', return_value=('accepted', 'test-id')) as send:
                sms.drain(db)
                sms.drain(db)
                send.assert_called_once_with('09171234567', sms.MESSAGE)
            with db.connect() as conn:
                sms.migrate(conn)
                self.assertEqual(conn.execute('SELECT status FROM violation_sms').fetchone()[0], 'accepted')
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            with db.connect() as conn:
                conn.execute('DELETE FROM violation_sms')
                sms.queue(conn, db, row)
                conn.execute("UPDATE students SET is_archived=1")
            with patch.object(sms_sender, 'send_sms') as send:
                sms.drain(db)
                send.assert_not_called()
            with db.connect() as conn:
                self.assertEqual(conn.execute('SELECT status FROM violation_sms').fetchone()[0], 'skipped')

    def test_publication_queues_even_when_credentials_are_unavailable(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp, patch.object(sms, 'wake'):
            db = CBVMSDatabase(Path(tmp) / 'test.db')
            db.initialize(process_deadlines=False)
            db.insert_student('S1', 'Synthetic', 'BSIT', '3A', b'', b'')
            with db.connect() as conn:
                conn.execute("UPDATE students SET mobile_number='09171234567'")
                vid = conn.execute("INSERT INTO violations(student_id,student_name,violation_type) VALUES('S1','Synthetic','Wrong Uniform')").lastrowid
            with patch.object(sms_sender, 'configured', return_value=True), db.connect() as conn:
                db._publish_violation_conn(conn, vid, datetime.now(timezone.utc), 'test')
                db._publish_violation_conn(conn, vid, datetime.now(timezone.utc), 'test')
            with db.connect() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM violation_sms').fetchone()[0], 1)
                conn.execute('DELETE FROM violation_sms')
                row = conn.execute('SELECT * FROM violations').fetchone()
                with patch.object(sms_sender, 'configured', return_value=False):
                    sms.queue(conn, db, row)
                self.assertEqual(conn.execute('SELECT status FROM violation_sms').fetchone()[0], 'pending')
            with patch.object(sms_sender, 'configured', return_value=False), patch.object(sms_sender, 'send_sms') as send:
                sms.drain(db)
                send.assert_not_called()
            with patch.object(sms_sender, 'configured', return_value=True), patch.object(sms_sender, 'send_sms', return_value=('accepted','test-receipt')) as send:
                sms.drain(db)
                send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
