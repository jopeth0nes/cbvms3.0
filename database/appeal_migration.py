"""Versioned publication migration. Never reconcile historical punishments implicitly."""
import json
from core.discipline import format_db_datetime, is_disciplinary_code

VERSION = 'appeals_publication_v1'


def preview(conn):
    counts = {}
    for table in ('violations', 'appeals'):
        counts[table] = {r['status']: r['n'] for r in conn.execute(
            f'SELECT status, COUNT(*) n FROM {table} GROUP BY status')}
    counts['active_strikes'] = conn.execute('SELECT COUNT(*) FROM strikes WHERE is_active=1').fetchone()[0]
    counts['suspensions'] = conn.execute('SELECT COUNT(*) FROM student_suspensions').fetchone()[0]
    counts['conflicts'] = [dict(r) for r in conn.execute("""SELECT v.id violation_id, v.student_id,
        v.status violation_status, a.status appeal_status, s.id strike_id FROM violations v
        JOIN strikes s ON s.violation_id=v.id LEFT JOIN appeals a ON a.violation_id=v.id
        WHERE s.is_active=1 AND (a.status IN ('pending','approved')
            OR v.status IN ('pending_review','unreviewed','dismissed','resolved'))""")]
    columns = {r[1] for r in conn.execute('PRAGMA table_info(violations)')}
    unpublished = ' AND v.appeal_opened_at IS NULL' if 'appeal_opened_at' in columns else ''
    counts['fresh_opportunity_ids'] = [r['id'] for r in conn.execute(f"""SELECT v.* FROM violations v
        JOIN students s ON s.student_id=v.student_id
        WHERE v.status IN ('pending_review','unreviewed')
        AND NOT EXISTS (SELECT 1 FROM appeals a WHERE a.violation_id=v.id)
        AND NOT EXISTS (SELECT 1 FROM strikes st WHERE st.violation_id=v.id) {unpublished}""")
        if is_disciplinary_code(r['violation_code'])]
    return counts


def migrate_appeals(database, conn, now):
    conn.execute('''CREATE TABLE IF NOT EXISTS discipline_reconciliations (
        violation_id INTEGER PRIMARY KEY, strike_id INTEGER NOT NULL,
        actor TEXT NOT NULL, reason TEXT NOT NULL, reconciled_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS workflow_migrations (
        version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, preview_json TEXT NOT NULL)''')
    if conn.execute('SELECT 1 FROM workflow_migrations WHERE version=?', (VERSION,)).fetchone():
        return
    report = preview(conn)
    for vid in report['fresh_opportunity_ids']:
        conn.execute("UPDATE violations SET status='pending_review' WHERE id=?", (vid,))
        database._publish_violation_conn(conn, vid, now, VERSION)
    conn.execute("UPDATE violations SET lifecycle_origin='legacy_preserved' WHERE lifecycle_origin IS NULL")
    for conflict in report['conflicts']:
        conn.execute("UPDATE violations SET lifecycle_origin='reconciliation_required' WHERE id=?",
                     (conflict['violation_id'],))
    conn.execute('INSERT INTO workflow_migrations VALUES (?,?,?)',
                 (VERSION, format_db_datetime(now), json.dumps(report, sort_keys=True)))
