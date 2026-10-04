"""Bounded queues, locks, restart journals and cancellation with real SQLite."""
from dataclasses import replace
import json
import threading
import time
import unittest
from unittest.mock import patch
from tests import test_attendance as fixtures
from core.attendance_writer import AttendanceWriter


class AttendanceWriterTests(unittest.TestCase):
    setUp = fixtures.AttendanceTests.setUp
    fact = fixtures.AttendanceTests.fact
    def until(self,predicate,timeout=5):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:time.sleep(.02)
        self.assertTrue(predicate())

    def start_writer(self,**kwargs):
        writer=AttendanceWriter(self.db,**kwargs)
        self.addCleanup(lambda: (writer.stop(),writer.done.wait(3)))
        writer.start()
        return writer

    def test_real_database_lock_retry_keeps_observed_time_and_snapshot(self):
        worker=self.start_writer(retry_seconds=.05)
        cancel=threading.Event()
        with self.db.connect() as lock:
            lock.execute('BEGIN IMMEDIATE')
            self.assertTrue(worker.offer(self.fact(),cancel))
            self.until(lambda:'locked' in worker.error)
            self.assertEqual(self.db.query_attendance(view='events')['count'],0)
            self.assertTrue(list(self.path.parent.glob('*.attendance-pending/*/*.json')))
            lock.rollback()
        self.until(lambda:worker.accepted==1)
        row=self.db.query_attendance(view='events')['rows'][0]
        self.assertEqual(row['observed_at'],self.fact().observed_at)
        self.assertEqual(row['source_id'],'gate-1')
        self.assertEqual(row['frame_id'],'0')
        self.assertEqual(list(self.path.parent.glob('*.attendance-pending/*/*.json')),[])

    def test_cancel_while_locked_prevents_event_and_restart_replay(self):
        worker=self.start_writer(retry_seconds=.05)
        cancelled=threading.Event()
        with self.db.connect() as lock:
            lock.execute('BEGIN IMMEDIATE')
            worker.offer(self.fact(),cancelled)
            self.until(lambda:'locked' in worker.error)
            cancelled.set()
            lock.rollback()
        self.until(lambda:worker.cancelled==1)
        worker.stop();self.assertTrue(worker.done.wait(3))
        restarted=self.start_writer()
        time.sleep(.3)
        self.assertEqual(self.db.query_attendance(view='events')['count'],0)
        self.assertEqual(list(self.path.parent.glob('*.attendance-pending/*/*.json')),[])

    def test_journal_replay_after_restart_and_after_committed_retry(self):
        worker=self.start_writer(retry_seconds=.05)
        with self.db.connect() as lock:
            lock.execute('BEGIN IMMEDIATE')
            worker.offer(self.fact(),threading.Event())
            self.until(lambda:'locked' in worker.error)
            worker.stop();self.assertTrue(worker.done.wait(3))
            self.assertEqual(worker.saved_pending,1)
            lock.rollback()
        restarted=self.start_writer()
        self.until(lambda:restarted.accepted==1)
        restarted.stop();self.assertTrue(restarted.done.wait(3))
        # Simulate commit succeeding just before process loss prevented journal cleanup.
        base=self.path.with_name(self.path.name+'.attendance-pending')/'orphan'
        base.mkdir()
        (base/(self.fact().key+'.json')).write_text(json.dumps(self.fact().payload()))
        third=self.start_writer()
        self.until(lambda:not list(base.glob('*.json')))
        self.assertEqual(self.db.query_attendance(view='events')['count'],1)
        self.assertEqual(self.db.query_attendance()['rows'][0]['sighting_count'],1)

    def test_cross_camera_suppression_does_not_extend_the_actual_cooldown(self):
        first=self.start_writer();second=self.start_writer()
        cancelled=threading.Event()
        first.offer(self.fact(),cancelled)
        self.until(lambda:first.accepted==1)
        second.offer(self.fact(299,source_id='gate-2',session_id='session-2'),cancelled)
        self.until(lambda:second.queue.empty() and not second.active)
        self.assertEqual(second.accepted,0)
        self.assertTrue(second.offer(self.fact(300,source_id='gate-2',session_id='session-2'),cancelled))
        self.until(lambda:second.accepted==1)
        self.assertEqual(self.db.query_attendance(view='events')['count'],2)

    def test_failed_writer_can_retry_and_corrupt_journal_stays_visible(self):
        worker=AttendanceWriter(self.db)
        self.addCleanup(lambda:(worker.stop(),worker.done.wait(3)))
        worker.offer(self.fact(),threading.Event())
        with patch('core.attendance_writer.Path.mkdir',side_effect=OSError('disk unavailable')):
            worker.start();self.assertTrue(worker.done.wait(3))
        self.assertIn('Writer unavailable',worker.status())
        self.assertFalse(worker.offer(self.fact(600),threading.Event()))
        self.assertTrue(worker.retry())
        self.until(lambda:worker.accepted==1)
        worker.stop();self.assertTrue(worker.done.wait(3))
        base=self.path.with_name(self.path.name+'.attendance-pending')/'corrupt'
        base.mkdir();broken=base/'broken.json';broken.write_text('not JSON')
        restarted=self.start_writer()
        self.until(lambda:'Unreadable' in restarted.status())
        restarted.offer(self.fact(600),threading.Event())
        self.until(lambda:restarted.accepted==1)
        self.assertIn('Unreadable',restarted.status())
        self.assertTrue(broken.exists())

    def test_another_running_writer_does_not_recover_owned_journals(self):
        first=self.start_writer()
        with self.db.connect() as lock:
            lock.execute('BEGIN IMMEDIATE')
            first.offer(self.fact(),threading.Event())
            self.until(lambda:'locked' in first.error)
            second=self.start_writer()
            time.sleep(.4)
            self.assertFalse(second.active)
            self.assertEqual(second.accepted,0)
            lock.rollback()
        self.until(lambda:first.accepted==1)
        self.assertEqual(self.db.query_attendance(view='events')['count'],1)

    def test_queue_is_bounded_overflow_is_visible_and_two_students_are_independent(self):
        worker=AttendanceWriter(self.db,capacity=2)
        cancelled=threading.Event()
        self.db.insert_student('0002','Second','BSIT','1A',b'',b'')
        self.assertTrue(worker.offer(self.fact(),cancelled))
        self.assertTrue(worker.offer(self.fact(student_id='0002'),cancelled))
        self.assertFalse(worker.offer(self.fact(student_id='0003'),cancelled))
        self.assertEqual(worker.queue.qsize(),2)
        self.assertIn('NOT QUEUED',worker.status())
        self.addCleanup(lambda:(worker.stop(),worker.done.wait(3)))
        worker.start()
        self.until(lambda:worker.accepted==2)
        self.assertEqual(self.db.query_attendance()['count'],2)
        # Sustained frames are throttled in memory, and still guarded by DB cooldown on restart.
        for i in range(1,200):
            self.assertFalse(worker.offer(self.fact(i),cancelled))
        self.assertEqual(worker.queue.qsize(),0)


if __name__=='__main__':unittest.main()
