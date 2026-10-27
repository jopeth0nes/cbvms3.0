"""Campus calendar policy. Naive legacy timestamps mean UTC, never local time."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from core.discipline import parse_db_datetime, utc_now

MANILA = ZoneInfo('Asia/Manila')
ENROLLED = 'Enrolled in current term'
UNVERIFIED = 'Enrollment unverified — legacy record'
UPCOMING = 'Enrolled for upcoming term only'
OUTSIDE = 'Outside academic term'


def bounds(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError('Start date must not follow inclusive end date.')
    return tuple(datetime.combine(d, time(), MANILA).astimezone(timezone.utc).isoformat()
                 for d in (first, last + timedelta(days=1)))


def instant(value=None):
    result = (datetime.fromtimestamp(value,timezone.utc) if isinstance(value,(float,int)) and not isinstance(value,bool)
              else utc_now() if value is None else parse_db_datetime(value))
    if result is None:
        raise ValueError('An actual occurrence timestamp is required.')
    return result


def resolve_term(conn, occurred_at=None):
    stamp = instant(occurred_at).isoformat()
    rows = conn.execute("""SELECT * FROM academic_terms WHERE publication_state='published'
        AND julianday(starts_at)<=julianday(?) AND julianday(ends_before)>julianday(?)""",
        (stamp, stamp)).fetchall()
    if len(rows) > 1:
        raise ValueError('Overlapping campus calendar requires administrator review.')
    return dict(rows[0]) if rows else {}


def enrollment_context(conn, student_id, occurred_at=None):
    """Resolve eligibility and demographics from the versions effective at occurrence."""
    import json
    at=instant(occurred_at)
    term=resolve_term(conn,at)
    rows=conn.execute('SELECT * FROM student_term_enrollments WHERE student_id=?',(student_id,)).fetchall()
    revisions=conn.execute("""SELECT semester_id,snapshot FROM (
        SELECT semester_id,snapshot,ROW_NUMBER() OVER(PARTITION BY semester_id ORDER BY julianday(effective_at) DESC,id DESC) position
        FROM enrollment_revisions WHERE student_id=? AND julianday(effective_at)<=julianday(?)) WHERE position=1""",
        (student_id,at.isoformat())).fetchall()
    versions={r['semester_id']:json.loads(r['snapshot']) for r in revisions}
    historical=[]
    for row in rows:
        version=versions.get(row['semester_id'],dict(row))
        if version['confirmed_at'] and instant(version['confirmed_at'])<=at:
            historical.append(version)
    enrollment=next((e for e in historical if e['semester_id']==term.get('id') and e['status']=='confirmed'),None)
    exception_rows=conn.execute('''SELECT x.*,e.status current_status,e.capture_id current_capture
        FROM enrollment_administrative_exceptions x
        JOIN student_term_enrollments e ON e.id=x.enrollment_id
        WHERE x.student_id=? AND julianday(x.effective_at)<=julianday(?)''',
        (student_id,at.isoformat())).fetchall()
    exception_enrollments=[]
    for exception in exception_rows:
        # The latest occurrence-effective revision controls withdrawal. Exception
        # proof grants eligibility only while this enrollment remains an uncaptured draft.
        version=versions.get(exception['semester_id'])
        if version is None:
            saved=conn.execute('SELECT * FROM student_term_enrollments WHERE id=?',
                               (exception['enrollment_id'],)).fetchone()
            version=dict(saved) if saved else {}
        if version.get('status')=='draft' and not version.get('capture_id'):
            eligible_version=dict(version,eligibility_method='administrative_exception',
                exception_effective_at=exception['effective_at'])
            eligible_version['_eligibility_granted']=True
            exception_enrollments.append(eligible_version)
    if enrollment is None:
        enrollment=next((e for e in exception_enrollments if e['semester_id']==term.get('id')),None)
    eligible_enrollments=historical+exception_enrollments
    terms={r['id']:dict(r) for r in conn.execute('SELECT * FROM academic_terms')}
    prior=[terms[e['semester_id']] for e in eligible_enrollments if terms[e['semester_id']]['starts_at']
           and instant(terms[e['semester_id']]['starts_at'])<=at]
    last=max(prior,key=lambda t:instant(t['starts_at'])) if prior else None
    upcoming=any((e['status']=='confirmed' or e.get('_eligibility_granted'))
                 and terms[e['semester_id']]['publication_state']=='published'
                 and instant(terms[e['semester_id']]['starts_at'])>at for e in eligible_enrollments)
    status=(OUTSIDE if not term else ENROLLED if enrollment else UPCOMING if upcoming and not last
            else f"Not enrolled in current term — last verified enrollment: {last['semester_name']} {last['school_year']}" if last else UNVERIFIED)
    if enrollment:
        enrollment.pop('_eligibility_granted',None)
    return {'term':term,'enrollment':enrollment,'status':status,'eligible':bool(term and enrollment),
            'eligibility_method':enrollment.get('eligibility_method', 'camera_capture') if enrollment else None}
