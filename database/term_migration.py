"""Additive version 1. No historical assignments, profiles or penalties are inferred."""
import hashlib
import sqlite3
from pathlib import Path

VERSION = 'academic-enrollment-v1'
EXCEPTION_VERSION = 'academic-enrollment-exception-v1'


def migrate_enrollment_exceptions(conn):
    """Add repeat-safe audited exception proof to an existing calendar schema.

    This targeted helper does not initialize CBVMS or modify enrollment rows.
    """
    required = ('student_term_enrollments', 'students', 'academic_terms', 'users')
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [name for name in required if name not in present]
    if missing:
        raise ValueError('Academic exception migration requires existing tables: ' + ', '.join(missing))
    conn.execute('''CREATE TABLE IF NOT EXISTS enrollment_administrative_exceptions (
        id INTEGER PRIMARY KEY,
        enrollment_id INTEGER NOT NULL UNIQUE REFERENCES student_term_enrollments(id),
        student_id TEXT NOT NULL REFERENCES students(student_id),
        semester_id INTEGER NOT NULL REFERENCES academic_terms(id),
        actor TEXT NOT NULL REFERENCES users(username),
        reason TEXT NOT NULL CHECK(length(trim(reason))>0),
        effective_at TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE,
        payload_hash TEXT NOT NULL)''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS enrollment_exception_owner_insert
        BEFORE INSERT ON enrollment_administrative_exceptions
        WHEN NOT EXISTS (SELECT 1 FROM student_term_enrollments e
          WHERE e.id=NEW.enrollment_id AND e.student_id=NEW.student_id
            AND e.semester_id=NEW.semester_id AND e.status='draft' AND e.capture_id IS NULL)
        BEGIN SELECT RAISE(ABORT,'Administrative exception enrollment mismatch'); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS enrollment_exception_immutable_update
        BEFORE UPDATE ON enrollment_administrative_exceptions
        BEGIN SELECT RAISE(ABORT,'Administrative enrollment exception is immutable'); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS enrollment_exception_immutable_delete
        BEFORE DELETE ON enrollment_administrative_exceptions
        BEGIN SELECT RAISE(ABORT,'Administrative enrollment exception is immutable'); END''')
    conn.execute('''CREATE TABLE IF NOT EXISTS campus_schema_versions (
        version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('INSERT OR IGNORE INTO campus_schema_versions(version) VALUES(?)', (EXCEPTION_VERSION,))


def migrate(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS campus_schema_versions (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    additions = {
        'academic_terms': {'start_date': 'TEXT', 'end_date': 'TEXT', 'starts_at': 'TEXT', 'ends_before': 'TEXT',
            'publication_state': "TEXT NOT NULL DEFAULT 'draft'", 'published_at': 'TEXT', 'published_by': 'TEXT',
            'revision': 'INTEGER NOT NULL DEFAULT 0'},
        'violations': {'enrollment_status': "TEXT NOT NULL DEFAULT 'legacy_unverified'", 'academic_snapshot': 'TEXT',
                       'term_classification': "TEXT NOT NULL DEFAULT 'legacy_assignment'"},
        'attendance_events': {'enrollment_status': "TEXT NOT NULL DEFAULT 'legacy_unverified'", 'term_classification': "TEXT NOT NULL DEFAULT 'legacy_assignment'"},
        'security_events': {'semester_id': 'INTEGER REFERENCES academic_terms(id)', 'term_classification': "TEXT NOT NULL DEFAULT 'legacy_unverified'"},
        'student_suspensions': {'effective_ended_at': 'TEXT', 'effective_end_reason': 'TEXT'},
    }
    for table, fields in additions.items():
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not cols:
            continue
        for name, declaration in fields.items():
            if name not in cols:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
    for ddl in (
        '''CREATE TABLE IF NOT EXISTS term_assignment_reviews (
            id INTEGER PRIMARY KEY, source_table TEXT NOT NULL, record_id INTEGER NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT, resolution_reason TEXT,
            UNIQUE(source_table,record_id,reason))''',
        '''CREATE TABLE IF NOT EXISTS academic_boundary_receipts (
            semester_id INTEGER NOT NULL REFERENCES academic_terms(id),kind TEXT NOT NULL,boundary TEXT NOT NULL,
            PRIMARY KEY(semester_id,kind,boundary))''',
        '''CREATE TABLE IF NOT EXISTS enrollment_review_receipts (
            student_id TEXT NOT NULL,source_id TEXT NOT NULL,observed_at TEXT NOT NULL,PRIMARY KEY(student_id,source_id,observed_at))''',
        '''CREATE TABLE IF NOT EXISTS enrollment_revisions (
            id INTEGER PRIMARY KEY, enrollment_id INTEGER NOT NULL REFERENCES student_term_enrollments(id),
            student_id TEXT NOT NULL REFERENCES students(student_id), semester_id INTEGER NOT NULL REFERENCES academic_terms(id),
            effective_at TEXT NOT NULL, snapshot TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS academic_audit (
            id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
            action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
            before_json TEXT, after_json TEXT, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS enrollment_captures (
            id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
            semester_id INTEGER NOT NULL REFERENCES academic_terms(id),
            encoding BLOB NOT NULL, photo BLOB NOT NULL, photo_sha256 TEXT NOT NULL UNIQUE,
            provenance TEXT NOT NULL, captured_at TEXT NOT NULL, actor TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1)''',
        '''CREATE TABLE IF NOT EXISTS student_term_enrollments (
            id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
            semester_id INTEGER NOT NULL REFERENCES academic_terms(id),
            college_department TEXT NOT NULL, course TEXT NOT NULL,
            report_year_level TEXT NOT NULL, report_section TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','confirmed','withdrawn')),
            capture_id TEXT UNIQUE REFERENCES enrollment_captures(id),
            confirmed_at TEXT, confirmed_by TEXT REFERENCES users(username),
            CHECK(status!='confirmed' OR (capture_id IS NOT NULL AND confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL)),
            UNIQUE(student_id,semester_id))''',
        '''CREATE TABLE IF NOT EXISTS enrollment_reviews (
            id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
            semester_id INTEGER REFERENCES academic_terms(id), enrollment_status TEXT NOT NULL,
            observed_at TEXT NOT NULL, last_seen TEXT NOT NULL, source_id TEXT NOT NULL,
            snapshot BLOB, snapshot_sha256 TEXT, occurrences INTEGER NOT NULL DEFAULT 1,
            resolved_at TEXT, resolved_by TEXT, resolution_reason TEXT)''',
        '''CREATE UNIQUE INDEX IF NOT EXISTS enrollment_review_open ON enrollment_reviews
            (student_id,COALESCE(semester_id,-1),enrollment_status,source_id) WHERE resolved_at IS NULL''',
        '''CREATE TABLE IF NOT EXISTS enrollment_review_events (
            id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL REFERENCES enrollment_reviews(id),
            observed_at TEXT NOT NULL, snapshot BLOB, snapshot_sha256 TEXT,
            UNIQUE(review_id,observed_at))''',
        '''CREATE TABLE IF NOT EXISTS enrollment_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), alarm_cooldown INTEGER NOT NULL CHECK(alarm_cooldown BETWEEN 1 AND 86400))''',
        'INSERT OR IGNORE INTO enrollment_settings VALUES(1,300)',
        '''CREATE TRIGGER IF NOT EXISTS enrollment_capture_owner BEFORE INSERT ON student_term_enrollments
            WHEN NEW.capture_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM enrollment_captures c
              WHERE c.id=NEW.capture_id AND c.student_id=NEW.student_id AND c.semester_id=NEW.semester_id)
            BEGIN SELECT RAISE(ABORT,'Capture owner mismatch'); END''',
        '''CREATE TRIGGER IF NOT EXISTS enrollment_capture_owner_update BEFORE UPDATE ON student_term_enrollments
            WHEN NEW.capture_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM enrollment_captures c
              WHERE c.id=NEW.capture_id AND c.student_id=NEW.student_id AND c.semester_id=NEW.semester_id)
            BEGIN SELECT RAISE(ABORT,'Capture owner mismatch'); END''',
    ):
        conn.execute(ddl)
    for operation in ('INSERT', 'UPDATE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS campus_calendar_{operation.lower()} BEFORE {operation} ON academic_terms
          WHEN NEW.publication_state='published' BEGIN
          SELECT CASE WHEN NEW.starts_at IS NULL OR NEW.ends_before IS NULL OR julianday(NEW.starts_at)>=julianday(NEW.ends_before)
            THEN RAISE(ABORT,'Published terms require valid boundaries') END;
          SELECT CASE WHEN EXISTS(SELECT 1 FROM academic_terms t WHERE t.id!=NEW.id AND t.publication_state='published'
            AND julianday(t.starts_at)<julianday(NEW.ends_before) AND julianday(t.ends_before)>julianday(NEW.starts_at))
            THEN RAISE(ABORT,'Published terms overlap') END; END''')
    # Guard loose legacy relationships as well as declared foreign keys.
    conn.execute('''CREATE TRIGGER IF NOT EXISTS referenced_term_delete BEFORE DELETE ON academic_terms
        WHEN EXISTS(SELECT 1 FROM violations WHERE semester_id=OLD.id)
          OR EXISTS(SELECT 1 FROM attendance WHERE semester_id=OLD.id)
          OR EXISTS(SELECT 1 FROM attendance_events WHERE semester_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'Referenced academic term cannot be deleted'); END''')
    conn.execute('''CREATE VIEW IF NOT EXISTS attendance_report_summaries AS
        WITH ranked AS (SELECT e.*,
          ROW_NUMBER() OVER(PARTITION BY student_id,attendance_date,semester_id ORDER BY observed_at,id) AS position,
          MIN(observed_at) OVER(PARTITION BY student_id,attendance_date,semester_id) AS first_time,
          MAX(observed_at) OVER(PARTITION BY student_id,attendance_date,semester_id) AS last_time,
          COUNT(*) OVER(PARTITION BY student_id,attendance_date,semester_id) AS sightings
          FROM attendance_events e)
        SELECT -id AS id,student_id,student_name,attendance_date,first_time AS first_seen,last_time AS last_seen,
          sightings AS sighting_count,0 AS legacy_summary,college_department,course,report_year_level,report_section,
          semester_id,semester_name,school_year,provenance FROM ranked WHERE position=1
        UNION ALL SELECT id,student_id,student_name,attendance_date,first_seen,last_seen,sighting_count,
          legacy_summary,college_department,course,report_year_level,report_section,semester_id,semester_name,
          school_year,provenance FROM attendance WHERE legacy_summary=1''')
    conn.execute('INSERT OR IGNORE INTO campus_schema_versions(version) VALUES(?)', (VERSION,))
    migrate_enrollment_exceptions(conn)


def preview(path):
    """SQLite read-only URI; never instantiate CBVMSDatabase or invoke startup."""
    path = Path(path).resolve()
    with sqlite3.connect(path.as_uri() + '?mode=ro',uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in
                  ('students','academic_terms','violations','strikes','appeals','attendance','attendance_events','security_events','student_term_enrollments') if t in tables}
        terms = [dict(r) for r in conn.execute('SELECT * FROM academic_terms')] if 'academic_terms' in tables else []
        missing, invalid = {}, {}
        for table in ('violations','strikes','attendance','attendance_events','security_events'):
            if table not in tables or 'semester_id' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
                continue
            missing[table] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE semester_id IS NULL').fetchone()[0]
            invalid[table] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE semester_id IS NOT NULL AND semester_id NOT IN(SELECT id FROM academic_terms)').fetchone()[0]
        evidence = {}
        date_conflicts, invalid_timestamps = {}, {}
        dated_terms = 'academic_terms' in tables and 'starts_at' in {r[1] for r in conn.execute('PRAGMA table_info(academic_terms)')}
        for table,column in (('violations','timestamp'),('attendance_events','observed_at'),('security_events','observed_at')):
            if table not in tables:
                continue
            columns={r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            if column not in columns:
                continue
            invalid_timestamps[table]=[r[0] for r in conn.execute(f'SELECT id FROM {table} WHERE julianday({column}) IS NULL')]
            if dated_terms and 'semester_id' in columns:
                date_conflicts[table]=[r[0] for r in conn.execute(f'''SELECT e.id FROM {table} e
                    JOIN academic_terms t ON t.id=e.semester_id WHERE t.publication_state='published'
                    AND (julianday(e.{column})<julianday(t.starts_at) OR julianday(e.{column})>=julianday(t.ends_before))''')]
        evidence_mismatches = {}
        relationships = {t:[tuple(r) for r in conn.execute(f'PRAGMA foreign_key_list("{t}")')] for t in counts}
        for table, column in (('violations','snapshot'),('evidence_files','file_data'),('security_events','snapshot'),
                              ('enrollment_reviews','snapshot'),('enrollment_review_events','snapshot')):
            if table in tables:
                rows = conn.execute(f'SELECT id,{column} FROM {table} WHERE {column} IS NOT NULL')
                evidence[table] = {str(r[0]): hashlib.sha256(r[1]).hexdigest() for r in rows}
                hash_column='file_sha256' if table=='evidence_files' else 'snapshot_sha256'
                if hash_column in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
                    evidence_mismatches[table]=[r[0] for r in conn.execute(f'SELECT id,{hash_column} FROM {table} WHERE {hash_column} IS NOT NULL')
                        if evidence[table].get(str(r[0]))!=r[1]]
        return dict(version=VERSION, terms=terms, counts=counts, missing_assignments=missing,
                    date_conflicts=date_conflicts, invalid_timestamps=invalid_timestamps,
                    inconsistent_assignments=invalid, foreign_key_errors=[tuple(r) for r in conn.execute('PRAGMA foreign_key_check')],
                    evidence_hashes=evidence, evidence_hash_mismatches=evidence_mismatches, foreign_key_relationships=relationships,
                    preserved_assignments={t:counts[t]-missing[t]-invalid[t] for t in missing}, proposed='Add calendar/enrollment/audit schema only. Preserve every assignment and ID. Existing terms remain draft until dates are approved.',
                    unresolved_legacy_students=(conn.execute("SELECT COUNT(*) FROM students s WHERE NOT EXISTS(SELECT 1 FROM student_term_enrollments e WHERE e.student_id=s.student_id AND e.confirmed_at IS NOT NULL)").fetchone()[0] if 'student_term_enrollments' in tables else counts.get('students',0)), historical_enrollments_created=0)
