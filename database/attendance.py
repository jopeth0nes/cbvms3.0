"""One authoritative attendance store: original daily summaries plus audited events."""
from datetime import timedelta
from core.attendance import Sighting, utc_stamp, manila_date, validate_filters, snapshot_fields
from core.academics import resolve_pair
from core.discipline import utc_now, parse_db_datetime


def migrate_attendance(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(attendance)')}
    additions = dict(sighting_count='INTEGER NOT NULL DEFAULT 0', legacy_summary='INTEGER NOT NULL DEFAULT 1',
        college_department="TEXT NOT NULL DEFAULT 'Unspecified/Needs review'",
        course="TEXT NOT NULL DEFAULT 'Unspecified/Needs review'", report_year_level="TEXT NOT NULL DEFAULT ''",
        report_section="TEXT NOT NULL DEFAULT ''", semester_id='INTEGER', semester_name="TEXT NOT NULL DEFAULT ''",
        school_year="TEXT NOT NULL DEFAULT ''", provenance="TEXT NOT NULL DEFAULT 'legacy_summary: original date/timezone and count unknown'")
    for key, ddl in additions.items():
        if key not in columns:
            conn.execute(f'ALTER TABLE attendance ADD COLUMN {key} {ddl}')
    conn.execute('''CREATE TABLE IF NOT EXISTS attendance_events (
        id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, student_id TEXT NOT NULL,
        student_name TEXT NOT NULL, observed_at TEXT NOT NULL, attendance_date TEXT NOT NULL,
        source_id TEXT NOT NULL, source_label TEXT NOT NULL, session_id TEXT NOT NULL, frame_id TEXT NOT NULL,
        monitor_generation INTEGER NOT NULL, camera_generation INTEGER NOT NULL, presence_id TEXT NOT NULL,
        student_status TEXT NOT NULL, college_department TEXT NOT NULL, course TEXT NOT NULL,
        report_year_level TEXT NOT NULL, report_section TEXT NOT NULL,
        semester_id INTEGER, semester_name TEXT NOT NULL, school_year TEXT NOT NULL,
        provenance TEXT NOT NULL, cooldown_seconds INTEGER NOT NULL, persisted_at TEXT NOT NULL,
        UNIQUE(student_id,session_id,frame_id))''')
    conn.execute('CREATE INDEX IF NOT EXISTS attendance_event_student_time ON attendance_events(student_id,observed_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS attendance_event_day ON attendance_events(attendance_date,student_id,id)')
    conn.execute('CREATE INDEX IF NOT EXISTS attendance_event_source ON attendance_events(source_id,attendance_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS attendance_summary_day ON attendance(attendance_date,student_id,id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS attendance_receipts (
        event_key TEXT PRIMARY KEY, outcome TEXT NOT NULL, observed_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS attendance_settings (
        id INTEGER PRIMARY KEY CHECK(id=1), cooldown_seconds INTEGER NOT NULL CHECK(cooldown_seconds BETWEEN 0 AND 86400))''')
    conn.execute('INSERT OR IGNORE INTO attendance_settings VALUES(1,300)')
    # Preserve legacy summaries byte-for-byte; do not manufacture events or historic classifications.
    conn.execute('''CREATE TABLE IF NOT EXISTS attendance_legacy (
        id INTEGER PRIMARY KEY, student_id TEXT, student_name TEXT, attendance_date TEXT, first_seen TEXT, last_seen TEXT)''')
    conn.execute('''INSERT OR IGNORE INTO attendance_legacy
        SELECT id,student_id,student_name,attendance_date,first_seen,last_seen FROM attendance WHERE legacy_summary=1''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS attendance_events_immutable_update
        BEFORE UPDATE ON attendance_events BEGIN SELECT RAISE(ABORT,'Sighting events are immutable'); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS attendance_events_immutable_delete
        BEFORE DELETE ON attendance_events BEGIN SELECT RAISE(ABORT,'Sighting events are immutable'); END''')


class AttendanceStore:
    def _attendance_staff_only(self):
        # No student-facing attendance API. Even full student sessions cannot query the staff store.
        if self.student_session_ref is not None:
            raise PermissionError('Attendance reports are available to staff only.')

    def get_attendance_cooldown(self):
        self._attendance_staff_only()
        with self.connect() as conn:
            return conn.execute('SELECT cooldown_seconds FROM attendance_settings WHERE id=1').fetchone()[0]

    def set_attendance_cooldown(self, seconds, *, actor):
        self._attendance_staff_only()
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 <= seconds <= 86400:
            raise ValueError('Cooldown must be 0–86400 seconds.')
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM users WHERE username=? AND role IN ('admin','superadmin')", (actor,)).fetchone():
                raise PermissionError('An administrator or superadmin is required.')
            conn.execute('UPDATE attendance_settings SET cooldown_seconds=? WHERE id=1', (seconds,))

    def attendance_term_snapshot(self):
        with self.connect() as conn:
            row = conn.execute('SELECT id,semester_name,school_year FROM academic_terms WHERE is_current=1 LIMIT 1').fetchone()
            return dict(row) if row else {}

    def record_attendance(self, student_id, *, observed_at=None, sighting=None, valid_if=None):
        """Single writer boundary. Retries/cooldown/summary updates commit atomically.

        Live callers supply an immutable fact qualified while the source was fresh.
        The cancellation guard remains live; worker latency is not observation time.
        Legacy Python callers use explicit `legacy_api_observation` provenance.
        """
        self._attendance_staff_only()
        timestamp = utc_stamp(observed_at if observed_at is not None else utc_now())
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if valid_if is not None and not valid_if():
                return False
            student = conn.execute('SELECT * FROM students WHERE student_id=?', (student_id,)).fetchone()
            if student is None:
                return False
            if sighting is None:
                values = snapshot_fields(dict(student))
                sighting = Sighting(student_id, student['name'], timestamp, 'legacy-api', 'Legacy Python caller',
                    'legacy-api', timestamp, 0, 0, '', student['student_status'], **values,
                    provenance='legacy_api_observation: source/session unavailable')
            if not isinstance(sighting, Sighting) or sighting.student_id != student_id:
                raise ValueError('Sighting identity does not match the student.')
            timestamp = utc_stamp(sighting.observed_at)
            if not sighting.session_id or not sighting.frame_id or not sighting.source_id:
                raise ValueError('Sighting source, session and frame are required.')
            receipt = conn.execute('SELECT outcome FROM attendance_receipts WHERE event_key=?', (sighting.key,)).fetchone()
            if receipt:
                return False
            cooldown = conn.execute('SELECT cooldown_seconds FROM attendance_settings WHERE id=1').fetchone()[0]
            observed = parse_db_datetime(timestamp)
            # Both neighbours matter for delayed/out-of-order writes. Exact boundary is accepted.
            near = conn.execute('''SELECT 1 FROM attendance_events WHERE student_id=?
                AND observed_at>? AND observed_at<? LIMIT 1''', (student_id,
                utc_stamp(observed-timedelta(seconds=cooldown)), utc_stamp(observed+timedelta(seconds=cooldown)))).fetchone()
            outcome = 'cooldown' if near else 'accepted'
            conn.execute('INSERT INTO attendance_receipts VALUES(?,?,?)', (sighting.key, outcome, timestamp))
            if not near:
                payload = sighting.payload()
                payload['observed_at'] = timestamp
                payload.update(event_key=sighting.key, attendance_date=manila_date(timestamp),
                               cooldown_seconds=cooldown, persisted_at=utc_stamp(utc_now()))
                columns = ','.join(payload)
                conn.execute(f'INSERT INTO attendance_events({columns}) VALUES({",".join("?" for _ in payload)})', tuple(payload.values()))
                day = payload['attendance_date']
                existing = conn.execute('SELECT id FROM attendance WHERE student_id=? AND attendance_date=?', (student_id,day)).fetchone()
                if not existing:
                    conn.execute('''INSERT INTO attendance(student_id,student_name,attendance_date,first_seen,last_seen,
                        sighting_count,legacy_summary,college_department,course,report_year_level,report_section,
                        semester_id,semester_name,school_year,provenance) VALUES(?,?,?,?,?,1,0,?,?,?,?,?,?,?,?)''',
                        (student_id,sighting.student_name,day,timestamp,timestamp,sighting.college_department,sighting.course,
                         sighting.report_year_level,sighting.report_section,sighting.semester_id,sighting.semester_name,
                         sighting.school_year,'confirmed_sightings' if sighting.provenance=='confirmed_live_identity' else sighting.provenance))
                else:
                    # Snapshot on summaries describes the earliest new accepted event, never current profile data.
                    first = conn.execute('SELECT * FROM attendance_events WHERE student_id=? AND attendance_date=? ORDER BY observed_at,id LIMIT 1', (student_id,day)).fetchone()
                    conn.execute('''UPDATE attendance SET first_seen=MIN(first_seen,?), last_seen=MAX(last_seen,?),
                        sighting_count=sighting_count+1, student_name=?, college_department=?,course=?,report_year_level=?,
                        report_section=?,semester_id=?,semester_name=?,school_year=? WHERE id=?''',
                        (timestamp,timestamp,first['student_name'],first['college_department'],first['course'],first['report_year_level'],
                         first['report_section'],first['semester_id'],first['semester_name'],first['school_year'],existing['id']))
            if valid_if is not None and not valid_if():
                conn.rollback()
                return False
            return not bool(near)

    def _attendance_query(self, view, filters=None, selected_ids=None):
        self._attendance_staff_only()
        if view not in ('summary','events'):
            raise ValueError('Choose Daily Summary or Sighting Events.')
        filters = validate_filters(filters)
        table = 'attendance' if view == 'summary' else 'attendance_events'
        clauses, args = [], []
        for key, value in filters.items():
            if key in ('start','end'):
                clauses.append(f'a.attendance_date {">=" if key=="start" else "<="} ?')
            elif key == 'source_id' and view == 'summary':
                clauses.append('EXISTS(SELECT 1 FROM attendance_events e WHERE e.student_id=a.student_id AND e.attendance_date=a.attendance_date AND e.source_id=?)')
            elif key in ('name','search'):
                keys = ('student_name',) if key=='name' else ('student_name','student_id','course')
                clauses.append('('+' OR '.join(f'instr(lower(a.{column}),lower(?))>0' for column in keys)+')')
                args.extend([value]*len(keys))
                continue
            else:
                clauses.append(f'a.{key}=?')
            args.append(value)
        if selected_ids is not None:
            ids = [int(i) for i in selected_ids]
            if len(ids)>500:
                raise ValueError('Select no more than 500 rows per export.')
            clauses.append('a.id IN ('+','.join('?' for _ in ids)+')' if ids else '0')
            args.extend(ids)
        sql = f'FROM {table} a' + (' WHERE '+' AND '.join(clauses) if clauses else '')
        order = 'a.attendance_date DESC,a.student_id COLLATE BINARY,a.id' if view=='summary' else 'a.observed_at DESC,a.student_id COLLATE BINARY,a.id DESC'
        return sql, args, order

    def query_attendance(self, *, view='summary', filters=None, page=0, page_size=50):
        if not isinstance(page,int) or page<0 or page_size not in (25,50,100,250):
            raise ValueError('Invalid attendance page or page size.')
        sql,args,order = self._attendance_query(view,filters)
        with self.connect() as conn:
            conn.execute('BEGIN')
            count = conn.execute('SELECT COUNT(*) '+sql,args).fetchone()[0]
            page = min(page,max(0,(count-1)//page_size))
            rows = conn.execute('SELECT a.* '+sql+' ORDER BY '+order+' LIMIT ? OFFSET ?', (*args,page_size,page*page_size)).fetchall()
        return dict(rows=[dict(r) for r in rows],count=count,page=page,page_size=page_size)

    def iter_attendance(self, *, view='summary', filters=None, selected_ids=None):
        sql,args,order = self._attendance_query(view,filters,selected_ids)
        with self.connect() as conn:
            conn.execute('BEGIN')
            cursor = conn.execute('SELECT a.* '+sql+' ORDER BY '+order,args)
            while batch := cursor.fetchmany(250):
                yield from (dict(r) for r in batch)

    def attendance_filter_options(self):
        self._attendance_staff_only()
        with self.connect() as conn:
            semesters = [dict(r) for r in conn.execute('SELECT DISTINCT semester_id,semester_name,school_year FROM attendance_events WHERE semester_id IS NOT NULL ORDER BY school_year DESC,semester_name')]
            sources = [dict(r) for r in conn.execute('SELECT DISTINCT source_id,source_label FROM attendance_events ORDER BY source_label,source_id')]
            return dict(semesters=semesters,sources=sources)

    def get_attendance_report(self, start='', end='', search=''):
        pair = resolve_pair('',search)
        return list(self.iter_attendance(filters=dict(start=start,end=end,search=pair[1] if pair else search)))
