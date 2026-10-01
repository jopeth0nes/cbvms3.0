"""Component readiness, staged analysis and real SQLite portal isolation."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import queue
import tempfile
import threading
import time
import unittest

from auth.auth_manager import AuthManager
from core.model_readiness import ModelReadiness, ComponentStatus, face_readiness
from core.live_pipeline import FaceTrackingProcessor, LiveWorker, MonitorTask, MonitorResult
from core.live_state import FrameContext
from core.portal_state import PortalRefresh, comparable, snapshot, violation_group
from database.db_manager import CBVMSDatabase
from tests.test_live_pipeline import PipelineFixture
from tests import test_violation_workflow as workflow
from datetime import datetime, timezone
DETECTED_AT = datetime(2099,8,1,2,tzinfo=timezone.utc)
from tests.test_live_dashboard import dashboard, sample, tick


def wait_until(predicate, timeout=2):
    deadline = time.monotonic()+timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for test worker')
        time.sleep(.005)


class ReadinessTests(unittest.TestCase):
    def test_slow_database_does_not_block_analysis_and_stop_cancels_write(self):
        import numpy as np
        entered, release = threading.Event(), threading.Event()
        committed = []
        def persist(result):
            entered.set()
            release.wait(2)
            if result.task.valid():
                committed.append(result.task.context.frame_id)
        processor = SimpleNamespace(persist=persist, analyze=lambda task:
                                    MonitorResult(task, (), time.monotonic()))
        worker = LiveWorker(processor, persistence_worker=True)
        frame = np.zeros((32, 32, 3), np.uint8)
        def task(i):
            return MonitorTask(FrameContext(1, ('camera', i), time.monotonic()), frame,
                               time.time(), threading.Event())
        worker.start()
        first = task(1)
        try:
            worker.offer(first)
            self.assertTrue(entered.wait(1))
            worker.results.get(timeout=1)
            worker.offer(task(2))
            second = worker.results.get(timeout=1)
            self.assertEqual(second.task.context.frame_id, ('camera', 2))
            worker.stop()
            self.assertTrue(first.cancelled.is_set())
        finally:
            worker.stop()
            release.set()
            self.assertTrue(worker.done.wait(1))
            self.assertTrue(worker.writes_done.wait(1))
        self.assertEqual(committed, [])

    def test_failure_retry_and_no_duplicate_initialization(self):
        state = ModelReadiness()
        loader = MagicMock(side_effect=[RuntimeError('bad weights'), True])
        state.add('face', loader)
        state.start()
        wait_until(lambda: state.snapshot()['face'].state == 'failed')
        self.assertIn('bad weights', state.message())
        state.start()
        self.assertEqual(loader.call_count, 1)
        state.start(retry=True)
        wait_until(lambda: state.ready('face'))
        for _ in range(4):
            state.start(retry=True)
        self.assertEqual(loader.call_count, 2)

    def test_slow_optional_and_stalled_native_owner_do_not_block_face_or_allow_duplicate(self):
        entered, release = threading.Event(), threading.Event()
        def optional():
            entered.set()
            release.wait(2)
            return True
        state = ModelReadiness(timeout=.01)
        loader = MagicMock(side_effect=optional)
        state.add('uniform', loader)
        state.add('face', lambda: True)
        state.start()
        try:
            self.assertTrue(entered.wait(1))
            wait_until(lambda: state.ready('face'))
            wait_until(lambda: state.snapshot()['uniform'].state == 'stalled')
            state.start(retry=True)
            self.assertEqual(loader.call_count, 1)
            self.assertIn('still running', state.message())
        finally:
            release.set()
        wait_until(lambda: state.ready('uniform'))

    def test_login_and_dashboard_share_registry(self):
        recognizer = SimpleNamespace(_ensure_detector=MagicMock(return_value=True),
                                     _ensure_recognition=MagicMock(return_value=True))
        first = face_readiness(recognizer)
        first.start()
        second = face_readiness(recognizer)
        second.start()
        wait_until(lambda: first.ready('face') and first.ready('recognition'))
        self.assertIs(first, second)
        recognizer._ensure_detector.assert_called_once()
        recognizer._ensure_recognition.assert_called_once()

    def test_missing_face_asset_is_specific_and_never_downloaded(self):
        from core.recognizer import FaceRecognizer
        db = MagicMock(); db.get_all_students.return_value = []
        r = FaceRecognizer(db)
        with tempfile.TemporaryDirectory() as directory:
            r.model_dir = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'Missing or incomplete detection weights'):
                r._ensure_detector()
            (r.model_dir/'det_10g.onnx').write_bytes(b'incomplete')
            with self.assertRaisesRegex(RuntimeError, 'Missing or incomplete'):
                r._ensure_detector()

    def test_tracking_offered_without_recognition_or_uniform_and_switch_discards_it(self):
        panel = dashboard()
        panel._readiness._status['recognition'] = ComponentStatus('loading')
        panel._readiness._status['uniform'] = ComponentStatus('failed', 'missing')
        tick(panel)
        panel._tracking_worker.offer.assert_called_once()
        panel._live_worker.offer.assert_not_called()
        task = panel._tracking_worker.offer.call_args.args[0]
        panel._invalidate_monitor()
        self.assertTrue(task.cancelled.is_set())
        self.assertTrue(panel._tracking_worker.results.empty())

    def test_stalled_preview_also_changes_camera_status(self):
        panel = dashboard()
        panel._latest_monitor_sample = lambda: sample(2, 5)
        tick(panel, 10)
        self.assertIn('Reconnecting', panel._status_camera.configure.call_args.kwargs['text'])

    def test_temporary_tracks_stable_and_never_supply_violation_evidence(self):
        fx = PipelineFixture()
        recognizer = SimpleNamespace(detect_faces=lambda frame: [{'box': [80,20,130,90]}])
        tracker = FaceTrackingProcessor(recognizer)
        with patch('core.live_pipeline.time.monotonic', side_effect=lambda: fx.now):
            a = tracker.analyze(fx.task(1)).assessments[0]
            b = tracker.analyze(fx.task(2)).assessments[0]
        self.assertEqual(a.track_id, b.track_id)
        self.assertEqual(b.state, 'Identifying')
        self.assertFalse(b.reliable_identity or b.accepted_categories or b.student_id)

    def test_identity_published_before_slow_uniform_expires(self):
        fx = PipelineFixture()
        published = []
        fx.processor.publish_identity = published.append
        def slow(*args):
            self.assertTrue(published)
            fx.now += 4
            return {'correct_uniform': .1}
        fx.trainer.predict_proba.side_effect = slow
        self.assertIsNone(fx.analyze(1))
        self.assertEqual(len(published), 1)
        self.assertFalse(published[0].assessments[0].accepted_categories)
        fx.database.log_violation.assert_not_called()


class PortalTests(unittest.TestCase):
    STUDENT_A = '2023-00883'
    STUDENT_B = '0000-00002'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name)/'test.db'
        self.db = CBVMSDatabase(self.db_path); self.db.initialize()
        for sid in (self.STUDENT_A,self.STUDENT_B):
            self.db.insert_student(sid,'Fixture','BSIT','3A',b'',b'')
        clock = patch('database.db_manager.utc_now',return_value=DETECTED_AT)
        clock.start();self.addCleanup(clock.stop)

    def _detect(self, student_id=None):
        return self.db.log_violation(student_id or self.STUDENT_A,'Fixture','wrong_uniform')

    def _confirm(self, vid):
        return self.db.confirm_violation(vid,confirmed_at=DETECTED_AT+timedelta(days=1))

    def _count(self,table):
        with self.db.connect() as conn:
            return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_pending_visible_only_to_authenticated_string_owner(self):
        violation = self._detect()
        rows = self.db.get_visible_violations_for_student(self.STUDENT_A, now=DETECTED_AT)
        self.assertEqual([r['id'] for r in rows], [violation])
        self.assertEqual(violation_group(rows[0]), 'Pending')
        self.assertEqual(rows[0]['appeal_window_status'], 'eligible')
        self.assertIsNotNone(rows[0]['appeal_deadline'])
        self.assertTrue(rows[0]['can_appeal'])
        self.assertFalse(rows[0]['strike_active'])
        self.assertEqual(self.db.get_visible_violations_for_student(self.STUDENT_B, now=DETECTED_AT), [])
        self.assertTrue(self._confirm(violation))
        confirmed = self.db.get_visible_violations_for_student(self.STUDENT_A, now=DETECTED_AT+timedelta(days=1))[0]
        self.assertEqual(confirmed['id'], violation)
        self.assertTrue(confirmed['can_appeal'])
        self.assertEqual(confirmed['appeal_deadline'], '2099-08-06 02:00:00')
        self.assertEqual(self._count('violations'), 1)

    def test_background_refresh_sees_committed_record_without_logout(self):
        self.db.insert_student_account(self.STUDENT_A, 'student', 'unique-test-password')
        auth = AuthManager(self.db)
        self.assertEqual(auth.authenticate('student', 'unique-test-password')['student_id'], self.STUDENT_A)
        self.assertIsNone(auth.authenticate('student', 'student123'))
        refresh = PortalRefresh(self.db, self.STUDENT_A)
        with patch('database.db_manager.utc_now', return_value=DETECTED_AT):
            refresh.request(); wait_until(lambda: not refresh.results.empty())
            first, error = refresh.poll(self.STUDENT_A)
            self.assertFalse(error)
            self.assertEqual(first['_violations'], [])
            violation = self._detect()
            self._detect(student_id=self.STUDENT_B)
            refresh.request(); wait_until(lambda: not refresh.results.empty())
            second, error = refresh.poll(self.STUDENT_A)
        self.assertFalse(error)
        self.assertEqual([v['id'] for v in second['_violations']], [violation])
        self.assertNotEqual(comparable(first), comparable(second))
        refresh.close()
        self.assertFalse(refresh.request())

    def test_refresh_rejects_old_session_and_other_owner(self):
        entered, release = threading.Event(), threading.Event()
        def loader(db, owner):
            entered.set(); release.wait(2)
            return {'owner': owner}
        refresh = PortalRefresh(self.db, self.STUDENT_A, loader)
        refresh.request(); self.assertTrue(entered.wait(1))
        self.assertFalse(refresh.request())
        refresh.close(); release.set()
        wait_until(lambda: not refresh.results.empty())
        self.assertIsNone(refresh.poll(self.STUDENT_A))
        refresh = PortalRefresh(self.db, self.STUDENT_A, lambda db, owner: {'owner': owner})
        refresh.request(); wait_until(lambda: not refresh.results.empty())
        self.assertIsNone(refresh.poll(self.STUDENT_B))

    def test_same_database_path_regardless_of_working_directory(self):
        import os
        before = CBVMSDatabase().db_path
        cwd = os.getcwd()
        try:
            os.chdir(self._tmp.name)
            self.assertEqual(CBVMSDatabase().db_path, before)
        finally:
            os.chdir(cwd)
        self.assertEqual(CBVMSDatabase(self.db_path).db_path, self.db.db_path)

    def test_countdown_alone_does_not_rebuild_unchanged_list(self):
        a = {'_violations': [{'id': 1, 'appeal_remaining_seconds': 200}]}
        b = {'_violations': [{'id': 1, 'appeal_remaining_seconds': 190}]}
        self.assertEqual(comparable(a), comparable(b))


if __name__ == '__main__':
    unittest.main()
