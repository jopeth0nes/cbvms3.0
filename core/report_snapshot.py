"""Consistent, read-only snapshots for the administrative reports workspace.

Rows contain only report-safe fields. Academic classifications for discipline
come from the incident snapshot; enrollment rows come from term enrollment.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone, date
from types import MappingProxyType
from zoneinfo import ZoneInfo
import json
import csv
from pathlib import Path
import zipfile

MANILA = ZoneInfo('Asia/Manila')
SECTIONS = ('Student Directory', 'Semester Enrollment Roster', 'Violations', 'Suspensions', 'Imported Discipline Snapshot')


def dependent_options(options, selected, *, key_index=0):
    """Return options allowed by a dependency; None means unrestricted."""
    options=tuple(options)
    return options if selected is None else tuple(v for v in options if v[key_index] in selected)


def is_current_generation(current, result_generation):
    """A completed worker result may render only for its originating request."""
    return current == result_generation


def sort_snapshot(snapshot,key,reverse=False):
    sections=tuple(replace(section,rows=tuple(sorted(section.rows,key=lambda r:str(r.get(key) or '').casefold(),reverse=reverse))) for section in snapshot.sections)
    return replace(snapshot,sections=sections,sort_order=(key,reverse))


@dataclass(frozen=True)
class ReportSection:
    name: str
    columns: tuple
    rows: tuple
    record_count: int
    distinct_students: int


@dataclass(frozen=True)
class ReportSnapshot:
    filters: object
    selected_sections: tuple
    sort_order: tuple
    generated_at: datetime
    sections: tuple
    terms: tuple = ()


def _distinct(rows):
    return len({r.get('student_id') for r in rows if r.get('student_id')})


def _manila_stamp(value):
    if not value:return ''
    if len(str(value))==10:
        try:return date.fromisoformat(str(value)).isoformat()
        except ValueError:pass
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(MANILA).isoformat(timespec='seconds')
    except ValueError:return str(value)


def _utc_datetime(value):
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _matches(row, filters, *, section):
    for key in ('academic_years', 'term_ids', 'departments', 'courses', 'year_levels', 'sections'):
        selected = filters.get(key)
        if selected is not None and len(selected):
            field = {'academic_years':'school_year','term_ids':'semester_id','departments':'college_department',
                     'courses':'course','year_levels':'year_level','sections':'section'}[key]
            if row.get(field) not in selected:
                return False
        elif selected is not None and not selected:
            return False
    for key,field in (('statuses','status'),('appeal_statuses','appeal_status')):
        selected=filters.get(key)
        applicable=(section in ('Semester Enrollment Roster','Violations','Suspensions','Imported Discipline Snapshot') if field=='status'
                    else section in ('Violations','Imported Discipline Snapshot'))
        if applicable and selected is not None and (not selected or row.get(field) not in selected):return False
    search = (filters.get('search') or '').strip().casefold()
    if search and search not in (str(row.get('student_id') or '') + ' ' + str(row.get('name') or '')).casefold():
        return False
    start, end = filters.get('date_from'), filters.get('date_to')
    if start or end:
        field = 'date' if section in ('Violations','Suspensions','Imported Discipline Snapshot') else 'confirmed_at'
        stamp = row.get(field)
        if not stamp:
            return False
        try:
            dt = datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
            day = (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(MANILA).date().isoformat()
        except ValueError:
            return False
        if start and day < start or end and day > end:
            return False
    return True


def create_snapshot(database, filters=None, selected_sections=None, *, sort_order=(), generated_at=None):
    """Read all selected sections under one SQLite snapshot transaction."""
    filters = {key:tuple(value) if isinstance(value,(list,set)) else value for key,value in dict(filters or {}).items()}
    selected_sections = tuple(s for s in (SECTIONS if selected_sections is None else selected_sections) if s in SECTIONS)
    if filters.get('date_from') and filters.get('date_to') and filters['date_from'] > filters['date_to']:
        raise ValueError('From date must be on or before To date.')
    for key in ('date_from','date_to'):
        if filters.get(key):
            parsed=date.fromisoformat(filters[key])
            if parsed.isoformat()!=filters[key]:raise ValueError('Dates must use exact YYYY-MM-DD format.')
    reference = generated_at or datetime.now(timezone.utc)
    if reference.tzinfo is None:reference=reference.replace(tzinfo=timezone.utc)
    with database.connect() as conn:
        conn.execute('BEGIN')
        terms = {r['id']:dict(r) for r in conn.execute('SELECT * FROM academic_terms')}
        students = {r['student_id']:dict(r) for r in conn.execute('SELECT student_id,name,course,year_and_section,college_department,report_section,report_year_level FROM students')}
        raw = {}
        raw['Student Directory'] = [dict(student_id=sid,name=s['name'],college_department=s.get('college_department') or 'Unverified',
             course=s.get('course') or 'Unverified',year_level=s.get('report_year_level') or '',section=s.get('report_section') or '',
             record_type='Directory') for sid,s in students.items()]
        raw['Semester Enrollment Roster'] = []
        for r in conn.execute('''SELECT e.*,s.name,t.school_year,t.semester_name,
                  rev.snapshot revision_snapshot FROM student_term_enrollments e
                 JOIN students s ON s.student_id=e.student_id LEFT JOIN academic_terms t ON t.id=e.semester_id
                 LEFT JOIN enrollment_revisions rev ON rev.id=(SELECT er.id FROM enrollment_revisions er
                    WHERE er.enrollment_id=e.id AND julianday(er.effective_at)<=julianday(?)
                    ORDER BY julianday(er.effective_at) DESC,er.id DESC LIMIT 1)
                 ORDER BY e.student_id,e.semester_id,e.id''',(reference.isoformat(),)):
            d=dict(r);t=terms.get(d['semester_id']) or {}
            if d.get('revision_snapshot'):
                revision=json.loads(d['revision_snapshot'])
                for key in ('status','college_department','course','report_year_level','report_section','confirmed_at'):
                    d[key]=revision.get(key,d.get(key))
            enrollment_status=d.get('status') or 'unverified'
            if enrollment_status=='confirmed' and t.get('starts_at'):
                try:
                    term_start=_utc_datetime(t['starts_at'])
                    if term_start.astimezone(timezone.utc)>reference.astimezone(timezone.utc):enrollment_status='Upcoming'
                except ValueError:pass
            raw['Semester Enrollment Roster'].append(dict(student_id=d['student_id'],name=d['name'], enrollment_id=d['id'],
                semester_id=d['semester_id'],semester_name=t.get('semester_name','Legacy / Unassigned'),school_year=t.get('school_year',''),
                college_department=d.get('college_department') or 'Unverified',course=d.get('course') or 'Unverified',
                year_level=d.get('report_year_level') or '',section=d.get('report_section') or '',status=enrollment_status,
                confirmed_at=_manila_stamp(d.get('confirmed_at')),record_type='Enrollment'))
        raw['Violations'] = []
        from core.discipline import violation_display_name
        for rec in conn.execute('''SELECT v.id,v.student_id,v.student_name,v.violation_type,v.timestamp,v.status,v.violation_code,
                v.semester_id,v.academic_snapshot,v.enrollment_status,v.term_classification,
                a.status appeal_status,a.decision_category_code,a.decision_category_label,
                a.decision_category_version,a.admin_notes decision_reason,a.decided_by,a.decided_at FROM violations v
                LEFT JOIN appeals a ON a.violation_id=v.id ORDER BY v.timestamp,v.id'''):
            r=dict(rec); t=terms.get(r.get('semester_id')) or {}; academic=json.loads(r.get('academic_snapshot') or '{}')
            fallback=('Outside academic term' if r.get('term_classification')=='outside_term' else 'Legacy / Unassigned') if not r.get('semester_id') else 'Unverified term'
            raw['Violations'].append(dict(student_id=r.get('student_id') or '',name=r.get('student_name') or '',record_id=r['id'],
                semester_id=r.get('semester_id'),semester_name=t.get('semester_name',fallback),
                school_year=t.get('school_year',''),college_department=academic.get('college_department') or 'Unverified',
                course=academic.get('course') or 'Unverified',year_level=academic.get('report_year_level') or 'Unverified',
                section=academic.get('report_section') or 'Unverified',category=violation_display_name(r.get('violation_code'),r.get('violation_type')),
                status=r.get('status') or '',date=_manila_stamp(r.get('timestamp')),appeal_status=r.get('appeal_status') or '',
                enrollment_status=r.get('enrollment_status') or 'legacy_unverified',term_classification=r.get('term_classification') or 'legacy_assignment',
                decision_category=r.get('decision_category_label') or '',decision_reason=r.get('decision_reason') or '',
                decided_by=r.get('decided_by') or '',decided_at=_manila_stamp(r.get('decided_at')),record_type='Violation'))
        raw['Suspensions'] = []
        for rec in conn.execute('''SELECT s.*,v.semester_id,v.academic_snapshot,v.student_name,v.enrollment_status,v.term_classification FROM student_suspensions s
                LEFT JOIN violations v ON v.id=s.violation_id ORDER BY s.starts_at,s.id'''):
            r=dict(rec);t=terms.get(r.get('semester_id')) or {};academic=json.loads(r.get('academic_snapshot') or '{}')
            next_term=conn.execute("SELECT starts_at FROM academic_terms WHERE publication_state='published' AND julianday(starts_at)>julianday(?) ORDER BY julianday(starts_at),id LIMIT 1",(r.get('starts_at'),)).fetchone()
            endings=[]
            for kind,value in (('scheduled',r.get('ends_at')),('rollover',r.get('effective_ended_at')),('lifted',r.get('lifted_at')),('rollover',next_term[0] if next_term else None)):
                if value:
                    try:
                        endings.append((_utc_datetime(value),kind,value))
                    except ValueError:pass
            ending=min(endings,key=lambda entry:entry[0]) if endings else None
            effective_end=ending[2] if ending else None
            try:
                started=_utc_datetime(r.get('starts_at') or '')
                ended=_utc_datetime(effective_end) if effective_end else None
                ref=reference if reference.tzinfo else reference.replace(tzinfo=timezone.utc)
                future=started>ref.astimezone(timezone.utc) and (ended is None or ended>ref.astimezone(timezone.utc)) and not r.get('lifted_at')
                active=future or (started<=ref.astimezone(timezone.utc) and (ended is None or ended>ref.astimezone(timezone.utc)) and not r.get('lifted_at'))
            except ValueError: active=False
            if active and future:suspension_status='Upcoming'
            elif active:suspension_status='Active'
            elif ending and ending[1]=='lifted':suspension_status='Lifted'
            elif ending and ending[1]=='rollover':suspension_status='Ended by term rollover'
            else:suspension_status='Ended'
            raw['Suspensions'].append(dict(student_id=r['student_id'],name=r.get('student_name') or '',record_id=r['id'],
                semester_id=r.get('semester_id'),semester_name=t.get('semester_name',('Outside academic term' if r.get('term_classification')=='outside_term' else 'Legacy / Unassigned') if not r.get('semester_id') else 'Unverified term'),school_year=t.get('school_year',''),
                college_department=academic.get('college_department') or 'Unverified',course=academic.get('course') or 'Unverified',
                year_level=academic.get('report_year_level') or 'Unverified',section=academic.get('report_section') or 'Unverified',
                status=suspension_status,date=_manila_stamp(r.get('starts_at')),ends_at=_manila_stamp(effective_end),reason=r.get('reason') or '',
                term_classification=r.get('term_classification') or 'legacy_assignment',record_type='Suspension'))
        raw['Imported Discipline Snapshot']=[]
        saved=conn.execute('SELECT payload FROM report_csv_snapshot WHERE id=1').fetchone()
        if saved:
            for i,r in enumerate(json.loads(saved[0])):
                label=(r.get('semester_name') or '').strip();year=(r.get('school_year') or '').strip()
                matches=[dict(t) for t in conn.execute("SELECT id,semester_name,school_year FROM academic_terms WHERE publication_state='published' AND semester_name=? AND school_year=?",(label,year))] if label and year else []
                local_term=matches[0] if len(matches)==1 else {}
                raw['Imported Discipline Snapshot'].append(dict(student_id=r.get('student_id') or '',name=r.get('name') or '',
                    record_id=f"imported:{r.get('record_type','Discipline')}:{r.get('record_id') or i+1}:{i+1}",semester_id=local_term.get('id'),
                    semester_name=local_term.get('semester_name') or (label+' · unresolved' if label else 'Legacy / Unassigned'),
                    school_year=local_term.get('school_year') or year,college_department=r.get('college_department') or 'Unverified',
                    course=r.get('course') or 'Unverified',year_level=r.get('year_level') or 'Unverified',section=r.get('section') or 'Unverified',
                    category=r.get('category') or '',status=r.get('status') or '',date=_manila_stamp(r.get('date')),ends_at=_manila_stamp(r.get('ends_at')),
                    source_record_id=r.get('record_id') or '',source_term_id=r.get('semester_id') or '',
                    reason=r.get('reason') or '',appeal_status=r.get('appeal_status') or '',
                    decision_category_code=r.get('decision_category_code') or '',decision_category_label=r.get('decision_category_label') or '',
                    decision_category_version=r.get('decision_category_version') or '',decision_reason=r.get('decision_reason') or '',
                    decided_by=r.get('decided_by') or '',decided_at=_manila_stamp(r.get('decided_at')),
                    source='Imported snapshot (read-only)',record_type=r.get('record_type') or 'Discipline'))
        conn.commit()
    definitions={
        'Student Directory':('student_id','name','college_department','course','year_level','section'),
        'Semester Enrollment Roster':('student_id','name','enrollment_id','semester_id','semester_name','school_year','college_department','course','year_level','section','status','confirmed_at'),
        'Violations':('student_id','name','record_id','semester_id','semester_name','school_year','term_classification','enrollment_status','college_department','course','year_level','section','category','status','date','appeal_status','decision_category','decision_reason','decided_by','decided_at'),
        'Suspensions':('student_id','name','record_id','semester_id','semester_name','school_year','term_classification','college_department','course','year_level','section','status','date','ends_at','reason'),
        'Imported Discipline Snapshot':('student_id','name','record_id','source_record_id','source_term_id','semester_id','semester_name','school_year','college_department','course','year_level','section','record_type','category','status','date','ends_at','reason','appeal_status','decision_category_code','decision_category_label','decision_category_version','decision_reason','decided_by','decided_at','source')}
    sections=[]
    for name in selected_sections:
        rows=[r for r in raw[name] if _matches(r,filters,section=name)]
        sort_key, reverse=(sort_order if sort_order else ('student_id',False))
        rows.sort(key=lambda r:str(r.get(sort_key) or '').casefold(),reverse=bool(reverse))
        sections.append(ReportSection(name,definitions[name],tuple(MappingProxyType(r) for r in rows),len(rows),_distinct(rows)))
    term_catalog=tuple(MappingProxyType(t) for t in terms.values() if t.get('publication_state')=='published')
    return ReportSnapshot(MappingProxyType(filters),selected_sections,tuple(sort_order),reference,tuple(sections),term_catalog)


def export_snapshot_csv(snapshot, path):
    """Write one CSV per selected section; multiple sections are delivered as ZIP."""
    path = Path(path)
    def write(stream, section):
        writer = csv.writer(stream)
        writer.writerow(section.columns)
        for row in section.rows:
            values=[]
            for key in section.columns:
                value=str(row.get(key) or '')
                values.append("'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value)
            writer.writerow(values)
    if len(snapshot.sections) == 1:
        with path.open('w',encoding='utf-8-sig',newline='') as out:
            write(out,snapshot.sections[0])
    else:
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for section in snapshot.sections:
                import io
                stream=io.StringIO(newline='')
                write(stream,section)
                archive.writestr(section.name.lower().replace(' ','_')+'.csv', '\ufeff'+stream.getvalue())
