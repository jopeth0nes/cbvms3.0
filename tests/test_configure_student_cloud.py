import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from scripts.configure_student_cloud import save_config
from core.cloud_sync import CloudClient
from database.cloud_sync_migration import migrate


class CloudConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'campus.db'
        self.target = self.root / 'cloud-sync.json'
        self.config = dict(origin='https://studentportal.bond', sync_key='fixture-only',
                           source_id=str(uuid.uuid4()), writer_id=str(uuid.uuid4()))
        with sqlite3.connect(self.db) as conn:
            migrate(conn)
            conn.execute('INSERT INTO campus_sync_identity(singleton,source_id,writer_id) VALUES (1,?,?)',
                         (self.config['source_id'], self.config['writer_id']))

    def test_direct_config_is_private_disabled_and_client_needs_no_sites_token(self):
        save_config(self.config, self.target, self.db)
        saved = json.loads(self.target.read_text())
        self.assertIs(saved['enabled'], False)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('OAI-Sites-Authorization', CloudClient(saved).headers)
        self.assertEqual(CloudClient(saved).headers['Authorization'], 'Bearer fixture-only')

    def test_existing_configuration_and_symlink_are_never_replaced(self):
        self.target.write_bytes(b'existing bytes')
        with self.assertRaises(FileExistsError):
            save_config(self.config, self.target, self.db)
        self.assertEqual(self.target.read_bytes(), b'existing bytes')
        self.target.unlink()
        self.target.symlink_to(self.root / 'missing')
        with self.assertRaises(FileExistsError):
            save_config(self.config, self.target, self.db)
        self.assertFalse((self.root / 'missing').exists())

    def test_rejects_mismatch_enablement_bad_origin_and_header_injection(self):
        for change in [dict(source_id=str(uuid.uuid4())), dict(writer_id=str(uuid.uuid4())),
                       dict(enabled=True), dict(enabled=0), dict(sync_key=''),
                       dict(sync_key='secret\r\nInjected: value'), dict(service_token=None),
                       dict(origin='https://user:password@example.com'),
                       dict(origin='https://example.com/api'), dict(origin='http://example.com'),
                       dict(origin='https://example.com:bad'), dict(extra='unknown')]:
            with self.subTest(fields=list(change)):
                with self.assertRaises(ValueError):
                    save_config({**self.config, **change}, self.target, self.db)
                self.assertFalse(self.target.exists())

    def test_unregistered_database_is_not_registered_by_helper(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('DELETE FROM campus_sync_identity')
        with self.assertRaises(ValueError):
            save_config(self.config, self.target, self.db)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM campus_sync_identity').fetchone()[0], 0)
        self.assertFalse(self.target.exists())

    def test_sites_gateway_header_is_preserved(self):
        config = {**self.config, 'service_token': 'fixture-gateway'}
        save_config(config, self.target, self.db)
        self.assertEqual(CloudClient(config).headers['OAI-Sites-Authorization'], 'Bearer fixture-gateway')


if __name__ == '__main__':
    unittest.main()
