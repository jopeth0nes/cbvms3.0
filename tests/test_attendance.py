"""Campus-sighting persistence, migration, filters, exports and authorization."""
import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from core.academics import COURSES, academic_values
from core.attendance import Sighting, utc_stamp, manila_date, export_csv
from database.db_manager import CBVMSDatabase
from database.attendance import migrate_attendance


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'test.db'
        self.db=CBVMSDatabase(self.path)
        self.db.initialize(process_deadlines=False)
        self.sid='0001-02/A'
        self.db.insert_student(self.sid,'=Student','BSIT','3A',b'face',b'photo')
        self.when=datetime(2026,10,4,15,55,tzinfo=timezone.utc)

    def fact(self,seconds=0,**changes):
        fact=Sighting(self.sid,'=Student',utc_stamp(self.when+timedelta(seconds=seconds)),
            'gate-1','Gate 1','session-1',str(seconds),1,1,'temporary-track',
            'Enrolled',*COURSES['it'],'3rd Year','A',1,'Semester 1','2026-2027')
        return replace(fact,**changes)

    def save(self,fact,**kwargs):
        return self.db.record_attendance(fact.student_id,sighting=fact,**kwargs)

    def test_cooldown_boundaries_across_cameras_and_midnight(self):
        self.assertTrue(self.save(self.fact()))
        for t in (1,299,299.999999):
            self.assertFalse(self.save(self.fact(t,source_id='other',session_id='other')))
        self.assertTrue(self.save(self.fact(300,source_id='other',session_id='other')))
        rows=self.db.query_attendance()['rows']
        self.assertEqual({r['attendance_date'] for r in rows},{'2026-10-04','2026-10-05'})
        self.assertTrue(all(r['sighting_count']==1 for r in rows))
        self.assertEqual(manila_date('2026-10-04 16:00:00+00:00'),'2026-10-05')
        # Cooldown spans midnight; a new date does not bypass it.
        self.assertFalse(self.save(self.fact(301,source_id='third',session_id='third')))

    def test_delayed_out_of_order_writes_and_retry_after_restart(self):
        self.assertTrue(self.save(self.fact(600)))
        self.assertFalse(self.save(self.fact(450)))
        self.assertTrue(self.save(self.fact(300)))
        reopened=CBVMSDatabase(self.path)
        reopened.initialize(process_deadlines=False)
        self.assertFalse(reopened.record_attendance(self.sid,sighting=self.fact(600)))
        reopened.set_attendance_cooldown(0,actor='admin')
        self.assertFalse(reopened.record_attendance(self.sid,sighting=self.fact(450)))
        self.assertEqual(reopened.query_attendance(view='events')['count'],2)
        row=reopened.query_attendance()['rows'][0]
        self.assertEqual(row['first_seen'],self.fact(300).observed_at)
        self.assertEqual(row['last_seen'],self.fact(600).observed_at)
        self.assertEqual(row['sighting_count'],2)

    def test_concurrent_cameras_and_same_fact_retries(self):
        barrier=threading.Barrier(9)
        results,errors=[],[]
        def insert(index):
            try:
                barrier.wait()
                fact=self.fact(session_id=f'camera-{index}',source_id=f'camera-{index}')
                results.append(self.save(fact))
            except Exception as exc:
                errors.append(exc)
        threads=[threading.Thread(target=insert,args=(i,)) for i in range(8)]
        for thread in threads:thread.start()
        barrier.wait()
        for thread in threads:thread.join(5)
        self.assertEqual(errors,[])
        self.assertEqual(results.count(True),1)
        self.assertEqual(self.db.query_attendance(view='events')['count'],1)
        self.assertEqual(self.db.query_attendance()['rows'][0]['sighting_count'],1)

    def test_snapshot_is_immutable_and_attendance_has_no_discipline_effects(self):
        tables=('violations','strikes','student_suspensions','student_notifications','student_accounts')
        with self.db.connect() as conn:
            before={t:[tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
        fact=self.fact()
        self.db.update_student_details(self.sid,student_status='Graduate',contacts={},changed_by='admin',reason='Graduated',
            academics=academic_values(*COURSES['nursing'],'Not applicable','B'))
        self.assertTrue(self.save(fact))
        row=self.db.query_attendance(view='events')['rows'][0]
        self.assertEqual(row['course'],'Information Technology')
        self.assertEqual(row['student_status'],'Enrolled')
        self.assertEqual(row['observed_at'],fact.observed_at)
        with self.db.connect() as conn:
            for table in tables:
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM {table}')],before[table])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE attendance_events SET course='Changed'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM attendance_events')
        self.assertEqual(self.db.get_student_by_student_id(self.sid)['encoding'],b'face')

    def test_cancelled_transaction_rolls_back_receipt_event_and_summary(self):
        calls=iter((True,False))
        self.assertFalse(self.save(self.fact(),valid_if=lambda:next(calls)))
        with self.db.connect() as conn:
            for table in ('attendance_receipts','attendance_events','attendance'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
        self.assertTrue(self.save(self.fact()))

    def test_legacy_summary_retains_provenance_without_fabricated_events(self):
        path=Path(self.tmp.name)/'legacy.db'
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE attendance(id INTEGER PRIMARY KEY,student_id TEXT,student_name TEXT,attendance_date TEXT,first_seen TEXT,last_seen TEXT,UNIQUE(student_id,attendance_date))')
            conn.execute("INSERT INTO attendance VALUES(7,'0001/A','Old Name','2020-01-01','2020-01-01 01:00:00','2020-01-01 02:00:00')")
        legacy=CBVMSDatabase(path)
        legacy.initialize(process_deadlines=False)
        legacy.initialize(process_deadlines=False)
        row=legacy.query_attendance()['rows'][0]
        self.assertEqual(row['id'],7)
        self.assertEqual(row['student_id'],'0001/A')
        self.assertEqual(row['first_seen'],'2020-01-01 01:00:00')
        self.assertEqual(row['sighting_count'],0)
        self.assertEqual(row['legacy_summary'],1)
        self.assertIn('unknown',row['provenance'])
        self.assertEqual(row['course'],'Unspecified/Needs review')
        self.assertEqual(legacy.query_attendance(view='events')['rows'],[])
        with legacy.connect() as conn:
            self.assertEqual(tuple(conn.execute('SELECT * FROM attendance_legacy').fetchone()),
                (7,'0001/A','Old Name','2020-01-01','2020-01-01 01:00:00','2020-01-01 02:00:00'))

    def test_pagination_combined_filters_and_both_export_scopes(self):
        self.db.set_attendance_cooldown(0,actor='admin')
        for i in range(61):
            self.save(self.fact(i,frame_id=str(i)))
        filters=dict(start='2026-10-04',end='2026-10-04',semester_id='1',college_department=COURSES['it'][0],
            course=COURSES['it'][1],report_year_level='3rd Year',report_section='A',student_id=self.sid,name='Student',source_id='gate-1')
        first=self.db.query_attendance(view='events',filters=filters,page_size=25)
        second=self.db.query_attendance(view='events',filters=filters,page_size=25,page=1)
        last=self.db.query_attendance(view='events',filters=filters,page_size=25,page=2)
        self.assertEqual((first['count'],len(first['rows']),len(second['rows']),len(last['rows'])),(61,25,25,11))
        self.assertFalse({r['id'] for r in first['rows']} & {r['id'] for r in second['rows']})
        self.assertEqual(self.db.query_attendance(filters=filters)['count'],1)
        for key,value in dict(student_id='001-02/A',report_section='Z',source_id='absent',semester_id='2',course='Nursing',name='nobody').items():
            self.assertEqual(self.db.query_attendance(view='events',filters=dict(filters,**{key:value}))['count'],0)
        all_path=Path(self.tmp.name)/'all.csv'
        selected_path=Path(self.tmp.name)/'selected.csv'
        self.assertEqual(export_csv(self.db,all_path,view='events',filters=filters),61)
        ids=[r['id'] for r in first['rows'][:2]]
        self.assertEqual(export_csv(self.db,selected_path,view='events',filters=filters,selected_ids=ids),2)
        with all_path.open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.reader(stream))
        metadata=json.loads(rows[0][1])
        self.assertEqual(metadata['filters']['student_id'],self.sid)
        self.assertIn('Asia/Manila',metadata['timezone'])
        self.assertEqual(rows[2][1],self.sid)
        self.assertEqual(rows[2][2],"'=Student")
        self.assertIn('UTC+08:00',rows[2][3])

    def test_invalid_filters_cooldown_and_student_sessions_are_rejected(self):
        for filters in ({'start':'2026-99-01'},{'start':'2026-10-05','end':'2026-10-04'},{'raw_sql':'DROP'}):
            with self.assertRaises(ValueError):self.db.query_attendance(filters=filters)
        for seconds in (-1,86401,True,'300'):
            with self.assertRaises(ValueError):self.db.set_attendance_cooldown(seconds,actor='admin')
        with self.assertRaises(PermissionError):self.db.set_attendance_cooldown(5,actor='student')
        self.db.set_attendance_cooldown(60,actor='superadmin')
        self.db.student_session_ref={'token':'student-session'}
        with self.assertRaises(PermissionError):self.db.query_attendance()
        with self.assertRaises(PermissionError):list(self.db.iter_attendance())
        with self.assertRaises(PermissionError):self.save(self.fact())


if __name__=='__main__':unittest.main()
