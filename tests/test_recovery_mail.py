import unittest
from unittest.mock import MagicMock, Mock, patch

from core.recovery_mail import process_recovery_mail_once, smtp_ready


SMTP_CONFIG = {
    'host': 'smtp.example.test', 'port': 587,
    'sender_email': 'cbvms@example.test', 'sender_password': 'fixture-secret',
}


def message(code='123456', expires_at=1_700_000_600_000):
    return {'id': 'mail-1', 'claim': 'claim-1', 'to': 'student@example.test',
            'code': code, 'expires_at': expires_at}


class RecoveryMailTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.call.return_value = {'messages': [message()]}
        self.server = Mock()
        self.server.send_message.return_value = {}
        self.smtp = patch('core.recovery_mail.smtplib.SMTP', return_value=self.server)
        self.smtp_mock = self.smtp.start()
        self.addCleanup(self.smtp.stop)

    def test_invalid_local_smtp_does_not_claim_or_advertise_readiness(self):
        self.assertFalse(smtp_ready({'sender_email': '', 'sender_password': ''}))
        self.assertEqual(process_recovery_mail_once(self.client, config={}), 'unconfigured')
        self.client.call.assert_not_called()
        self.smtp_mock.assert_not_called()

    def test_code_message_uses_starttls_and_reports_accepted_without_persisting_code(self):
        result = process_recovery_mail_once(self.client, config=SMTP_CONFIG, now_ms=1_700_000_000_000)
        self.assertEqual(result, 'accepted')
        self.client.call.assert_any_call('mail/claim', {})
        self.client.call.assert_any_call('mail/receipt', {
            'id': 'mail-1', 'claim': 'claim-1', 'status': 'accepted',
        })
        self.server.starttls.assert_called_once()
        sent = self.server.send_message.call_args.args[0]
        self.assertEqual(sent['To'], 'student@example.test')
        self.assertEqual(sent['Subject'], 'CBVMS verification code')
        self.assertIn('Your CBVMS verification code is: 123456', sent.get_content())
        self.assertIn('expires in 10 minutes', sent.get_content())
        self.assertNotIn('123456', repr(self.client.call.call_args_list))

    def test_null_code_sends_password_changed_security_notification(self):
        self.client.call.return_value = {'messages': [message(code=None, expires_at=1_700_086_400_000)]}
        self.assertEqual(process_recovery_mail_once(self.client, config=SMTP_CONFIG,
                                                    now_ms=1_700_000_000_000), 'accepted')
        sent = self.server.send_message.call_args.args[0]
        body = sent.get_content()
        self.assertEqual(sent['Subject'], 'CBVMS password changed')
        self.assertIn('password was changed', body)
        self.assertIn('cloud sessions were revoked', body)
        self.assertIn('offline', body)
        self.assertIn('sync when it reconnects', body)
        self.assertIn('If you did not make this change', body)

    def test_notification_beyond_24_hour_expiry_is_rejected(self):
        self.client.call.return_value = {'messages': [message(code=None, expires_at=1_700_086_400_001)]}
        self.assertEqual(process_recovery_mail_once(self.client, config=SMTP_CONFIG,
                                                    now_ms=1_700_000_000_000), 'failed')
        self.smtp_mock.assert_not_called()
        self.client.call.assert_called_with('mail/receipt', {
            'id': 'mail-1', 'claim': 'claim-1', 'status': 'failed',
        })

    def test_expired_message_is_failed_without_smtp_attempt(self):
        self.assertEqual(process_recovery_mail_once(self.client, config=SMTP_CONFIG,
                                                    now_ms=1_700_000_600_000), 'expired')
        self.smtp_mock.assert_not_called()
        self.client.call.assert_called_with('mail/receipt', {
            'id': 'mail-1', 'claim': 'claim-1', 'status': 'failed',
        })

    def test_ambiguous_smtp_send_is_reported_uncertain(self):
        self.server.send_message.side_effect = TimeoutError('provider detail and recipient leak')
        with self.assertLogs('core.recovery_mail', level='WARNING') as logs:
            self.assertEqual(process_recovery_mail_once(self.client, config=SMTP_CONFIG,
                                                        now_ms=1_700_000_000_000), 'uncertain')
        self.assertNotIn('provider detail', '\n'.join(logs.output))
        self.assertEqual(sum(call.args[0] == 'mail/claim' for call in self.client.call.call_args_list), 1)
        self.client.call.assert_called_with('mail/receipt', {
            'id': 'mail-1', 'claim': 'claim-1', 'status': 'uncertain',
        })

    def test_connection_failure_is_failed(self):
        self.smtp_mock.side_effect = OSError('connection failed')
        self.assertEqual(process_recovery_mail_once(self.client, config=SMTP_CONFIG,
                                                    now_ms=1_700_000_000_000), 'failed')
        self.server.send_message.assert_not_called()
        self.client.call.assert_called_with('mail/receipt', {
            'id': 'mail-1', 'claim': 'claim-1', 'status': 'failed',
        })

    def test_starttls_failure_is_failed_without_plaintext_fallback(self):
        self.server.starttls.side_effect = OSError('TLS unavailable')
        self.assertEqual(process_recovery_mail_once(self.client, config=SMTP_CONFIG,
                                                    now_ms=1_700_000_000_000), 'failed')
        self.server.send_message.assert_not_called()
        self.client.call.assert_called_with('mail/receipt', {
            'id': 'mail-1', 'claim': 'claim-1', 'status': 'failed',
        })

    def test_invalid_code_or_recipient_is_never_sent(self):
        self.client.call.return_value = {'messages': [message(code='12345') ]}
        self.assertEqual(process_recovery_mail_once(self.client, config=SMTP_CONFIG,
                                                    now_ms=1_700_000_000_000), 'failed')
        self.smtp_mock.assert_not_called()
        self.client.call.assert_called_with('mail/receipt', {
            'id': 'mail-1', 'claim': 'claim-1', 'status': 'failed',
        })

    def test_cloud_client_allowlist_has_only_the_two_mail_operations(self):
        from core.cloud_sync import CloudClient
        client = CloudClient({'origin': 'https://studentportal.example', 'sync_key': 'fixture'})
        client.opener = MagicMock()
        response = client.opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"messages": []}'
        self.assertEqual(client.call('mail/claim', {}), {'messages': []})
        self.assertEqual(client.call('mail/receipt', {'id': 'fixture'}), {'messages': []})
        self.assertIn('/api/sync/mail/claim', client.opener.open.call_args_list[0].args[0].full_url)
        self.assertIn('/api/sync/mail/receipt', client.opener.open.call_args_list[1].args[0].full_url)
        with self.assertRaises(ValueError):
            client.call('mail')


if __name__ == '__main__':
    unittest.main()
