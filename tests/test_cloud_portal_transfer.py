import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.prepare_cloud_portal_transfer import TABLES, prepare


class PortalTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.migrations = Path(__file__).resolve().parents[1] / 'sites/student-portal/drizzle'
        with closing(sqlite3.connect(':memory:')) as conn:
            for path in sorted(self.migrations.glob('*.sql'))[:2]:
                conn.executescript(path.read_text())
            self.tables = [{'name': name, 'columns': [r[1] for r in conn.execute(f'PRAGMA table_info({name})')], 'rows': []} for name in TABLES]
        self.tables[0]['rows'] = [dict(id='S1', email='s1@example.invalid', name='Fixture', snapshot='{}', synced_at='2026-01-01', active=1)]
        self.tables[1]['rows'] = [dict(student_id='S1', username='S1', password_hash='fixture-hash', must_change_password=1, pending_base_hash=None, pending_action_id=None)]
        self.tables[4]['rows'] = [dict(token_hash='old-session', student_id='S1', credential_hash='fixture', csrf='fixture', expires=1)]

    def run_transfer(self):
        source = self.root / 'backup.json'
        source.write_text(json.dumps({'tables': self.tables}))
        return prepare(source, self.root/'restore.db', self.root/'transfer.sql', self.migrations)

    def test_restores_values_preserves_hash_and_excludes_sessions_from_import(self):
        result = self.run_transfer()
        self.assertEqual(result['persistent_rows'], 2)
        self.assertEqual(result['transient_rows_backed_up_only'], 1)
        text = (self.root/'transfer.sql').read_text()
        self.assertIn('fixture-hash', text)
        self.assertNotIn('old-session', text)
        with closing(sqlite3.connect(self.root/'restore.db')) as conn:
            self.assertEqual(conn.execute('SELECT token_hash FROM portal_sessions').fetchone()[0], 'old-session')
            self.assertEqual(conn.execute('SELECT credential_version FROM portal_accounts').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.run_transfer()

    def test_refuses_missing_tables_and_unbacked_evidence(self):
        original = self.tables.pop()
        with self.assertRaisesRegex(ValueError, 'six-table'):
            self.run_transfer()
        self.tables.append(original)
        self.tables[0]['rows'][0]['snapshot'] = json.dumps({'violations': [{'has_snapshot': True}]})
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.run_transfer()
        self.assertFalse((self.root/'restore.db').exists())
