"""Read-only discipline reporting. No lifecycle processing, writes, or UI imports."""
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from core.academics import display_academics, NEEDS_REVIEW
from core.discipline import is_disciplinary_code, normalize_violation_code, parse_db_datetime, utc_now, violation_display_name

MANILA = ZoneInfo('Asia/Manila')
UNAVAILABLE = 'Unavailable classification (student missing)'
ALL = 'All'
METRICS = {'recorded': 'Recorded violations', 'finalized': 'Finalized violations',
           'suspensions': 'Suspension records', 'students': 'Distinct suspended students'}
OUTCOMES = ('Pending review', 'Appeal pending', 'Resolved / dismissed', 'Finalized', 'Awaiting finalization / legacy')
SUSPENSION_STATES = ('Active', 'Scheduled', 'Expired', 'Lifted / cancelled', 'Date unavailable')


@dataclass(frozen=True)
class ReportFilter:
    period: str = 'current'  # current, all, custom, unassigned, or term:<id>
    start: str = ''
    end: str = ''
    college: str = ALL
    course: str = ALL
    year: str = ALL
    category: str = ALL
    outcome: str = ALL
    suspension_status: str = ALL
    interval: str = 'Month'
    rank: str = 'recorded'

    def bounds(self):
        if self.period != 'custom':
            return None, None
        try:
            first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
        except ValueError as exc:
            raise ValueError('Enter both dates as YYYY-MM-DD.') from exc
        if last < first:
            raise ValueError('End date must be on or after start date.')
        return (datetime.combine(first, time.min, MANILA).astimezone(timezone.utc),
                datetime.combine(last + timedelta(days=1), time.min, MANILA).astimezone(timezone.utc))


def suspension_state(row, now):
    if row.get('lifted_at'):
        return 'Lifted / cancelled'
    start, end = parse_db_datetime(row.get('starts_at')), parse_db_datetime(row.get('ends_at'))
    if end and end <= now:
        return 'Expired'
    if not start or (row.get('ends_at') and not end):
        return 'Date unavailable'
    return 'Scheduled' if start > now else 'Active'


def bucket(stamp, interval):
    local = parse_db_datetime(stamp).astimezone(MANILA).date()
    if interval == 'Week':
        local -= timedelta(days=local.weekday())
    elif interval == 'Month':
        local = local.replace(day=1)
    return local.isoformat()


def trend(rows, interval, bounds=(None, None)):
    counts = Counter(bucket(r['date'], interval) for r in rows if parse_db_datetime(r['date']))
    # Fill missing buckets so gaps are not rendered as continuous activity.
    if not counts:
        return []
    start, end = bounds
    first = bucket(start, interval) if start else min(counts)
    last = bucket(end-timedelta(microseconds=1), interval) if end else max(counts)
    day, final = date.fromisoformat(first), date.fromisoformat(last)
    result = []
    while day <= final:
        result.append((day.isoformat(), counts[day.isoformat()]))
        if interval == 'Month':
            day = date(day.year + (day.month == 12), 1 if day.month == 12 else day.month+1, 1)
        else:
            day += timedelta(days=7 if interval == 'Week' else 1)
    return result


def _read_snapshot(database, username):
    # Dedicated read-only connection also avoids connect() hooks and schema helpers.
    uri = Path(database.db_path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=.25)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        role = conn.execute('SELECT role FROM users WHERE username=?', (username.strip(),)).fetchone()
        if not role or role['role'] not in ('admin', 'superadmin'):
            raise PermissionError('An authenticated administrator is required for suspension reporting.')
        terms = [dict(r) for r in conn.execute('SELECT * FROM academic_terms ORDER BY is_current DESC, id DESC')]
        students = {r['student_id']: dict(r) for r in conn.execute('''SELECT student_id,name,
            college_department,course,report_year_level,year_and_section FROM students''')}
        violations = [dict(r) for r in conn.execute('''SELECT id,student_id,student_name,
            violation_code,violation_type,timestamp,status,semester_id FROM violations''')]
        strikes = {r['violation_id']: dict(r) for r in conn.execute('''SELECT violation_id,
            awarded_at,is_active,semester_id FROM strikes''')}
        appeals = defaultdict(set)
        for row in conn.execute('SELECT violation_id,status FROM appeals'):
            appeals[row['violation_id']].add(row['status'])
        suspensions = [dict(r) for r in conn.execute('SELECT * FROM student_suspensions')]
        awards = defaultdict(set)
        for row in conn.execute('SELECT suspension_id,semester_id FROM automatic_suspension_awards'):
            awards[row['suspension_id']].add(row['semester_id'])
    return terms, students, violations, strikes, appeals, suspensions, awards


def load_report(database, username, filters=ReportFilter(), *, now=None):
    bounds = filters.bounds()
    if filters.interval not in ('Day','Week','Month') or filters.rank not in METRICS:
        raise ValueError('Unsupported chart interval or measure.')
    now = parse_db_datetime(now) if now is not None else utc_now()
    terms, students, violations, strikes, appeals, suspensions, awards = _read_snapshot(database, username)
    current = next((t for t in terms if t['is_current']), None)
    term_id = current['id'] if current and filters.period == 'current' else None
    if filters.period.startswith('term:'):
        term_id = int(filters.period.split(':')[1])
    elif filters.period not in ('current','all','custom','unassigned'):
        raise ValueError('Unsupported reporting period.')

    def classification(sid):
        student = students.get(sid)
        if student is None:
            return dict(college=UNAVAILABLE, course=UNAVAILABLE, year=UNAVAILABLE)
        fields = display_academics(student)
        return dict(college=fields['college_department'], course=fields['course'],
                    year=fields['report_year_level'] or NEEDS_REVIEW)

    def matches(row, *, period=True):
        if any(value != ALL and row[key] != value for key, value in
               (('college',filters.college),('course',filters.course),('year',filters.year),('category',filters.category))):
            return False
        if not period:
            return True
        if filters.period == 'current' and current is None:
            return False
        if term_id is not None and row['term_id'] != term_id:
            return False
        if filters.period == 'unassigned' and row['term_id'] is not None:
            return False
        if bounds[0]:
            at = parse_db_datetime(row['date'])
            if at is None or not bounds[0] <= at < bounds[1]:
                return False
        return True

    def base(row, kind, at, term, category, status):
        sid = row['student_id']
        return dict(kind=kind,id=row['id'],student_id=sid,
            name=students.get(sid,{}).get('name') or row.get('student_name') or 'Student record unavailable',
            available=sid in students,date=at,term_id=term,category=category,status=status,**classification(sid))

    recorded, finalized = [], []
    violation_by_id = {v['id']:v for v in violations}
    categories = set()
    for row in violations:
        code = normalize_violation_code(row['violation_code'] or row['violation_type'])
        if not row['student_id'] or row['student_id'] == 'unknown' or not is_disciplinary_code(code):
            continue
        categories.add(code)
        appeal = appeals[row['id']]
        strike = strikes.get(row['id'])
        upheld = bool(strike and strike['is_active'] and row['status'] not in ('dismissed','resolved')
                      and not appeal.intersection({'approved','pending'}))
        outcome = ('Resolved / dismissed' if row['status'] in ('dismissed','resolved') or 'approved' in appeal else
                   'Appeal pending' if 'pending' in appeal else 'Finalized' if upheld else
                   'Pending review' if row['status'] in ('pending_review','unreviewed') else 'Awaiting finalization / legacy')
        item = base(row,'violation',row['timestamp'],row['semester_id'],code,outcome)
        if filters.outcome != ALL and outcome != filters.outcome:
            continue
        if matches(item):
            recorded.append(item)
        if upheld:
            final = dict(item,date=strike['awarded_at'],term_id=strike['semester_id'])
            if matches(final):
                finalized.append(final)
    starts, active = [], []
    for row in suspensions:
        related = violation_by_id.get(row['violation_id'], {})
        if related.get('student_id') != row['student_id']:
            related = {}
        associations = set(awards[row['id']])
        if related.get('semester_id') is not None:
            associations.add(related['semester_id'])
        term = next(iter(associations)) if len(associations) == 1 else None
        category = normalize_violation_code(related['violation_code'] or related['violation_type']) if related else 'Unassigned'
        categories.add(category)
        item = base(row,'suspension',row['starts_at'],term,category,suspension_state(row,now))
        item.update(ends_at=row['ends_at'],reason=row['reason'])
        # Current snapshot deliberately ignores period AND suspension-status selectors.
        if item['status'] == 'Active' and matches(item,period=False):
            active.append(item)
        if matches(item) and (filters.suspension_status == ALL or filters.suspension_status == item['status']):
            starts.append(item)

    def distinct(rows):
        found = {}
        for row in rows:
            found.setdefault(row['student_id'],row)
        return list(found.values())

    records = dict(recorded=recorded,finalized=finalized,suspensions=starts,
                   students=distinct(starts),active=distinct(active))
    rankings = {}
    for field in ('college','course','year'):
        counts = Counter(r[field] for r in records[filters.rank])
        rankings[field] = sorted(counts.items(),key=lambda pair:(-pair[1],pair[0]))
    selected_term = next((t for t in terms if t['id']==term_id),None)
    scope = (f"{selected_term['semester_name']} · {selected_term['school_year']} (stored term association)"
             if selected_term else 'No current academic term' if filters.period=='current' else
             f'{filters.start} – {filters.end} · Asia/Manila, inclusive' if filters.period=='custom' else
             'Unassigned term records' if filters.period=='unassigned' else 'All Data')
    for title, value in (('College',filters.college),('Course',filters.course),('Year',filters.year),
                         ('Category',filters.category),('Violation outcome',filters.outcome),
                         ('Suspension status',filters.suspension_status)):
        if value != ALL:
            scope += f" · {title}: {violation_display_name(value) if title=='Category' else value}"
    return dict(filters=filters,terms=terms,current=current,scope=scope,
        refreshed_at=now.astimezone(MANILA).strftime('%d %b %Y · %H:%M:%S Asia/Manila'),
        counts={k:len(v) for k,v in records.items()},records=records,rankings=rankings,
        trends={k:trend(records[k],filters.interval,bounds) for k in ('recorded','finalized','suspensions')},
        outcomes=sorted(Counter(r['status'] for r in recorded).items()),
        suspension_states=sorted(Counter(r['status'] for r in starts).items()),categories=sorted(categories),
        years=sorted({classification(s)['year'] for s in students}),
        unassigned_suspensions=sum(r['term_id'] is None for r in starts),
        undated={k:sum(parse_db_datetime(r['date']) is None for r in records[k]) for k in records})


def drill_rows(report, metric, *, field=None, value=None):
    """Use the exact displayed snapshot and intersection; never a broader requery."""
    rows = report['records'][metric]
    return [r for r in rows if field is None or r[field] == value]
