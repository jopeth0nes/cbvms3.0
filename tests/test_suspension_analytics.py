"""Exact reporting fixtures; no production DB or lifecycle transitions during reads."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from core.academics import COLLEGES, NEEDS_REVIEW
from core.suspension_analytics import (ReportFilter, load_report, drill_rows, UNAVAILABLE, MANILA)
from core.report_worker import ReportWorker
from database.db_manager import CBVMSDatabase

NOW=datetime(2026,10,5,8,tzinfo=timezone.utc)


def seed(db):
    db.initialize(process_deadlines=False)
    term=db.get_current_academic_term()['id']
    old=db.set_current_academic_term('Semester 2','2025-2026')['id']
    with db.connect() as c:
        c.execute('UPDATE academic_terms SET is_current=0')
        c.execute('UPDATE academic_terms SET is_current=1 WHERE id=?',(term,))
        for sid,name,course,year in [('S1','Alex Rivera','BSIT','1A'),('S2','Bea Santos','BSN','2B'),('S3','Casey Tan','','')]:
            c.execute('INSERT INTO students(student_id,name,course,year_and_section) VALUES(?,?,?,?)',(sid,name,course,year))
        violations=[
            (1,'S1','wrong_uniform','2026-10-04 15:59:59','pending_review',term),
            (2,'S1','wrong_uniform','2026-10-04 16:00:00','confirmed',term),
            (3,'S2','earring','2026-10-05 01:00:00','dismissed',term),
            (4,'S2','wrong_uniform','2026-10-05 02:00:00','confirmed',term),
            (5,'S3','wrong_uniform','2026-10-05 03:00:00','confirmed',term),
            (6,'deleted','wrong_uniform','2026-10-05 04:00:00','confirmed',old),
            (7,'S1','wrong_uniform','2026-10-05 05:00:00','pending_review',old),
            (8,'S2','earring','2026-10-05 06:00:00','confirmed',term),
            (9,None,'unknown_person','2026-10-05 07:00:00','pending_review',term),
            (10,'S1','wrong_uniform','2026-09-29 04:00:00','confirmed',term),
            (11,'S3','earring','invalid','reviewed',term)]
        for id,sid,code,stamp,status,t in violations:
            c.execute('''INSERT INTO violations(id,student_id,student_name,violation_type,violation_code,timestamp,status,semester_id)
                VALUES(?,?,?,?,?,?,?,?)''',(id,sid,sid,code,code,stamp,status,t))
        for vid,at,active in [(2,'2026-10-05 16:00:00',1),(4,'2026-10-05 02:00:00',0),
                (5,'2026-10-05 03:00:00',1),(6,'2026-10-05 04:00:00',1),(8,'2026-10-05 06:00:00',1),
                (10,'2026-10-05 07:00:00',1)]:
            row=next(r for r in violations if r[0]==vid)
            c.execute('INSERT INTO strikes(violation_id,student_id,violation_code,semester_id,awarded_at,is_active) VALUES(?,?,?,?,?,?)',
                      (vid,row[1],row[2],row[5],at,active))
        for vid,sid,status in [(4,'S2','approved'),(5,'S3','rejected'),(8,'S2','pending')]:
            c.execute('INSERT INTO appeals(violation_id,student_id,reason,status) VALUES(?,?,?,?)',(vid,sid,'Fixture appeal',status))
        suspensions=[
            (1,'S1','2026-10-04 16:00:00','2026-10-07 00:00:00','2026-10-05 00:00:00',2),
            (2,'S1','2026-10-05 00:00:00','2026-10-12 00:00:00',None,2),
            (3,'S2','2026-10-01 00:00:00','2026-10-04 00:00:00',None,3),
            (4,'S2','2026-10-08 00:00:00',None,None,3),
            (5,'S3','2026-10-05 03:00:00',None,None,None),
            (6,'deleted','2026-10-05 04:00:00',None,None,None),
            (7,'S1','2026-10-05 05:00:00',None,'2026-10-05 06:00:00',10)]
        for id,sid,start,end,lifted,vid in suspensions:
            c.execute('''INSERT INTO student_suspensions(id,student_id,starts_at,ends_at,lifted_at,violation_id,reason,imposed_by,imposed_at,lift_reason)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',(id,sid,start,end,lifted,vid,'Fixture suspension','test',start,
                'Replaced by automatic strike escalation' if id==1 else 'Test lift' if lifted else None))
        c.execute('INSERT INTO automatic_suspension_awards VALUES(?,?,?,?)',('S1',term,3,1))
        c.execute('INSERT INTO automatic_suspension_awards VALUES(?,?,?,?)',('S1',term,5,2))
        c.execute('INSERT INTO automatic_suspension_awards VALUES(?,?,?,?)',('deleted',old,3,6))
        for i in range(4):
            c.execute('INSERT INTO student_notifications(student_id,title,message,violation_id) VALUES(?,?,?,?)',('S1','Fixture','Test',2))
        c.execute("INSERT INTO security_events(presence_id,event_code,observed_at) VALUES('x','unknown_person','2026-10-05 00:00:00')")
    return term,old


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.db=CBVMSDatabase(Path(temp.name)/'fixture.db')
        self.term,self.old=seed(self.db)
        self.custom=ReportFilter(period='custom',start='2026-10-05',end='2026-10-05',interval='Day')

    def report(self,f=None): return load_report(self.db,'admin',f or ReportFilter(),now=NOW)

    def test_exact_counts_date_basis_and_no_join_inflation(self):
        r=self.report(self.custom)
        self.assertEqual(r['counts'],dict(recorded=7,finalized=3,suspensions=5,students=3,active=3))
        self.assertEqual({x['id'] for x in r['records']['finalized']},{5,6,10})
        self.assertEqual(dict(r['outcomes']),{'Finalized':3,'Resolved / dismissed':2,'Pending review':1,'Appeal pending':1})
        self.assertEqual(r['trends']['recorded'],[('2026-10-05',7)])
        self.assertEqual(r['trends']['finalized'],[('2026-10-05',3)])
        self.assertEqual(r['trends']['suspensions'],[('2026-10-05',5)])

    def test_current_and_historical_terms_use_association_not_term_creation_date(self):
        self.assertEqual(self.report()['counts'],dict(recorded=8,finalized=3,suspensions=5,students=2,active=3))
        r=self.report(ReportFilter(period=f'term:{self.old}'))
        self.assertEqual(r['counts'],dict(recorded=2,finalized=1,suspensions=1,students=1,active=3))
        r=self.report(ReportFilter(period='unassigned'))
        self.assertEqual([x['id'] for x in r['records']['suspensions']],[5])
        self.assertEqual(r['counts']['recorded'],0)

    def test_all_data_undated_records_and_week_month_buckets(self):
        r=self.report(ReportFilter(period='all',interval='Week'))
        self.assertEqual(r['counts'],dict(recorded=10,finalized=4,suspensions=7,students=4,active=3))
        self.assertEqual(r['undated']['recorded'],1)
        self.assertEqual(r['trends']['recorded'],[('2026-09-28',2),('2026-10-05',7)])
        r=self.report(ReportFilter(period='all',interval='Month'))
        self.assertEqual(r['trends']['recorded'],[('2026-09-01',1),('2026-10-01',8)])

    def test_midnight_exclusive_end_and_active_snapshot_ignores_period(self):
        r=self.report(replace(self.custom,start='2026-10-04',end='2026-10-04'))
        self.assertEqual([x['id'] for x in r['records']['recorded']],[1])
        self.assertEqual(r['counts']['active'],3)
        r=self.report(replace(self.custom,start='2026-10-06',end='2026-10-06'))
        self.assertEqual([x['id'] for x in r['records']['finalized']],[2])
        self.assertEqual(r['counts']['recorded'],0)

    def test_rankings_and_distinct_people_are_different_measures(self):
        r=self.report(replace(self.custom,rank='suspensions'))
        self.assertEqual(dict(r['rankings']['course']),{'Information Technology':3,NEEDS_REVIEW:1,UNAVAILABLE:1})
        r=self.report(replace(self.custom,rank='students'))
        self.assertEqual(dict(r['rankings']['course']),{'Information Technology':1,NEEDS_REVIEW:1,UNAVAILABLE:1})
        self.assertEqual(len(drill_rows(r,'students',field='course',value='Information Technology')),1)

    def test_filter_intersections_and_drills_are_exact(self):
        f=replace(self.custom,college=COLLEGES[0],course='Information Technology',year='1st Year',category='wrong_uniform')
        r=self.report(f)
        self.assertEqual(r['counts'],dict(recorded=2,finalized=1,suspensions=3,students=1,active=1))
        for metric in r['counts']:
            self.assertEqual(len(drill_rows(r,metric)),r['counts'][metric])
        self.assertEqual(self.report(replace(f,course='Nursing'))['counts']['recorded'],0)
        r=self.report(replace(self.custom,outcome='Resolved / dismissed'))
        self.assertEqual(r['counts']['recorded'],2); self.assertEqual(r['counts']['finalized'],0)
        self.assertEqual(r['counts']['suspensions'],5)
        r=self.report(replace(self.custom,suspension_status='Lifted / cancelled'))
        self.assertEqual(r['counts']['suspensions'],2); self.assertEqual(r['counts']['active'],3)

    def test_active_indefinite_scheduled_lifted_expired(self):
        r=self.report(ReportFilter(period='all'))
        self.assertEqual(dict(r['suspension_states']),{'Lifted / cancelled':2,'Active':3,'Expired':1,'Scheduled':1})
        self.assertEqual({x['student_id'] for x in r['records']['active']},{'S1','S3','deleted'})
        self.assertIsNone(next(x for x in r['records']['active'] if x['student_id']=='S3')['ends_at'])

    def test_missing_classification_deleted_and_current_classifications(self):
        self.assertEqual(self.report(replace(self.custom,course=UNAVAILABLE))['counts']['recorded'],1)
        self.assertEqual(self.report(replace(self.custom,year=NEEDS_REVIEW))['counts']['recorded'],1)
        with self.db.connect() as c:
            c.execute("UPDATE students SET course='BSCS',report_year_level='4th Year' WHERE student_id='S1'")
        r=self.report(replace(self.custom,course='Computer Science',year='4th Year'))
        self.assertEqual(r['counts']['recorded'],2)

    def test_reads_do_not_run_deadline_processing_or_mutate_any_tables(self):
        with self.db.connect() as c: before=list(c.iterdump())
        with patch.object(self.db,'process_expired_deadlines',side_effect=AssertionError('write')), \
             patch.object(self.db,'reconcile_automatic_uniform_suspensions',side_effect=AssertionError('write')), \
             patch.object(self.db,'get_current_academic_term',side_effect=AssertionError('may create term')):
            for f in (ReportFilter(),self.custom,ReportFilter(period='all')): self.report(f)
        with self.db.connect() as c: self.assertEqual(before,list(c.iterdump()))

    def test_permissions_invalid_dates_missing_term_and_empty(self):
        for username in ('student','nonexistent',''):
            with self.assertRaises(PermissionError): load_report(self.db,username)
        self.assertEqual(load_report(self.db,'superadmin',now=NOW)['counts']['active'],3)
        for f in (replace(self.custom,start='bad'),replace(self.custom,end='2026-10-01')):
            with self.assertRaises(ValueError): self.report(f)
        r=self.report(replace(self.custom,course='no course'))
        self.assertFalse(any(r['counts'].values()))
        with self.db.connect() as c: c.execute('UPDATE academic_terms SET is_current=0')
        r=self.report(); self.assertEqual(r['counts']['recorded'],0)
        self.assertEqual(r['scope'],'No current academic term')

    def test_conflicting_or_foreign_suspension_term_is_unassigned(self):
        with self.db.connect() as c:
            c.execute('UPDATE automatic_suspension_awards SET semester_id=? WHERE suspension_id=2',(self.old,))
            c.execute('UPDATE student_suspensions SET violation_id=2 WHERE id=5')
        r=self.report(ReportFilter(period='unassigned'))
        self.assertEqual({x['id'] for x in r['records']['suspensions']},{2,5})

    def test_locked_database_fails_quickly_then_retries(self):
        lock=sqlite3.connect(self.db.db_path)
        lock.execute('BEGIN EXCLUSIVE')
        start=time.monotonic()
        try:
            with self.assertRaises(sqlite3.OperationalError): self.report()
        finally: lock.rollback(); lock.close()
        self.assertLess(time.monotonic()-start,1)
        self.assertEqual(self.report()['counts']['active'],3)

    def test_zero_buckets_are_filled(self):
        f=replace(self.custom,start='2026-10-03',end='2026-10-07')
        self.assertEqual(self.report(f)['trends']['finalized'],[
            ('2026-10-03',0),('2026-10-04',0),('2026-10-05',3),('2026-10-06',1),('2026-10-07',0)])

    def test_all_aggregates_share_one_read_snapshot_during_concurrent_commit(self):
        with self.db.connect() as c: c.execute('PRAGMA journal_mode=WAL')
        writer=sqlite3.connect(self.db.db_path)
        self.addCleanup(writer.close)
        connect=sqlite3.connect
        changed=[]
        def traced(*args,**kwargs):
            conn=connect(*args,**kwargs)
            def trace(sql):
                if 'SELECT id,student_id' in sql and not changed:
                    changed.append(True)
                    writer.execute("UPDATE students SET course='BSCS' WHERE student_id='S1'")
                    writer.execute("INSERT INTO violations(student_id,violation_type,violation_code,status,timestamp) VALUES('S1','earring','earring','pending_review','2026-10-05 08:00:00')")
                    writer.commit()
            conn.set_trace_callback(trace)
            return conn
        with patch('core.suspension_analytics.sqlite3.connect',side_effect=traced):
            result=self.report(ReportFilter(period='all'))
        self.assertTrue(changed)
        self.assertEqual(result['counts']['recorded'],10)
        self.assertEqual(dict(result['rankings']['course'])['Information Technology'],4)
        after=self.report(ReportFilter(period='all'))
        self.assertEqual(after['counts']['recorded'],11)
        self.assertEqual(dict(after['rankings']['course'])['Computer Science'],5)


class WorkerTests(unittest.TestCase):
    def test_latest_only_bounded_work_cleanup_and_errors(self):
        w=ReportWorker(); entered=threading.Event(); release=threading.Event()
        self.addCleanup(w.close); self.addCleanup(release.set)
        def slow(): entered.set(); release.wait(2); return 'first'
        w.offer(1,slow); self.assertTrue(entered.wait(1))
        for i in range(2,100): w.offer(i,lambda i=i:i)
        self.assertEqual(w.requests.qsize(),1)
        release.set()
        deadline=time.monotonic()+2; latest=None
        while time.monotonic()<deadline:
            try: latest=w.results.get(timeout=.1)
            except Exception: continue
            if latest[0]==99: break
        self.assertEqual(latest,(99,99,None))
        w.offer(100,lambda:1/0)
        self.assertIn('division',w.results.get(timeout=1)[2])
        w.close(); w.thread.join(1); self.assertFalse(w.thread.is_alive())
