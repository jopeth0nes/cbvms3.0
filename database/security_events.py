"""Staff-only metadata queries over the existing security event store."""
from datetime import date, datetime, time, timedelta, timezone

from core.attendance import MANILA, validate_filters

UNKNOWN_STATUS = 'Unknown / Unrecognized'
UNAVAILABLE_CAMERA = 'Unavailable (historical camera metadata)'
UNKNOWN_COLUMNS = ('observed_at', 'source_label', 'source_id', 'reference_id',
                   'presence_id', 'status', 'snapshot_available')


def migrate_security_events(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(security_events)')}
    for name in ('event_key', 'source_id', 'source_label', 'session_id'):
        if name not in columns:
            conn.execute(f'ALTER TABLE security_events ADD COLUMN {name} TEXT')
    # NULL keys preserve every historical row, including historical duplicates.
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS security_event_key ON security_events(event_key)')
    conn.execute('CREATE INDEX IF NOT EXISTS security_event_source_time ON security_events(source_id, observed_at)')


class SecurityEventStore:
    def _security_access(self, conn, username):
        if self.student_session_ref is not None or not username or not conn.execute(
                "SELECT 1 FROM users WHERE username=? AND role IN ('admin','superadmin')", (username,)).fetchone():
            raise PermissionError('Unknown sightings require an authenticated admin or superadmin.')

    @staticmethod
    def _security_filter(filters):
        filters = validate_filters(filters)
        if set(filters) - {'start', 'end', 'source_id', 'search'}:
            raise ValueError('Unsupported unknown sightings filter.')
        where, args = ["event_code='unknown_person'"], []
        for key, op in (('start', '>='), ('end', '<')):
            if key in filters:
                day = date.fromisoformat(filters[key])
                if key == 'end':
                    day += timedelta(days=1)
                stamp = datetime.combine(day, time(), MANILA).astimezone(timezone.utc)
                where.append(f'julianday(observed_at){op}julianday(?)')
                args.append(stamp.isoformat())
        if 'source_id' in filters:
            where.append("COALESCE(source_id,'')=?")
            args.append('' if filters['source_id'] == '__historical__' else filters['source_id'])
        if 'search' in filters:
            where.append("(instr(COALESCE(event_key,'legacy:'||id),?)>0 OR instr(presence_id,?)>0)")
            args.extend([filters['search']] * 2)
        return ' AND '.join(where), args

    _security_columns = """id, observed_at, presence_id,
        COALESCE(event_key,'legacy:'||id) reference_id,
        COALESCE(NULLIF(source_id,''),'Unavailable') source_id,
        COALESCE(NULLIF(source_label,''),'Unavailable (historical camera metadata)') source_label,
        CASE WHEN snapshot IS NOT NULL AND length(snapshot)>0 THEN 'Available' ELSE 'Unavailable' END snapshot_available,
        'Unknown / Unrecognized' status"""

    def query_unknown_sightings(self, *, username, filters=None, page=0, page_size=50):
        self._attendance_staff_only()
        where, args = self._security_filter(filters)
        size = max(1, min(250, int(page_size)))
        with self.connect() as conn:
            self._security_access(conn, username)
            conn.execute('BEGIN')
            count = conn.execute(f'SELECT COUNT(*) FROM security_events WHERE {where}', args).fetchone()[0]
            page = min(max(0, int(page)), max(0, (count-1)//size))
            rows = conn.execute(f'SELECT {self._security_columns} FROM security_events WHERE {where} '
                'ORDER BY observed_at DESC,id DESC LIMIT ? OFFSET ?', [*args, size, page*size]).fetchall()
            sources = conn.execute("SELECT DISTINCT COALESCE(source_id,'__historical__') source_id, "
                "COALESCE(source_label,?) source_label FROM security_events WHERE event_code='unknown_person' "
                'ORDER BY source_label,source_id', (UNAVAILABLE_CAMERA,)).fetchall()
        return dict(rows=[dict(r) for r in rows], count=count, page=page, sources=[dict(r) for r in sources])

    def iter_unknown_sightings(self, *, username, filters=None, selected_ids=None):
        self._attendance_staff_only()
        where, args = self._security_filter(filters)
        selected = None if selected_ids is None else {int(i) for i in selected_ids}
        with self.connect() as conn:
            self._security_access(conn, username)
            conn.execute('BEGIN')
            cursor = conn.execute(f'SELECT {self._security_columns} FROM security_events WHERE {where} '
                                  'ORDER BY observed_at DESC,id DESC', args)
            for row in cursor:
                if selected is None or row['id'] in selected:
                    yield dict(row)

    def unknown_snapshot(self, event_id, *, username):
        self._attendance_staff_only()
        with self.connect() as conn:
            self._security_access(conn, username)
            row = conn.execute("SELECT snapshot FROM security_events WHERE id=? AND event_code='unknown_person'",
                               (int(event_id),)).fetchone()
            return row[0] if row else None
