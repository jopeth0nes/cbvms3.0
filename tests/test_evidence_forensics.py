"""Focused regressions for bounded image validation and forensic record isolation."""
import io
import os
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from core.appeal_evidence import validate_evidence
from core.evidence_forensics import analyze_evidence
from database.db_manager import CBVMSDatabase
from database.evidence_forensics import create_run, run_one, claim_next, _finish
from tests.test_appeal_flow_completion import AppealFlowFixture, picture


class EvidenceForensicsImageTests(unittest.TestCase):
    def test_worker_failure_does_not_echo_input(self):
        result = subprocess.run([sys.executable,'-m','core.forensics_worker'],
                                input=b'private-student-payload', capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(result.stderr,b'analysis_failed')

    @unittest.skipUnless(os.name == 'posix', 'POSIX resource bounds')
    def test_worker_applies_cpu_file_descriptor_output_and_alarm_bounds(self):
        code = """import json,resource,signal
from core.forensics_worker import _limits
_limits()
print(json.dumps([resource.getrlimit(resource.RLIMIT_CPU),
                  resource.getrlimit(resource.RLIMIT_FSIZE),
                  resource.getrlimit(resource.RLIMIT_NOFILE),signal.alarm(0)]))
"""
        result = subprocess.run([sys.executable,'-c',code], capture_output=True, timeout=10, check=True)
        limits = __import__('json').loads(result.stdout)
        self.assertEqual(limits[:3],[[100,101],[2097152,2097152],[64,64]])
        self.assertIn(limits[3],(45,120))

    def test_extreme_side_is_rejected_before_image_load(self):
        data = io.BytesIO()
        Image.new('RGB', (10_001, 1), 'white').save(data, 'PNG')
        with self.assertRaisesRegex(ValueError, 'damaged|dimensions'):
            validate_evidence('wide.png', 'image', data.getvalue())

    def test_malformed_image_and_extension_mismatch_are_handled(self):
        with self.assertRaises(ValueError):
            validate_evidence('broken.png', 'image', b'not an image')
        png = picture()[2]
        report = analyze_evidence(png, 'photo.jpg')
        self.assertEqual(report['sha256'], __import__('hashlib').sha256(png).hexdigest())
        self.assertEqual(report['classification'], 'inconclusive')
        self.assertIsNone(report['risk'])
        self.assertEqual(report['provenance']['c2pa']['status'], 'unavailable')
        self.assertEqual(report['provenance']['model']['status'], 'not_configured')
        self.assertEqual(report['signals'][0]['code'], 'extension_format_mismatch')
        self.assertEqual(analyze_evidence(png, 'photo.png')['classification'], 'inconclusive')

    def test_metadata_is_allowlisted_and_editor_tag_is_neutral(self):
        image = Image.new('RGB', (8, 8), 'red')
        exif = Image.Exif()
        exif[305] = 'Editor X'
        exif[271] = 'Camera Y'
        exif[37510] = 'private xmp payload'
        out = io.BytesIO(); image.save(out, 'JPEG', exif=exif)
        report = analyze_evidence(out.getvalue(), 'safe.jpg')
        self.assertEqual(report['metadata']['software'], 'Editor X')
        self.assertEqual(report['metadata']['camera_make'], 'Camera Y')
        self.assertNotIn('private xmp payload', repr(report))
        self.assertFalse(report['signals'])

    def test_optional_analyzers_remain_inconclusive_and_keep_raw_score_uncalibrated(self):
        png = picture()[2]
        c2pa = types.SimpleNamespace(inspect_c2pa=lambda data, fmt: {'status':'valid'})
        model = types.SimpleNamespace(inspect_model=lambda data, fmt: {
            'status':'experimental','raw_score':0.91,'reliability':{'status':'experimental'},
            'localization_png':png,'reliability_png':None})
        with patch.dict(sys.modules, {'core.forensics_c2pa':c2pa, 'core.forensics_model':model}):
            report = analyze_evidence(png, 'photo.png')
        self.assertEqual(report['classification'], 'inconclusive')
        self.assertEqual(report['model_raw_score'], 0.91)
        self.assertFalse(report['model_calibrated'])
        self.assertIsNone(report['risk'])
        self.assertEqual(report['localization_png'], png)


class EvidenceForensicsDatabaseTests(AppealFlowFixture):
    @unittest.skipUnless(os.name == 'posix', 'POSIX inherited process lock')
    def test_live_process_lock_prevents_overlap_and_keeps_job_pending(self):
        self.submit()
        claim = claim_next(self.db)
        code = ("import fcntl,sys; f=open(sys.argv[1], 'a+b'); "
                "fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()")
        proc = subprocess.Popen([sys.executable, '-c', code, str(self.db.db_path)+'.forensics.lock'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(proc.stdout.readline().strip(), b'locked')
            self.assertFalse(run_one(self.db, claim[0], claimed=claim[1]))
            with self.db.connect() as conn:
                self.assertEqual(conn.execute('SELECT status FROM evidence_forensics_runs WHERE id=?',
                                              (claim[0],)).fetchone()[0], 'pending')
        finally:
            proc.communicate(timeout=5)
        self.assertTrue(run_one(self.db, claim[0]))

    def test_report_and_retry_require_staff_at_database_boundary(self):
        aid = self.submit()
        evidence = self.db.get_evidence_for_appeal(aid)[0]
        run_id = create_run(self.db,evidence['id'])
        for username in ('', 'S1', 'S2', 'missing'):
            with self.assertRaises(PermissionError):
                self.db.get_evidence_forensics_report(run_id,username=username)
            with self.assertRaises(PermissionError):
                self.db.retry_evidence_forensics(evidence['id'],username=username)
        self.db.student_session_ref = {'token':'student'}
        for method, ident in ((self.db.get_evidence_forensics_report,run_id),
                              (self.db.retry_evidence_forensics,evidence['id'])):
            with self.assertRaises(PermissionError): method(ident,username='admin')
        self.db.student_session_ref = None

    def test_worker_environment_excludes_secrets_and_inherits_lock(self):
        self.submit(); claim = claim_next(self.db)
        actual_run = subprocess.run
        calls = []
        def run(*args, **kwargs):
            calls.append(kwargs)
            return actual_run(*args, **kwargs)
        with patch.dict(os.environ, {'PRIVATE_API_KEY':'must-not-propagate'}), \
             patch('database.evidence_forensics.subprocess.run', side_effect=run):
            self.assertTrue(run_one(self.db, claim[0], claimed=claim[1]))
        self.assertNotIn('PRIVATE_API_KEY', calls[0]['env'])
        self.assertFalse(calls[0].get('shell',False))
        if os.name == 'posix': self.assertEqual(len(calls[0]['pass_fds']),1)

    def test_restart_recovers_upload_committed_before_schedule(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid = self.submit()
        reopened = CBVMSDatabase(self.db.db_path)
        claim = claim_next(reopened)
        self.assertIsNotNone(claim)
        self.assertTrue(run_one(reopened, claim[0], claimed=claim[1]))
        report = reopened.get_evidence_forensics_report(claim[0], username='admin')
        self.assertEqual((report['appeal_id'], report['status']), (aid, 'complete'))

    def test_every_disciplinary_value_and_original_bytes_survive_review_alert(self):
        aid = self.submit()
        evidence = self.db.get_evidence_for_appeal(aid)[0]
        claim = claim_next(self.db)
        def snapshot():
            with self.db.connect() as conn:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                          if r[0] not in ('evidence_forensics_runs', 'sqlite_sequence')]
                return {t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}"')] for t in tables}
        before = snapshot()
        _finish(self.db, claim[0], {'sha256': evidence['file_sha256'],
                'classification': 'review_recommended', 'signals': [{'code': 'c2pa_anomaly'}]},
                claim_token=claim[1][-1])
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.db.get_evidence_file(evidence['id'])['file_data'], picture()[2])
        self.assertTrue(self.db.update_appeal_decision(aid, 'approved', 'Manually reviewed',
                        decided_by='admin', decision_category_code='approval.valid_exemption'))

    def test_populated_legacy_migration_preserves_rows_and_foreign_keys(self):
        aid = self.submit()
        with self.db.connect() as conn:
            conn.execute('DROP TABLE evidence_forensics_runs')
            conn.execute('ALTER TABLE evidence_files DROP COLUMN file_sha256')
            before = [tuple(r) for r in conn.execute('SELECT * FROM evidence_files')]
        self.db.initialize(process_deadlines=False)
        self.db.initialize(process_deadlines=False)
        with self.db.connect() as conn:
            after = [tuple(r)[:-1] for r in conn.execute('SELECT * FROM evidence_files')]
            self.assertEqual(before, after)
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
            self.assertIsNone(conn.execute('SELECT file_sha256 FROM evidence_files').fetchone()[0])
        self.assertEqual(self.db.get_evidence_forensics_history(
            self.db.get_evidence_for_appeal(aid)[0]['id'], username='admin'), [])

    def test_worker_stores_inconclusive_historical_run_without_discipline_effects(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid = self.submit()
        evidence = self.db.get_evidence_for_appeal(aid)[0]
        run_id = create_run(self.db, evidence['id'])
        with self.db.connect() as conn:
            before = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in ('violations','appeals','strikes','strike_events','decision_history')}
        worker_db = CBVMSDatabase(self.db.db_path)
        worker_db.student_session_ref = {'token':'expired-after-submit'}
        self.assertTrue(run_one(worker_db, run_id))
        report = self.db.get_evidence_forensics_report(run_id, username='admin')
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['classification'], 'inconclusive')
        self.assertIsNone(report['risk'])
        self.assertEqual(report['facts']['format'],'PNG')
        with self.db.connect() as conn:
            after = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                     for table in before}
        self.assertEqual(after, before)

    def test_coordinator_start_failure_does_not_fail_valid_appeal(self):
        with patch('database.evidence_forensics.AUTOSTART_OVERRIDE',True), \
             patch('database.evidence_forensics.ForensicsCoordinator.for_db', side_effect=RuntimeError('unavailable')):
            appeal_id = self.submit()
        self.assertIsNotNone(appeal_id)
        self.assertEqual(len(self.db.get_evidence_for_appeal(appeal_id)), 1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM evidence_forensics_runs WHERE appeal_id=?",(appeal_id,)).fetchone()[0],'pending')
        with patch('database.evidence_forensics.AUTOSTART_OVERRIDE',True), \
             patch('database.evidence_forensics.ForensicsCoordinator.for_db', side_effect=RuntimeError('unavailable')):
            extra_id=self.db.insert_evidence_file(appeal_id,'S1',*picture())
        self.assertIsNotNone(extra_id)
        self.assertIsNotNone(self.db.get_evidence_file(extra_id))

    def test_migration_preview_never_starts_the_worker(self):
        with patch('database.evidence_forensics.AUTOSTART_OVERRIDE',True), \
             patch('database.evidence_forensics.ForensicsCoordinator.for_db') as start:
            self.db.initialize(process_deadlines=False)
        start.assert_not_called()

    def test_subprocess_timeout_becomes_error_without_changing_appeal_or_discipline(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid = self.submit()
        evidence = self.db.get_evidence_for_appeal(aid)[0]
        run_id = create_run(self.db,evidence['id'])
        with self.db.connect() as conn:
            before={table:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    for table in ('violations','appeals','strikes','strike_events','decision_history')}
        with patch('database.evidence_forensics.subprocess.run',side_effect=__import__('subprocess').TimeoutExpired('worker',1)):
            self.assertTrue(run_one(self.db,run_id))
        report=self.db.get_evidence_forensics_report(run_id,username='admin')
        self.assertEqual((report['status'],report['error_code']),('error','analysis_timeout'))
        self.assertEqual(self.db.get_appeal_for_violation(self.vid)['status'],'pending')
        with self.db.connect() as conn:
            after={table:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in before}
        self.assertEqual(after,before)

    def test_worker_crash_is_recorded_and_does_not_mutate_appeal(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid=self.submit()
        evidence=self.db.get_evidence_for_appeal(aid)[0]
        run_id=create_run(self.db,evidence['id'])
        with patch('database.evidence_forensics.subprocess.run',return_value=__import__('subprocess').CompletedProcess([],1)):
            self.assertTrue(run_one(self.db,run_id))
        report=self.db.get_evidence_forensics_report(run_id,username='admin')
        self.assertEqual((report['status'],report['error_code']),('error','analysis_failed'))
        self.assertEqual(self.db.get_appeal_for_violation(self.vid)['status'],'pending')

    def test_sqlite_queue_keeps_backlog_over_64_and_claims_only_one_globally(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid=self.submit()
        evidence=self.db.get_evidence_for_appeal(aid)[0]
        with self.db.connect() as conn:
            for _ in range(69):
                conn.execute("INSERT INTO evidence_files(appeal_id,student_id,filename,file_type,file_data,file_sha256) VALUES(?,?,?,?,?,?)",
                    (aid,'S1','more.png','image',evidence['file_data'],evidence['file_sha256']))
            ids=[r[0] for r in conn.execute('SELECT id FROM evidence_files WHERE appeal_id=? ORDER BY id',(aid,))]
        for evidence_id in ids:
            create_run(self.db,evidence_id)
        claims=list(ThreadPoolExecutor(max_workers=2).map(lambda _:claim_next(self.db),range(2)))
        active=[item for item in claims if item]
        self.assertEqual(len(active),1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence_forensics_runs WHERE status='analyzing'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence_forensics_runs WHERE status='pending'").fetchone()[0],len(ids)-1)

    def test_expired_worker_lease_is_terminal_and_old_token_cannot_finish(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid=self.submit()
        entries=self.db.get_evidence_for_appeal(aid)
        first=create_run(self.db,entries[0]['id'])
        second_id=self.db.insert_evidence_file(aid,'S1','again.png','image',entries[0]['file_data'])
        second=create_run(self.db,second_id)
        first_claim=claim_next(self.db)
        self.assertEqual(first_claim[0],first)
        token=first_claim[1][-1]
        with self.db.connect() as conn:
            conn.execute("UPDATE evidence_forensics_runs SET lease_expires_at='2000-01-01 00:00:00' WHERE id=?",(first,))
        second_claim=claim_next(self.db)
        self.assertEqual(second_claim[0],second)
        old_report={'sha256':first_claim[1][0] and __import__('hashlib').sha256(first_claim[1][0]).hexdigest()}
        _finish(self.db,first,old_report,claim_token=token)
        with self.db.connect() as conn:
            row=conn.execute('SELECT status,error_code FROM evidence_forensics_runs WHERE id=?',(first,)).fetchone()
            active=conn.execute("SELECT id FROM evidence_forensics_runs WHERE status='analyzing'").fetchone()[0]
        self.assertEqual(tuple(row),('error','worker_lease_expired'))
        self.assertEqual(active,second)

    def test_retry_race_creates_one_new_pending_history_row(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid=self.submit()
        evidence=self.db.get_evidence_for_appeal(aid)[0]
        old=create_run(self.db,evidence['id'])
        with self.db.connect() as conn:
            conn.execute("UPDATE evidence_forensics_runs SET status='error',created_at='2000-01-01 00:00:00',completed_at=?,error_code='test' WHERE id=?",
                         ('2000-01-01 00:00:00',old))
        with ThreadPoolExecutor(max_workers=4) as pool:
            run_ids=list(pool.map(lambda _:self.db.retry_evidence_forensics(evidence['id'],username='admin'),range(4)))
        self.assertEqual(len(set(run_ids)),1)
        self.assertIsNotNone(run_ids[0])
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM evidence_forensics_runs WHERE evidence_id=?',(evidence['id'],)).fetchone()[0],2)

    def test_privilege_stale_invalidation_and_history(self):
        with patch('database.evidence_forensics.schedule', return_value=None):
            aid = self.submit()
        evidence = self.db.get_evidence_for_appeal(aid)[0]
        run_id = create_run(self.db, evidence['id'])
        with self.db.connect() as conn:
            conn.execute("UPDATE evidence_forensics_runs SET status='complete',classification='review_recommended',signals_json='[{\"code\":\"observation\"}]',localization_png=? WHERE id=?",
                         (b'PNG-map', run_id))
        view = self.db.get_evidence_forensics_report(run_id, username='admin')
        self.assertTrue(view['valid'])
        self.assertEqual(view['localization_png'], b'PNG-map')
        self.db.student_session_ref = {'token':'forged-student-session'}
        with self.assertRaises(PermissionError):
            self.db.get_evidence_forensics_history(evidence['id'], username='admin')
        self.db.student_session_ref = None
        changed = io.BytesIO(); Image.new('RGB',(40,30),'red').save(changed,'PNG')
        with self.db.connect() as conn:
            conn.execute('UPDATE evidence_files SET file_data=? WHERE id=?',(changed.getvalue(),evidence['id']))
        stale = self.db.get_evidence_forensics_report(run_id, username='admin')
        self.assertFalse(stale['valid'])
        self.assertEqual(stale['classification'],'inconclusive')
        self.assertEqual(stale['signals'],[])
        self.assertNotIn('localization_png',stale)

    def test_migration_is_repeat_safe_and_does_not_assign_legacy_hash(self):
        with self.db.connect() as conn:
            conn.execute('ALTER TABLE evidence_files DROP COLUMN file_sha256')
        self.db.initialize(process_deadlines=False)
        self.db.initialize(process_deadlines=False)
        with self.db.connect() as conn:
            self.assertIsNone(conn.execute('SELECT file_sha256 FROM evidence_files LIMIT 1').fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence_forensics_runs").fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
