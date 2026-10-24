import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
import zipfile
import json
import csv
from io import StringIO

from database.db_manager import CBVMSDatabase
from core.report_snapshot import create_snapshot, export_snapshot_csv, dependent_options, is_current_generation
from core.reports import snapshot_report_html


class ReportSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = CBVMSDatabase(Path(self.temp.name) / 'reports.db')
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('S-01', '王 Student', 'BSIT', '2A', b'', b'')

    def test_directory_is_separate_from_authoritative_term_enrollment(self):
        from tests.term_fixture import verified_term
        term = verified_term(self.db, 'S-01', 2026)
        snap = create_snapshot(self.db, {}, ['Student Directory'])
        enrollment = create_snapshot(self.db, {'term_ids': [term]}, ['Semester Enrollment Roster'])
        directory = snap.sections[0]
        self.assertEqual(directory.record_count, 1)
        roster = enrollment.sections[0]
        self.assertEqual(roster.record_count, 1)
        self.assertEqual(roster.rows[0]['semester_id'], term)
        self.assertEqual(roster.rows[0]['status'], 'confirmed')
        empty = create_snapshot(self.db, {'term_ids': []}, ['Semester Enrollment Roster'])
        self.assertEqual(empty.sections[0].record_count, 0)

    def test_term_filters_are_stable_ids_and_print_is_escaped(self):
        from tests.term_fixture import verified_term
        term = verified_term(self.db, 'S-01', 2026)
        all_rows = create_snapshot(self.db, {'term_ids': [term]}, ['Semester Enrollment Roster'])
        self.assertEqual(all_rows.sections[0].record_count, 1)
        html = snapshot_report_html(all_rows)
        self.assertIn('Asia/Manila', html)
        self.assertIn('王 Student', html)
        self.assertNotIn('<script>', html)

    def test_date_validation_rejects_reversed_bounds(self):
        with self.assertRaisesRegex(ValueError, 'on or before'):
            create_snapshot(self.db, {'date_from':'2026-10-02','date_to':'2026-10-01'}, ['Violations'])

    def test_dependent_checklist_options_and_stale_generation_model(self):
        years=[('2025–2026 · First Semester','2025–2026'),('2026–2027 · First Semester','2026–2027')]
        self.assertEqual(dependent_options(years,['2026–2027'],key_index=1), (years[1],))
        self.assertEqual(dependent_options(years,None), tuple(years))
        self.assertFalse(is_current_generation(12,11))
        self.assertTrue(is_current_generation(12,12))

    def test_reports_layout_positions_do_not_overlap_and_actions_wrap(self):
        from ui.reports_panel import action_grid, checklist_grid
        actions=[action_grid(i) for i in range(7)]
        self.assertEqual(len(actions),len(set(actions)))
        self.assertEqual(max(row for row,_ in actions),1)
        cells=[]
        for i in range(6):cells.extend(checklist_grid(i))
        self.assertEqual(len(cells),len(set(cells)))
        self.assertEqual(max(row for row,_ in cells),5)

    def test_latest_only_worker_rejects_stale_report_result(self):
        import threading, time
        from core.report_worker import ReportWorker
        worker=ReportWorker(); entered=threading.Event(); release=threading.Event()
        self.addCleanup(worker.close); self.addCleanup(release.set)
        def slow_snapshot():
            entered.set(); release.wait(2); return 'old snapshot'
        worker.offer(1,slow_snapshot)
        self.assertTrue(entered.wait(1))
        worker.offer(2,lambda:'new snapshot')
        release.set()
        deadline=time.monotonic()+2; accepted=[]
        while time.monotonic()<deadline and not accepted:
            try: generation,value,error=worker.results.get(timeout=.1)
            except Exception: continue
            self.assertIsNone(error)
            if is_current_generation(2,generation): accepted.append(value)
        self.assertEqual(accepted,['new snapshot'])

    def test_checklist_all_empty_and_toggle_semantics(self):
        from ui.multi_select_checklist import selection_after_toggle, select_all, clear_selection
        self.assertIsNone(select_all())  # All stays distinct from an explicitly empty set.
        self.assertEqual(clear_selection(),[])
        self.assertEqual(selection_after_toggle([],[], 'unused',False),[])
        self.assertEqual(selection_after_toggle(['A','B'],None,'A',False),['B'])
        self.assertEqual(selection_after_toggle(['A','B'],['B'],'A',True),['A','B'])

    def test_snapshot_filter_values_are_immutable(self):
        selected=['A']
        snap=create_snapshot(self.db,{'departments':selected},['Student Directory'])
        selected.append('B')
        self.assertEqual(snap.filters['departments'],('A',))

    def test_imported_foreign_term_id_is_never_trusted(self):
        from tests.term_fixture import verified_term
        local=verified_term(self.db,'S-01',2026)
        with self.db.connect() as conn:
            conn.execute('INSERT INTO report_csv_snapshot(id,payload) VALUES(1,?)',(__import__('json').dumps([
                {'student_id':'S-01','name':'Historic','record_id':str(local),'record_type':'Violation','semester_id':local,
                 'semester_name':'First Semester','school_year':'2099-2100','college_department':'Unverified','course':'Unverified',
                 'appeal_status':'approved','decision_category_code':'excused','decision_category_label':'Excused','decision_category_version':2,
                 'decision_reason':'Historical decision','decided_by':'admin','decided_at':'2026-01-01T00:00:00Z','date':'2026-01-01T00:00:00Z'},
                {'student_id':'S-01','name':'Historic','record_id':'2','record_type':'Violation','semester_id':999,
                 'semester_name':'First Semester','school_year':'2026-2027','college_department':'Unverified','course':'Unverified'}]),))
        rows=create_snapshot(self.db,{},['Imported Discipline Snapshot']).sections[0].rows
        unresolved=next(r for r in rows if r['school_year']=='2099-2100')
        resolved=next(r for r in rows if r['school_year']=='2026-2027')
        self.assertEqual(unresolved['semester_id'],None)
        self.assertIn('unresolved',unresolved['semester_name'])
        self.assertEqual(unresolved['decision_category_label'],'Excused')
        self.assertEqual(str(unresolved['source_term_id']),str(local))
        self.assertTrue(unresolved['date'].endswith('+08:00'))
        self.assertEqual(resolved['semester_id'],local)
        html=snapshot_report_html(create_snapshot(self.db,{'appeal_statuses':['approved']},['Imported Discipline Snapshot']))
        self.assertIn('Historical decision',html)
        self.assertIn('approved',html)

    def test_multiple_years_and_summer_keep_stable_ownership_and_distinct_counts(self):
        from tests.term_fixture import verified_term
        first=verified_term(self.db,'S-01',2026)
        with self.db.connect() as conn:
            # Same semester label in another year and a separately published summer term.
            start='2027-01-01T00:00:00+00:00';end='2027-05-01T00:00:00+00:00'
            second=conn.execute("INSERT INTO academic_terms(semester_code,semester_name,school_year,start_date,end_date,starts_at,ends_before,publication_state) VALUES('semester_1','First Semester','2027-2028','2027-01-01','2027-04-30',?,?,'published')",(start,end)).lastrowid
            summer=conn.execute("INSERT INTO academic_terms(semester_code,semester_name,school_year,start_date,end_date,starts_at,ends_before,publication_state) VALUES('summer','Summer','2027-2028','2027-05-01','2027-06-30','2027-05-01T00:00:00+00:00','2027-07-01T00:00:00+00:00','published')").lastrowid
            # Disposable verified enrollment fixture; the report reads only this term record.
            for term,course,effective in ((second,'Computer Science','2027-01-02T00:00:00+00:00'),(summer,'Information Technology','2027-05-02T00:00:00+00:00')):
                cap=f'capture-{term}'
                conn.execute('INSERT INTO enrollment_captures(id,student_id,semester_id,encoding,photo,photo_sha256,provenance,captured_at,actor) VALUES(?,?,?,X\'01\',X\'01\',?,?,?,?)',(cap,'S-01',term,cap,'{}',effective,'admin'))
                cur=conn.execute("INSERT INTO student_term_enrollments(student_id,semester_id,college_department,course,report_year_level,report_section,status,capture_id,confirmed_at,confirmed_by) VALUES(?,?,?,?,?,?,'confirmed',?,?,?)",('S-01',term,'College of Computer Studies',course,'2nd Year','A',cap,effective,'admin'))
                conn.execute('INSERT INTO enrollment_revisions(enrollment_id,student_id,semester_id,effective_at,snapshot) VALUES(?,?,?,?,?)',(cur.lastrowid,'S-01',term,effective,json.dumps(dict(student_id='S-01',semester_id=term,status='confirmed',college_department='College of Computer Studies',course=course,report_year_level='2nd Year',report_section='A',confirmed_at=effective))))
        snap=create_snapshot(self.db,{'term_ids':[first,second,summer]},['Semester Enrollment Roster'])
        roster=snap.sections[0]
        self.assertEqual(roster.record_count,3);self.assertEqual(roster.distinct_students,1)
        self.assertEqual(len({r['semester_id'] for r in roster.rows}),3)
        self.assertNotEqual(next(r for r in roster.rows if r['semester_id']==second)['course'],next(r for r in roster.rows if r['semester_id']==summer)['course'])
        combined=create_snapshot(self.db,{'term_ids':[second,summer],'departments':['College of Computer Studies','College of Arts'],'courses':['Computer Science','Information Technology']},['Semester Enrollment Roster'])
        self.assertEqual(combined.sections[0].record_count,2) # OR within each filter, AND across filters.
        self.assertEqual(create_snapshot(self.db,{'term_ids':[]},['Semester Enrollment Roster']).sections[0].record_count,0)
        self.assertEqual(create_snapshot(self.db,{},['Semester Enrollment Roster']).sections[0].record_count,3)

    def test_manila_midnight_inclusive_range_and_print_csv_order(self):
        from tests.term_fixture import verified_term
        term=verified_term(self.db,'S-01',2026)
        with self.db.connect() as conn:
            common=('S-01','王 Student','wrong_uniform','pending_review',term,'{"college_department":"Unverified","course":"Unverified"}')
            for stamp in ('2026-01-01T15:59:59+00:00','2026-01-01T15:30:00+00:00','2026-01-01T16:00:00+00:00'):
                conn.execute('INSERT INTO violations(student_id,student_name,violation_type,timestamp,status,semester_id,academic_snapshot) VALUES(?,?,?,?,?,?,?)',(common[0],common[1],common[2],stamp,common[3],common[4],common[5]))
        snap=create_snapshot(self.db,{'date_from':'2026-01-01','date_to':'2026-01-01'},['Violations'])
        rows=snap.sections[0].rows
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[0]['date'].endswith('+08:00'))
        html=snapshot_report_html(snap)
        first=html.find(f"<td>{rows[0]['record_id']}</td>");second=html.find(f"<td>{rows[1]['record_id']}</td>")
        self.assertGreater(first,-1);self.assertGreater(second,first)
        self.assertIn('violations by occurrence',html)
        path=Path(self.temp.name)/'ordered.csv';export_snapshot_csv(snap,path)
        with path.open(encoding='utf-8-sig',newline='') as source: exported=list(csv.DictReader(source))
        self.assertEqual([str(r['record_id']) for r in rows],[r['record_id'] for r in exported])

    def test_snapshot_read_does_not_change_any_table_rows(self):
        before=self._table_dump()
        create_snapshot(self.db,{},None)
        after=self._table_dump()
        self.assertEqual(before,after)

    def test_suspension_end_is_earliest_policy_boundary_and_future_is_upcoming(self):
        from tests.term_fixture import verified_term
        old=verified_term(self.db,'S-01',2026)
        with self.db.connect() as conn:
            new=conn.execute("INSERT INTO academic_terms(semester_code,semester_name,school_year,start_date,end_date,starts_at,ends_before,publication_state) VALUES('semester_2','Second Semester','2026-2027','2027-01-01','2027-04-30','2027-01-01T00:00:00+00:00','2027-05-01T00:00:00+00:00','published')").lastrowid
            violation=conn.execute("INSERT INTO violations(student_id,student_name,violation_type,timestamp,status,semester_id,academic_snapshot) VALUES('S-01','Student','wrong_uniform','2026-08-01T00:00:00+00:00','confirmed',?,'{}')",(old,)).lastrowid
            rows=[
                ('rollover','2026-08-01T00:00:00+00:00','2027-06-01T00:00:00+00:00',None),
                ('lifted','2026-08-01T00:00:00+00:00','2027-06-01T00:00:00+00:00','2026-09-01T00:00:00+00:00'),
                ('open','2026-08-01T00:00:00+00:00',None,None),
                ('upcoming','2026-11-01T00:00:00+00:00','2027-06-01T00:00:00+00:00',None)]
            for reason,starts,ends,lifted in rows:
                conn.execute('INSERT INTO student_suspensions(student_id,reason,starts_at,ends_at,imposed_by,imposed_at,violation_id,lifted_at) VALUES(?,?,?,?,?,?,?,?)',('S-01',reason,starts,ends,'admin',starts,violation,lifted))
        sections=create_snapshot(self.db,{},['Suspensions'],generated_at=datetime(2026,10,1,tzinfo=timezone.utc)).sections[0]
        by_reason={r['reason']:r for r in sections.rows}
        self.assertEqual(by_reason['rollover']['status'],'Active')
        self.assertEqual(by_reason['rollover']['ends_at'],'2027-01-01T08:00:00+08:00')
        self.assertEqual(by_reason['lifted']['status'],'Lifted')
        self.assertEqual(by_reason['open']['status'],'Active')
        self.assertEqual(by_reason['upcoming']['status'],'Upcoming')

    def _table_dump(self):
        with self.db.connect() as conn:
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {table:tuple(tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')) for table in tables}

    def test_enrollment_revision_effective_time_and_read_only_export(self):
        from tests.term_fixture import verified_term
        term=verified_term(self.db,'S-01',2026)
        with self.db.connect() as conn:
            enrollment=conn.execute('SELECT * FROM student_term_enrollments WHERE student_id=? AND semester_id=?',('S-01',term)).fetchone()
            before=dict(enrollment)
            after=dict(before,course='Computer Science')
            conn.execute('INSERT INTO enrollment_revisions(enrollment_id,student_id,semester_id,effective_at,snapshot) VALUES(?,?,?,?,?)',
                         (before['id'],'S-01',term,'2025-01-01T00:00:00+00:00',__import__('json').dumps(before)))
            conn.execute('INSERT INTO enrollment_revisions(enrollment_id,student_id,semester_id,effective_at,snapshot) VALUES(?,?,?,?,?)',
                         (before['id'],'S-01',term,'2026-02-01T00:00:00+00:00',__import__('json').dumps(after)))
            conn.execute('UPDATE student_term_enrollments SET course=? WHERE id=?',(after['course'],before['id']))
        old=create_snapshot(self.db,{'term_ids':[term]},['Semester Enrollment Roster'],generated_at=datetime(2026,1,1,tzinfo=timezone.utc))
        new=create_snapshot(self.db,{'term_ids':[term]},['Semester Enrollment Roster'],generated_at=datetime(2026,3,1,tzinfo=timezone.utc))
        self.assertNotEqual(old.sections[0].rows[0]['course'],new.sections[0].rows[0]['course'])
        with self.db.connect() as conn:
            state=tuple(conn.execute('SELECT course,status FROM student_term_enrollments WHERE id=?',(before['id'],)).fetchone())
            revisions=conn.execute('SELECT COUNT(*) FROM enrollment_revisions').fetchone()[0]
        path=Path(self.temp.name)/'report.zip'
        export_snapshot_csv(create_snapshot(self.db,{},['Student Directory','Violations']),path)
        with zipfile.ZipFile(path) as archive:self.assertEqual(len(archive.namelist()),2)
        with self.db.connect() as conn:
            self.assertEqual(state,tuple(conn.execute('SELECT course,status FROM student_term_enrollments WHERE id=?',(before['id'],)).fetchone()))
            self.assertEqual(revisions,conn.execute('SELECT COUNT(*) FROM enrollment_revisions').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
