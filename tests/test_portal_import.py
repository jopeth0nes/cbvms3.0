import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.portal_import import build_batch, prepare
from core.discipline import utc_now
from database.db_manager import CBVMSDatabase
from tests.evidence_fixture import picture_evidence
from tests.term_fixture import verified_term

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / 'sites/student-portal/drizzle'
IMPORT_ID = '9b29b1a6-5b45-4a86-b18e-e28728c40000'
HASH = 'pbkdf2_sha256$v1$600000$' + '1' * 32 + '$' + '2' * 64


class PortalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        mail = patch('core.email_sender.dispatch_discipline_notice')
        mail.start(); self.addCleanup(mail.stop)
        root = Path(self.temp.name)
        config = patch('database.db_manager.__file__', str(root / 'database/db_manager.py'))
        config.start(); self.addCleanup(config.stop)
        self.campus = root / 'campus.db'
        self.cloud = root / 'cloud.db'
        self.output = root / 'prepared'
        self.db = CBVMSDatabase(self.campus)
        self.db.initialize()
        self._make_cloud()

    def _make_cloud(self):
        with sqlite3.connect(self.cloud) as conn:
            for name in sorted(MIGRATIONS.glob('*.sql')):
                conn.executescript(name.read_text(encoding='utf-8'))
            conn.execute("INSERT INTO campus_sync_control(singleton,source_id,writer_id,protocol,generation,run_id,checked_at,minimum_snapshot_version,published_student_count) VALUES(1,'source-fixture','writer-fixture',2,4,NULL,'2026-10-24T00:00:00+00:00',3,1)")
            conn.execute("INSERT INTO campus_sync_publications(generation,run_id) VALUES(4,'run-fixture')")
            cloud_snapshot = {'schema_version': 3, 'violations': [{'id': 1}]}
            conn.execute('INSERT INTO portal_students(id,email,name,snapshot,synced_at,active) VALUES(?,?,?,?,?,?)',
                ('cloud-only', 'cloud@example.invalid', 'Cloud fixture', json.dumps(cloud_snapshot), 'then', 1))
            conn.execute('INSERT INTO portal_accounts(student_id,username,password_hash,must_change_password,credential_version) VALUES(?,?,?,?,?)',
                ('cloud-only', 'cloud-user', HASH, 0, 8))
            conn.execute('INSERT INTO portal_actions(id,student_id,kind,payload,created_at) VALUES(?,?,?,?,?)',
                ('queued-fixture', 'cloud-only', 'reports', '{}', 'then'))
            conn.execute('INSERT INTO portal_preferences(student_id,value) VALUES(?,?)', ('cloud-only', '{"email":true}'))

    def _student_case(self):
        _, _, blob = picture_evidence()
        self.db.insert_student('00123', 'Élodie 東京', 'BSIT', '3A', b'fixture-face-encoding', b'fixture-profile-photo',
            account_username='u00123', account_password='Local Secret Password', email='private@example.invalid')
        verified_term(self.db, '00123', utc_now().year)
        vid = self.db.log_violation('00123', 'Élodie 東京', 'wrong_uniform', snapshot_jpeg=blob)
        with self.db.connect() as conn:
            conn.execute('INSERT INTO student_notifications(student_id,title,message,violation_id) VALUES(?,?,?,?)',
                ('00123', 'Fixture notice', 'Fixture only', vid))
            cursor = conn.execute('INSERT INTO appeals(violation_id,student_id,reason) VALUES(?,?,?)',
                (vid, '00123', 'A synthetic appeal with enough characters.'))
            appeal_id = cursor.lastrowid
            conn.execute('INSERT INTO evidence_files(appeal_id,student_id,filename,file_type,file_data,file_sha256) VALUES(?,?,?,?,?,?)',
                (appeal_id, '00123', 'support.png', 'image', blob, hashlib.sha256(blob).hexdigest()))
            conn.commit()
        return vid, appeal_id, blob

    def _prepare(self):
        return prepare(self.campus, self.cloud, self.output, IMPORT_ID)

    def test_full_offline_export_and_insert_only_sqlite_rehearsal(self):
        local_id, appeal_id, blob = self._student_case()
        original_bytes = self.campus.read_bytes()
        with sqlite3.connect(self.campus) as conn:
            before = list(conn.iterdump())
        result = self._prepare()
        self.assertEqual(result['student_count'], 1)
        self.assertEqual(result['evidence_count'], 2)
        self.assertEqual(original_bytes, self.campus.read_bytes())
        with sqlite3.connect(self.campus) as conn:
            self.assertEqual(before, list(conn.iterdump()))
        bundle_path = Path(result['bundle'])
        self.assertEqual(bundle_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(bundle_path.parent.stat().st_mode & 0o777, 0o700)
        bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
        raw = bundle_path.read_bytes()
        self.assertNotIn(b'fixture-face-encoding', raw)
        self.assertNotIn(b'fixture-profile-photo', raw)
        self.assertNotIn(b'Local Secret Password', raw)
        row = bundle['students'][0]
        self.assertEqual(row['id'], '00123')
        self.assertEqual(row['name'], 'Élodie 東京')
        mapped = row['snapshot']['violations'][0]
        self.assertLess(mapped['id'], 0)
        self.assertEqual(mapped['campus_record_id'], local_id)
        self.assertEqual(mapped['campus_violation_id'], local_id)
        notice = row['snapshot']['notifications'][0]
        self.assertEqual(notice['violation_id'], mapped['id'])
        self.assertEqual(notice['campus_violation_id'], local_id)
        appeal = next(a for a in row['snapshot']['appeals'] if a['id'] == appeal_id)
        supporting = appeal['supporting_evidence'][0]
        self.assertEqual(supporting['checksum_origin'], 'stored')
        self.assertEqual(hashlib.sha256(blob).hexdigest(), supporting['sha256'])
        self.assertEqual(len(bundle['mapping']['00123']), 1)
        self.assertTrue(all(Path(result['bundle']).parent.joinpath(e['path']).stat().st_mode & 0o777 == 0o600 for e in bundle['evidence']))
        self.assertTrue(all(hashlib.sha256((bundle_path.parent / e['path']).read_bytes()).hexdigest() == e['sha256'] for e in bundle['evidence']))

        batch = build_batch(bundle_path)
        with sqlite3.connect(self.cloud) as conn:
            before_actions = conn.execute('SELECT * FROM portal_actions').fetchall()
            before_prefs = conn.execute('SELECT * FROM portal_preferences').fetchall()
            before_cloud = conn.execute("SELECT * FROM portal_students WHERE id='cloud-only'").fetchone()
            conn.execute('BEGIN')
            for item in batch:
                conn.execute(item['sql'], item['params'])
            conn.commit()
            self.assertEqual(conn.execute('SELECT * FROM portal_actions').fetchall(), before_actions)
            self.assertEqual(conn.execute('SELECT * FROM portal_preferences').fetchall(), before_prefs)
            self.assertEqual(conn.execute("SELECT * FROM portal_students WHERE id='cloud-only'").fetchone(), before_cloud)
            imported = json.loads(conn.execute("SELECT snapshot FROM portal_students WHERE id='00123'").fetchone()[0])
            self.assertEqual(imported['violations'][0]['id'], mapped['id'])
            self.assertEqual(conn.execute("SELECT password_hash FROM portal_accounts WHERE student_id='00123'").fetchone()[0], bundle['accounts'][0]['password_hash'])
            self.assertEqual(conn.execute('SELECT source_checkpoint FROM campus_portal_imports').fetchone()[0], bundle['source_checkpoint'])

    def test_missing_invalid_and_colliding_credentials_reject_without_output(self):
        self._student_case()
        with self.db.connect() as conn:
            conn.execute("DELETE FROM student_accounts WHERE student_id='00123'")
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())
        with self.db.connect() as conn:
            conn.execute("INSERT INTO student_accounts(student_id,username,password_hash) VALUES(?,?,?)", ('00123', 'u00123', HASH))
            conn.execute("UPDATE student_accounts SET password_hash='invalid' WHERE student_id='00123'")
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())
        with self.db.connect() as conn:
            conn.execute("UPDATE student_accounts SET password_hash=?,credential_version=-1 WHERE student_id='00123'", (HASH,))
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())

    def test_existing_legacy_credential_is_preserved_without_reset_or_rehash(self):
        self._student_case()
        legacy = hashlib.sha256(b'Synthetic legacy fixture password').hexdigest()
        with self.db.connect() as conn:
            conn.execute('UPDATE student_accounts SET password_hash=? WHERE student_id=?', (legacy, '00123'))
        result = self._prepare()
        bundle = json.loads(Path(result['bundle']).read_text())
        self.assertEqual(bundle['accounts'][0]['password_hash'], legacy)
        self.assertTrue(build_batch(result['bundle']))

    def test_username_and_permanent_id_collisions_reject(self):
        self._student_case()
        with sqlite3.connect(self.cloud) as conn:
            conn.execute("UPDATE portal_accounts SET username='u00123' WHERE student_id='cloud-only'")
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())
        with sqlite3.connect(self.cloud) as conn:
            conn.execute("UPDATE portal_accounts SET username='cloud-user' WHERE student_id='cloud-only'")
            conn.execute("UPDATE portal_students SET id='00123' WHERE id='cloud-only'")
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())
        with sqlite3.connect(self.cloud) as conn:
            conn.execute("UPDATE portal_students SET id='cloud-only',email=? WHERE id='00123'", (hashlib.sha256(b'00123').hexdigest() + '@cbvms.invalid',))
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())

    def test_original_evidence_missing_or_checksum_mismatch_rejects(self):
        self._student_case()
        with self.db.connect() as conn:
            conn.execute("UPDATE violations SET snapshot_sha256=?", ('0' * 64,))
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())
        with self.db.connect() as conn:
            conn.execute('UPDATE violations SET snapshot=NULL,snapshot_sha256=?', ('1' * 64,))
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())

    def test_supporting_evidence_checksum_mismatch_rejects(self):
        self._student_case()
        with self.db.connect() as conn:
            conn.execute("UPDATE evidence_files SET file_sha256=?", ('0' * 64,))
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())

    def test_supporting_evidence_owner_mismatch_and_orphan_reject(self):
        self._student_case()
        with self.db.connect() as conn:
            conn.execute("UPDATE evidence_files SET student_id='another-student'")
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())
        with sqlite3.connect(self.campus) as conn:
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute("INSERT INTO evidence_files(appeal_id,student_id,filename,file_type,file_data) VALUES(999,'00123','orphan.png','image',?)", (b'orphan',))
            conn.commit()
        with self.assertRaises(ValueError): self._prepare()
        self.assertFalse(self.output.exists())

    def test_tampered_bundle_and_stale_control_or_rows_abort_batch(self):
        self._student_case()
        result = self._prepare()
        bundle = json.loads(Path(result['bundle']).read_text())
        tampered = dict(bundle); tampered['students'] = []
        with self.assertRaises(ValueError): build_batch(tampered)
        batch = build_batch(result['bundle'])
        for update in ("UPDATE campus_sync_control SET generation=9", "UPDATE portal_students SET name='changed' WHERE id='cloud-only'", "DELETE FROM campus_sync_control"):
            temp_cloud = Path(self.temp.name) / ('stale' + str(len(update)) + '.db')
            shutil.copy2(self.cloud, temp_cloud)
            with sqlite3.connect(temp_cloud) as conn:
                conn.execute(update); conn.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute('BEGIN')
                    for item in batch: conn.execute(item['sql'], item['params'])
                    conn.commit()
                conn.rollback()
                self.assertEqual(conn.execute("SELECT count(*) FROM portal_students WHERE id='00123'").fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT count(*) FROM campus_portal_imports').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
