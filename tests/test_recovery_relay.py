"""Mail-only transport and SMTP readiness; never touches real mail or databases."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from core.recovery_relay import RecoveryRelayClient, relay_cycle, load_relay_config, run_relay


CONFIG = {'origin': 'https://portal.example', 'relay_key': 'x' * 48, 'enabled': True}


class RecoveryRelayTests(unittest.TestCase):
    def test_mail_only_client_rejects_data_and_sync_routes(self):
        client = RecoveryRelayClient(CONFIG)
        for path in ('audit', 'publish', 'actions', '../sync/publish', 'claim?foo=bar'):
            with self.assertRaises(ValueError):
                client.call(path, {})
        client.opener = MagicMock()
        response = client.opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"messages":[]}'
        client.call('mail/claim', {})
        req = client.opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://portal.example/api/relay/mail/claim')
        self.assertFalse(any(k.lower().startswith('x-cbvms') for k in req.headers))
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + CONFIG['relay_key'])

    def test_client_rejects_non_https_and_credential_in_url(self):
        for origin in ('http://portal.example', 'https://key@portal.example',
                       'https://portal.example/path', 'https://portal.example?key=secret'):
            with self.assertRaises(ValueError):
                RecoveryRelayClient({**CONFIG, 'origin': origin})

    def test_config_has_restricted_permissions_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'relay.json'
            p.write_text(json.dumps(CONFIG))
            p.chmod(0o600)
            self.assertEqual(load_relay_config(p), CONFIG)
            p.chmod(0o644)
            with self.assertRaises(ValueError): load_relay_config(p)
            p.chmod(0o600)
            link = Path(tmp) / 'link'; link.symlink_to(p)
            with self.assertRaises(ValueError): load_relay_config(link)

    @patch('core.recovery_mail.smtplib.SMTP')
    def test_smtp_health_authenticates_over_tls_without_sending(self, smtp):
        from core.recovery_mail import check_smtp_connection
        from tests.test_recovery_mail import SMTP_CONFIG
        self.assertTrue(check_smtp_connection(SMTP_CONFIG))
        server=smtp.return_value.__enter__.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once()
        server.send_message.assert_not_called()
        server.starttls.side_effect=OSError('secret provider detail')
        self.assertFalse(check_smtp_connection(SMTP_CONFIG))
        server.send_message.assert_not_called()

    @patch('core.recovery_relay.relay_cycle', return_value='empty')
    def test_standalone_runner_needs_no_campus_sync_configuration(self, cycle):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'relay.json';config.write_text(json.dumps(CONFIG));config.chmod(0o600)
            status=Path(tmp)/'status.json'
            self.assertEqual(run_relay(config, once=True, status_path=status),'empty')
            cycle.assert_called_once()
            self.assertEqual(json.loads(status.read_text())['state'],'empty')
            self.assertEqual(status.stat().st_mode & 0o777,0o600)
            self.assertNotIn(CONFIG['relay_key'],status.read_text())

    @patch('core.recovery_relay.relay_cycle', side_effect=OSError('sensitive provider detail'))
    def test_runner_records_generic_connection_error(self, cycle):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'relay.json';config.write_text(json.dumps(CONFIG));config.chmod(0o600)
            status=Path(tmp)/'status.json'
            self.assertEqual(run_relay(config, once=True, status_path=status),'connection_or_configuration_error')
            self.assertNotIn('sensitive provider detail',status.read_text())

    @patch('core.recovery_relay.process_recovery_mail_once', return_value='empty')
    @patch('core.recovery_relay.check_smtp_connection', return_value=True)
    def test_readiness_is_checked_before_advertising_and_claiming(self, check, process):
        client = Mock()
        self.assertEqual(relay_cycle(client), 'empty')
        check.assert_called_once_with()
        client.call.assert_called_once_with('mail/heartbeat', {'ready': True})
        process.assert_called_once_with(client)

    @patch('core.recovery_relay.process_recovery_mail_once')
    @patch('core.recovery_relay.check_smtp_connection', return_value=False)
    def test_failed_smtp_health_marks_offline_and_does_not_claim(self, check, process):
        client = Mock()
        self.assertEqual(relay_cycle(client), 'smtp_unavailable')
        client.call.assert_called_once_with('mail/heartbeat', {'ready': False})
        process.assert_not_called()

    @patch('core.recovery_relay.process_recovery_mail_once', return_value='uncertain')
    def test_ambiguous_delivery_marks_offline_without_duplicate_send(self, process):
        client = Mock()
        self.assertEqual(relay_cycle(client, check_health=False), 'uncertain')
        process.assert_called_once_with(client)
        client.call.assert_called_once_with('mail/heartbeat', {'ready': False})


if __name__ == '__main__': unittest.main()
