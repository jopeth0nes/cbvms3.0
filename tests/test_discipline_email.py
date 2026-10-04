import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from core.discipline import utc_now
from database.db_manager import CBVMSDatabase
from core.email_sender import send_discipline_notice


class DisciplineEmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'test.db')
        self.db.initialize()
        self.db.insert_student('S1', 'Student', 'BSIT', '3A', b'', b'', email='student@example.com')
        self.dispatch = Mock()
        self.publication_dispatch = Mock()
        patch('core.email_sender.dispatch_discipline_notice', side_effect=
              lambda *args: (self.publication_dispatch if args[3] == 'CBVMS - Violation Notice'
                             else self.dispatch)(*args)).start()
        self.addCleanup(patch.stopall)

    def test_detection_emails_designated_student_once_confirmation_does_not_repeat(self):
        self.db.insert_student('S2', 'Other Student', 'BSIT', '3A', b'', b'', email='other@example.com')
        vid = self.db.log_violation('S1', 'Student', 'wrong_uniform')
        self.db.confirm_violation(vid)
        self.db.confirm_violation(vid)
        self.publication_dispatch.assert_called_once()
        args = self.publication_dispatch.call_args.args
        self.assertEqual(args[:3], ('student@example.com', 'Student', 'S1'))
        self.assertIn('Wrong uniform', args[4])
        self.assertIn(f'Violation #{vid}', args[4])
        self.assertIn('Appeal deadline:', args[4])
        self.dispatch.assert_not_called()

    def test_rejected_appeals_award_one_strike_and_third_emails_suspension(self):
        from tests.evidence_fixture import picture_evidence
        for count in range(1, 4):
            vid = self.db.log_violation('S1', 'Student', 'wrong_uniform',
                                        snapshot_jpeg=picture_evidence()[2])
            aid = self.db.insert_appeal(vid, 'S1', 'Please review my uniform.',
                                        evidence=picture_evidence())
            self.assertIsNotNone(aid)
            self.assertTrue(self.db.update_appeal_decision(aid, 'rejected',
                            'Evidence reviewed.', decided_by='admin', decision_category_code='rejection.violation_confirmed'))
            self.assertFalse(self.db.update_appeal_decision(aid, 'rejected',
                             'Evidence reviewed.', decided_by='admin', decision_category_code='rejection.violation_confirmed'))
            with self.db.connect() as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM strikes WHERE student_id='S1' AND is_active=1"
                ).fetchone()[0], count)
            self.assertEqual(self.dispatch.call_count, int(count == 3))
        self.assertEqual(self.publication_dispatch.call_count, 3)
        self.assertEqual(self.dispatch.call_args.args[0], 'student@example.com')
        self.assertEqual(self.dispatch.call_args.args[3], 'CBVMS - Suspension Notice')
        self.assertEqual(len(self.db.get_suspension_history('S1')), 1)

    def test_cancelled_publication_does_not_email(self):
        checks = iter([True, False])
        self.assertIsNone(self.db.log_violation('S1', 'Student', 'wrong_uniform',
                                             valid_if=lambda: next(checks)))
        self.publication_dispatch.assert_not_called()
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM violations').fetchone()[0], 0)

    def test_appeal_history_for_both_admin_roles(self):
        from tests.evidence_fixture import picture_evidence
        vid = self.db.log_violation('S1', 'Student', 'wrong_uniform',
                                    snapshot_jpeg=picture_evidence()[2])
        aid = self.db.insert_appeal(vid, 'S1', 'Please review my uniform evidence.', evidence=picture_evidence())
        self.assertTrue(self.db.update_appeal_decision(
            aid, 'approved', 'Uniform verified', decided_by='admin', decision_category_code='approval.detection_error'))
        self.db.log_violation('S1', 'Student', 'wrong_uniform')
        reopened = CBVMSDatabase(self.db.db_path)
        reopened.initialize(process_deadlines=False)
        for username in ('admin', 'superadmin'):
            result = reopened.get_appeal_inbox(username=username, status='history', search='S1')
            self.assertEqual(result['total'], 1)
            row = result['rows'][0]
            self.assertEqual(row['id'], aid)
            self.assertEqual(row['decided_by'], 'admin')
            self.assertEqual(row['admin_notes'], 'Uniform verified')
            self.assertTrue(row['decided_at'])
            self.assertEqual(reopened.get_appeal_inbox(
                username=username, status='history', search='S1', offset=1)['rows'], [])
        with self.assertRaises(PermissionError):
            reopened.get_appeal_inbox(username='student', status='history')

    def test_publication_without_email_skips_delivery(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE students SET email='' WHERE student_id='S1'")
        self.db.log_violation('S1', 'Student', 'wrong_uniform')
        self.publication_dispatch.assert_not_called()

    def test_third_uniform_strike_suspends_and_emails_once(self):
        for _ in range(3):
            self.db.log_violation('S1', 'Student', 'wrong_uniform', status='confirmed')
        self.dispatch.assert_not_called()
        now = utc_now() + timedelta(days=6)
        self.db.process_expired_deadlines(now=now)
        self.db.process_expired_deadlines(now=now)
        self.dispatch.assert_called_once()
        self.assertEqual(self.dispatch.call_args.args[0], 'student@example.com')
        self.assertIn('Suspension Notice', self.dispatch.call_args.args[3])
        self.assertEqual(len(self.db.get_suspension_history('S1')), 1)

    def test_uniform_milestones_expiry_and_semester_reset(self):
        from core.discipline import parse_db_datetime
        now = utc_now() + timedelta(days=6)
        for strike in range(1, 9):
            self.db.log_violation('S1', 'Student', 'wrong_uniform', status='confirmed')
            self.db.process_expired_deadlines(now=now)
            self.db.process_expired_deadlines(now=now)
            if strike in (3, 5, 7):
                days = {3: 2, 5: 7, 7: 14}[strike]
                active = self.db.get_active_suspension('S1', now=now)
                self.assertEqual(parse_db_datetime(active['ends_at']) - parse_db_datetime(active['starts_at']), timedelta(days=days))
                self.assertEqual(active['imposed_by'], 'system')
                self.assertEqual(self.dispatch.call_count, {3: 1, 5: 2, 7: 3}[strike])
        self.assertEqual(len(self.db.get_suspension_history('S1')), 3)
        self.assertIn('parents', self.dispatch.call_args.args[4])
        self.assertIsNone(self.db.get_active_suspension('S1', now=now + timedelta(days=14, seconds=1)))
        self.db.set_current_academic_term('Semester 2', '2099-2100')
        for _ in range(3):
            self.db.log_violation('S1', 'Student', 'wrong_uniform', status='confirmed')
        self.db.process_expired_deadlines(now=now + timedelta(days=20))
        self.assertEqual(self.dispatch.call_count, 4)
        self.assertEqual(len(self.db.get_suspension_history('S1')), 4)

    def test_other_category_keeps_review_policy(self):
        for _ in range(7):
            self.db.log_violation('S1', 'Student', 'earring', status='confirmed')
        self.db.process_expired_deadlines(now=utc_now() + timedelta(days=6))
        self.assertEqual(self.db.get_suspension_history('S1'), [])
        self.dispatch.assert_called_once()

    def test_existing_pre_policy_strikes_catch_up_once(self):
        for _ in range(5):
            self.db.log_violation('S1', 'Student', 'wrong_uniform', status='confirmed')
        with patch.object(self.db, '_automatic_uniform_suspension_conn'):
            self.db.process_expired_deadlines(now=utc_now() + timedelta(days=6))
        self.assertEqual(self.db.get_suspension_history('S1'), [])
        self.dispatch.reset_mock()
        self.db.process_expired_deadlines()
        self.db.process_expired_deadlines()
        self.assertEqual(len(self.db.get_suspension_history('S1')), 1)
        self.assertIn('7 days', self.dispatch.call_args.args[4])
        self.dispatch.assert_called_once()

    def test_suspension_and_email_roll_back_with_strike_transaction(self):
        for _ in range(3):
            self.db.log_violation('S1', 'Student', 'no_uniform', status='confirmed')
        with patch.object(self.db, '_automatic_uniform_suspension_conn',
                          wraps=self.db._automatic_uniform_suspension_conn) as award:
            original = self.db._insert_event_notification_conn
            def fail_after_suspension(conn, **kwargs):
                if kwargs.get('title') == 'Third Strike Reached':
                    raise RuntimeError('Abort suspension transaction')
                return original(conn, **kwargs)
            with patch.object(self.db, '_insert_event_notification_conn', side_effect=fail_after_suspension):
                with self.assertRaises(RuntimeError):
                    self.db.process_expired_deadlines(now=utc_now() + timedelta(days=6))
            self.assertTrue(award.called)
        self.assertEqual(self.db.get_suspension_history('S1'), [])
        self.dispatch.assert_not_called()
        self.db.process_expired_deadlines(now=utc_now() + timedelta(days=6))
        self.assertEqual(len(self.db.get_suspension_history('S1')), 1)
        self.dispatch.assert_called_once()

    def test_reaching_same_milestone_again_does_not_repeat_suspension(self):
        ids = [self.db.log_violation('S1', 'Student', 'wrong_uniform', status='confirmed')
               for _ in range(3)]
        now = utc_now() + timedelta(days=6)
        self.db.process_expired_deadlines(now=now)
        with self.db.connect() as conn:
            conn.execute('UPDATE strikes SET is_active=0 WHERE violation_id=?', (ids[0],))
        self.db.log_violation('S1', 'Student', 'wrong_uniform', status='confirmed')
        self.db.process_expired_deadlines(now=now)
        self.assertEqual(len(self.db.get_suspension_history('S1')), 1)
        self.dispatch.assert_called_once()

    def test_suspension_email_and_overlap_rejection(self):
        args = dict(reason='OSA review', starts_at=utc_now(), ends_at=None, imposed_by='osa')
        self.db.impose_suspension('S1', **args)
        self.dispatch.assert_called_once()
        self.assertIn('OSA review', self.dispatch.call_args.args[4])
        with self.assertRaises(ValueError):
            self.db.impose_suspension('S1', **args)
        self.dispatch.assert_called_once()

    def test_rollback_sends_nothing(self):
        with self.assertRaises(RuntimeError):
            with self.db.connect() as conn:
                self.db._queue_discipline_email_conn(conn, 'S1', 'Notice', 'Message')
                raise RuntimeError('Abort')
        self.dispatch.assert_not_called()

    def test_missing_email_skips_delivery(self):
        with self.db.connect() as conn:
            conn.execute("UPDATE students SET email='' WHERE student_id='S1'")
            self.db._queue_discipline_email_conn(conn, 'S1', 'Notice', 'Message')
        self.dispatch.assert_not_called()

    def test_smtp_uses_recipient_and_notice_content(self):
        with patch('core.email_sender.load_smtp_config', return_value={
            'sender_email': 'office@example.com', 'sender_password': 'test-password',
            'host': 'smtp.example.com', 'port': 587,
        }), patch('core.email_sender.smtplib.SMTP') as smtp:
            success, error = send_discipline_notice('student@example.com', 'Student',
                                                   'S1', 'Suspension Notice', 'Assigned by OSA')
        self.assertTrue(success, error)
        server = smtp.return_value.__enter__.return_value
        server.starttls.assert_called_once()
        sent = server.sendmail.call_args.args
        self.assertEqual(sent[:2], ('office@example.com', 'student@example.com'))
        from email import message_from_string
        message = message_from_string(sent[2])
        self.assertEqual(message['Subject'], 'Suspension Notice')
        self.assertIn('Assigned by OSA', message.get_payload(decode=True).decode('utf-8'))
