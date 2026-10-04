"""Synthetic cameras through the existing identity pipeline and real async SQLite writer."""
from dataclasses import replace
from datetime import datetime, timezone
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.camera import CameraSample
from core.live_pipeline import LiveWorker, FaceTrackingProcessor
from core.live_state import FrameContext
from core.attendance import utc_stamp
from tests import test_attendance as fixtures
from tests.test_live_pipeline import PipelineFixture, detection


class AttendancePipelineTests(unittest.TestCase):
    setUp=fixtures.AttendanceTests.setUp

    def until(self,predicate,timeout=5):
        deadline=time.monotonic()+timeout
        while not predicate() and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(predicate())

    def test_two_known_students_compliant_and_unknown_survive_blocked_discipline_queue(self):
        self.db.insert_student('0002/B','Second','BSIT','2B',b'face2',b'photo2')
        fx=PipelineFixture();fx.processor.database=self.db
        fx.recognizer.recognize_faces.return_value=[detection(self.sid),detection('0002/B',350,embedding=1),detection('',520,embedding=2)]
        fx.trainer.predict_proba.return_value={'correct_uniform':.95}
        tracker=FaceTrackingProcessor(SimpleNamespace(detect_faces=lambda _:fx.recognizer.recognize_faces.return_value),fx.detector)
        worker=LiveWorker(fx.processor,persistence_worker=True)
        release=threading.Event();blocked=threading.Event()
        def blocked_discipline(result):
            blocked.set();release.wait(4)
        self.addCleanup(lambda:(release.set(),worker.stop(),worker.done.wait(3),worker.writes_done.wait(3),worker.attendance_writer.done.wait(3)))
        with patch.object(fx.processor,'persist',side_effect=blocked_discipline), patch('core.live_pipeline.skin_fraction',return_value=0):
            worker.start()
            with self.db.connect() as lock:
                lock.execute('BEGIN IMMEDIATE')
                for seq in range(1,9):
                    task=fx.task(seq)
                    task=replace(task,context=FrameContext(1,('fixture-camera',seq),time.monotonic()),
                        source_id='gate-fixture',source_label='Gate fixture')
                    fx.latest=CameraSample(task.frame,task.context.frame_id,task.context.captured_at)
                    localized=tracker.analyze(task)
                    task=replace(task,observations=localized.observations)
                    worker.offer(task)
                    result=worker.results.get(timeout=3)
                    self.assertEqual(result.task.context,task.context)
                    self.assertFalse(worker.last_error)
                self.assertTrue(blocked.is_set())
                self.until(lambda:'locked' in worker.attendance_writer.error)
                self.until(lambda:len(list(self.path.parent.glob('*.attendance-pending/*/*.json')))==2)
                self.assertEqual(self.db.query_attendance()['count'],0)
                lock.rollback()
            self.until(lambda:worker.attendance_writer.accepted==2)
            release.set()
        events=self.db.query_attendance(view='events')['rows']
        self.assertEqual({r['student_id'] for r in events},{self.sid,'0002/B'})
        self.assertTrue(all(r['observed_at']==utc_stamp(datetime.fromtimestamp(1800000002,timezone.utc)) for r in events))
        self.assertTrue(all(r['source_id']=='gate-fixture' and r['session_id']=='fixture-camera' for r in events))
        self.assertTrue(all(r['course']=='Information Technology' for r in events))
        self.assertEqual(self.db.get_violations_for_student(self.sid),[])
        self.assertEqual(self.db.query_attendance()['rows'][0]['sighting_count'],1)

    def test_attendance_inherits_the_existing_identity_confirmation_threshold(self):
        from core.live_pipeline import LiveProcessor
        from core.live_state import LiveState, LiveConfig
        fx=PipelineFixture()
        fx.processor=LiveProcessor(self.db,fx.recognizer,fx.detector,fx.trainer,fx.matcher,fx.notifier,
            latest_sample=lambda:fx.latest,state=LiveState(LiveConfig(identity_frames=4)))
        fx.recognizer.recognize_faces.return_value=[detection(self.sid)]
        worker=LiveWorker(fx.processor,persistence_worker=True)
        for seq in (1,2,3):
            fx.analyze(seq)
            self.assertEqual(worker.attendance_writer.queue.qsize(),0)
        fx.analyze(4)
        self.assertEqual(worker.attendance_writer.queue.qsize(),1)

    def test_confirmed_identity_is_recorded_even_when_uniform_stage_expires(self):
        fx=PipelineFixture();fx.processor.database=self.db
        fx.recognizer.recognize_faces.return_value=[detection(self.sid)]
        worker=LiveWorker(fx.processor,persistence_worker=True)
        def slow_uniform(*args):
            fx.now+=4
            return {'correct_uniform': .95}
        fx.trainer.predict_proba.side_effect=slow_uniform
        for seq in (1,2,3):
            self.assertIsNone(fx.analyze(seq))
        self.assertEqual(worker.attendance_writer.queue.qsize(),1)
        fact,cancelled=worker.attendance_writer.queue.get_nowait()
        self.assertEqual(fact.student_id,self.sid)
        self.assertEqual(fact.observed_at,utc_stamp(datetime.fromtimestamp(1800000002,timezone.utc)))
        self.assertFalse(cancelled.is_set())

    def test_qualification_rejects_stale_cancelled_ambiguous_and_wrong_session(self):
        fx=PipelineFixture();fx.processor.database=self.db
        fx.recognizer.recognize_faces.return_value=[detection(self.sid)]
        result=fx.confirmed()
        sink=[]
        class Sink:
            def offer(self,fact,cancelled):sink.append(fact)
        fx.processor.attendance_writer=Sink()
        with patch('core.live_pipeline.time.monotonic',side_effect=lambda:fx.now):
            fx.processor.submit_attendance(replace(result,assessments=tuple(replace(a,reliable_identity=False) for a in result.assessments)))
            fx.latest=replace(fx.latest,frame_id=('other-session',4))
            fx.processor.submit_attendance(result)
            fx.latest=CameraSample(result.task.frame,result.task.context.frame_id,result.task.context.captured_at)
            fx.now+=4;fx.processor.submit_attendance(result)
            fx.now-=4;fx.cancelled.set();fx.processor.submit_attendance(result)
        self.assertEqual(sink,[])
        self.assertEqual(self.db.query_attendance()['count'],0)

    def test_exclusive_lock_fails_read_quickly_without_queuing_unqualified_identity(self):
        fx=PipelineFixture();fx.processor.database=self.db
        worker=LiveWorker(fx.processor,persistence_worker=True)
        self.assertLessEqual(fx.processor.read_database.timeout,.1)
        with self.db.connect() as lock:
            lock.execute('BEGIN EXCLUSIVE')
            started=time.monotonic()
            with self.assertRaisesRegex(Exception,'locked'):
                fx.analyze(1)
            self.assertLess(time.monotonic()-started,.75)
            self.assertEqual(worker.attendance_writer.queue.qsize(),0)
            lock.rollback()


if __name__=='__main__':unittest.main()
