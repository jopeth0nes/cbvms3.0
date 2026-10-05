"""Disposable SQLite and simulated camera verification; no physical camera or models."""
import csv
from dataclasses import replace
from datetime import timedelta
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from core.attendance import export_csv, utc_stamp
from core.security_writer import SecurityWriter, UnknownEncounter
from database.security_events import migrate_security_events
from tests import test_attendance as fixtures
from tests.test_live_pipeline import PipelineFixture, detection


class UnknownSightingsTests(unittest.TestCase):
    setUp = fixtures.AttendanceTests.setUp

    def save(self, key='event-1', **changes):
        values = dict(event_key=key, source_id='gate', source_label='Gate', session_id='session',
                      observed_at=self.when, snapshot_jpeg=b'snapshot-original')
        values.update(changes)
        return self.db.log_security_event('person:1:1', **values)

    def query(self, **kwargs):
        return self.db.query_unknown_sightings(username='admin', **kwargs)

    def until(self, predicate):
        end = time.monotonic()+5
        while not predicate() and time.monotonic()<end:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_migration_preserves_historical_timestamp_snapshot_and_duplicate_rows(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE security_events(id INTEGER PRIMARY KEY,presence_id TEXT,event_code TEXT,observed_at TEXT,snapshot BLOB)')
        historical = (1,'person:1:1','unknown_person','2020-01-02 03:04:05',b'old-jpeg')
        conn.execute('INSERT INTO security_events VALUES(?,?,?,?,?)', historical)
        conn.execute('INSERT INTO security_events VALUES(?,?,?,?,?)', (2,*historical[1:]))
        migrate_security_events(conn); migrate_security_events(conn)
        self.assertEqual(conn.execute('SELECT id,presence_id,event_code,observed_at,snapshot FROM security_events WHERE id=1').fetchone(),historical)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM security_events').fetchone()[0],2)
        self.assertEqual(conn.execute('SELECT event_key,source_id,source_label FROM security_events LIMIT 1').fetchone(),(None,None,None))
        legacy = self.save(None,source_id=None,source_label=None)
        self.db.initialize(process_deadlines=False)
        row = self.query()['rows'][0]
        self.assertEqual(row['reference_id'],f'legacy:{legacy}')
        self.assertIn('historical',row['source_label'])
        self.assertEqual(self.db.unknown_snapshot(legacy,username='superadmin'),b'snapshot-original')
        self.assertEqual(self.query(filters={'source_id':'__historical__'})['count'],1)

    def test_retry_key_preserves_original_metadata_and_no_student_side_effects(self):
        first = self.save()
        self.assertEqual(self.save(observed_at=self.when+timedelta(hours=1),snapshot_jpeg=b'changed'),first)
        self.assertEqual(self.query()['count'],1)
        self.assertEqual(self.db.unknown_snapshot(first,username='admin'),b'snapshot-original')
        self.assertEqual(self.db.query_attendance()['count'],0)
        self.assertIsNone(self.db.get_active_suspension(self.sid))
        with self.db.connect() as conn:
            for table in ('violations','strikes','student_accounts'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0,table)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM students').fetchone()[0],1)

    def test_filters_manila_midnight_pagination_export_and_roles(self):
        for i in range(61):
            self.save(f'event-{i:03}',source_id='gate' if i<30 else 'yard',
                      source_label='=Gate' if i<30 else 'Yard',observed_at=self.when+timedelta(minutes=i))
        one=self.query(page_size=25);two=self.query(page=1,page_size=25)
        self.assertEqual(one['count'],61)
        self.assertFalse({r['id'] for r in one['rows']} & {r['id'] for r in two['rows']})
        self.assertEqual(self.query(page=100,page_size=25)['page'],2)
        self.assertEqual(self.query(filters={'start':'2026-10-04','end':'2026-10-04'})['count'],5)
        self.assertEqual(self.query(filters={'source_id':'yard'})['count'],31)
        self.assertEqual(self.query(filters={'search':'event-060'})['count'],1)
        target=self.path.parent/'export.csv'
        self.assertEqual(export_csv(self.db,target,view='unknown',username='superadmin'),61)
        with target.open(encoding='utf-8-sig') as stream:
            rows=list(csv.reader(stream))
        self.assertEqual(len(rows),63)
        self.assertIn('Asia/Manila',rows[0][1]);self.assertIn('not unique people',rows[0][1])
        self.assertNotIn('snapshot-original',target.read_text())
        self.assertIn("'=Gate",target.read_text())
        self.assertEqual(export_csv(self.db,target,view='unknown',username='admin',selected_ids=[one['rows'][0]['id']]),1)
        for username in ('missing', '', None):
            with self.assertRaises(PermissionError):self.db.query_unknown_sightings(username=username)
            with self.assertRaises(PermissionError):self.db.unknown_snapshot(1,username=username)
            with self.assertRaises(PermissionError):export_csv(self.db,target,view='unknown',username=username)
        self.db.student_session_ref=object()
        for operation in (lambda:self.query(),lambda:self.db.unknown_snapshot(1,username='admin'),
                          lambda:export_csv(self.db,target,view='unknown',username='admin'),lambda:self.save('blocked')):
            with self.assertRaises(PermissionError):operation()
        self.db.student_session_ref=None
        self.assertEqual(self.query()['count'],61)

    def writer(self, **kwargs):
        writer=SecurityWriter(self.db,retry_seconds=.03,**kwargs)
        self.addCleanup(lambda:(writer.stop(),writer.done.wait(3)))
        return writer

    def fact(self, key='key'):
        return UnknownEncounter(key,'person:1:1',utc_stamp(self.when),'gate','Gate','session',b'jpeg')

    def test_locked_database_retries_once_and_reports_status(self):
        writer=self.writer();cancelled=threading.Event()
        with self.db.connect() as lock:
            lock.execute('BEGIN IMMEDIATE')
            writer.start()
            self.assertTrue(writer.offer(self.fact(),cancelled))
            self.assertFalse(writer.offer(self.fact(),cancelled))
            self.until(lambda:'locked' in writer.status())
            self.assertIn('Retry pending',writer.status())
            lock.rollback()
        self.until(lambda:writer.accepted==1)
        self.assertEqual(self.query()['count'],1)
        self.assertEqual(writer.error,'')

    def test_dashboard_exit_waits_for_unknown_writer(self):
        from ui.dashboard import open_dashboard
        app=MagicMock()
        with patch('ui.dashboard.CBVMSDashboard',return_value=app):
            open_dashboard(database=self.db)
        app._live_worker.processor.security_writer.done.wait.assert_called_once()

    def test_cancelled_queued_work_and_bounded_overflow(self):
        writer=self.writer(capacity=1);cancelled=threading.Event()
        self.assertTrue(writer.offer(self.fact(),cancelled))
        self.assertFalse(writer.offer(self.fact('other'),threading.Event()))
        self.assertIn('NOT QUEUED',writer.status())
        cancelled.set();writer.start()
        self.until(lambda:writer.cancelled==1)
        self.assertEqual(self.query()['count'],0)

    def test_uncertain_commit_retry_uses_same_key(self):
        writer=self.writer();original=writer.database.log_security_event
        calls=[]
        def uncertain(*args,**kwargs):
            result=original(*args,**kwargs);calls.append(kwargs['event_key'])
            if len(calls)==1:raise RuntimeError('simulated lost commit acknowledgement')
            return result
        with patch.object(writer.database,'log_security_event',side_effect=uncertain):
            writer.start();writer.offer(self.fact(),threading.Event())
            self.until(lambda:writer.accepted==1)
        self.assertEqual(calls,['key','key']);self.assertEqual(self.query()['count'],1)

    def test_simulated_two_people_repeated_frames_then_recognized_and_camera_switch(self):
        fx=PipelineFixture();fx.processor.database=self.db
        fx.trainer.predict_proba.return_value={'correct_uniform':.95}
        fx.recognizer.recognize_faces.return_value=[detection('',80),detection('',350,embedding=1)]
        for seq in range(1,9):
            result=fx.analyze(seq,source_id='gate',source_label='Gate')
            fx.persist(result);fx.persist(result)
        before=self.query()['rows']
        self.assertEqual(len(before),2)
        self.assertEqual(len({r['reference_id'] for r in before}),2)
        self.assertEqual(self.db.query_attendance()['count'],0)
        fx.notifier.notify.assert_not_called()
        fx.recognizer.recognize_faces.return_value=[detection(self.sid)]
        for seq in range(9,13):fx.persist(fx.analyze(seq))
        self.assertEqual(self.query()['rows'],before)
        self.assertEqual(self.db.query_attendance()['count'],1)
        fx.recognizer.recognize_faces.return_value=[detection('')]
        for seq in range(13,17):fx.persist(fx.analyze(seq,generation=2,camera_generation=2,source_id='yard',source_label='Yard'))
        self.assertEqual(self.query()['count'],3)
        self.assertEqual(self.query(filters={'source_id':'yard'})['count'],1)
        self.assertEqual(self.db.get_violations_for_student(self.sid),[])

    def test_unknown_confirmation_rejects_ambiguity_loss_stale_cancelled_and_model_failure(self):
        for changes in ({'identity_uncertain':True},{'embedding':()},{}):
            fx=PipelineFixture();fx.processor.database=self.db
            fx.recognizer.recognize_faces.return_value=[dict(detection(''),**changes)]
            if not changes:
                result=fx.analyze(1);fx.persist(result) # first observation cannot confirm
                fx.recognizer.recognize_faces.return_value=[]
                fx.analyze(2)
                fx.recognizer.recognize_faces.return_value=[detection('')]
                result=fx.analyze(3);fx.persist(result) # transient loss reset confirmation
            else:
                result=fx.confirmed();fx.persist(result)
            self.assertEqual(self.query()['count'],0)
        fx=PipelineFixture();fx.processor.database=self.db
        fx.recognizer.recognize_faces.return_value=[detection('')]
        result=fx.confirmed()
        fx.now+=4;fx.persist(result)
        fx.now-=4;fx.latest=replace(fx.latest,frame_id=('other-camera',4));fx.persist(result)
        fx.latest=replace(fx.latest,frame_id=result.task.context.frame_id)
        with patch('core.live_pipeline.MotionProjection.advance',return_value=None):
            fx.latest=replace(fx.latest,frame_id=('camera',5));fx.persist(result)
        fx.cancelled.set();fx.persist(result)
        self.assertEqual(self.query()['count'],0)
        fx=PipelineFixture();fx.processor.database=self.db
        fx.recognizer.last_error='model unavailable'
        with self.assertRaisesRegex(RuntimeError,'unavailable'):fx.analyze(1)
        self.assertEqual(self.query()['count'],0)


if __name__=='__main__':unittest.main()
