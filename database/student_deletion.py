"""Explicit superadmin deletion of a recycled local student, in one transaction."""
from pathlib import Path


def connection_configured():
    data = Path(__file__).resolve().parents[1] / 'data'
    return any((data / filename).exists() for filename in ('cloud-sync.json', 'registration-sync.json'))


def permanently_delete(db, student_pk, student_id, actor_token, expected_version, reason):
    if db.student_session_ref is not None:
        raise PermissionError('Only a current superadmin may permanently delete students.')
    if not (reason or '').strip():
        raise ValueError('A reason for permanent deletion is required.')
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor = db._staff_actor(conn, actor_token)
        if not actor or actor['role'] != 'superadmin':
            raise PermissionError('Only a current superadmin may permanently delete students.')
        student = conn.execute('SELECT * FROM students WHERE id=?', (student_pk,)).fetchone()
        if (not student or student['student_id'] != student_id or not student['is_archived']
                or student['archive_version'] != expected_version):
            raise ValueError('Select a current Recycle Bin entry and refresh before deleting.')
        # Local deletion cannot revoke or erase a separately hosted identity.
        if (conn.execute('SELECT 1 FROM campus_sync_identity').fetchone()
                or connection_configured()
                or conn.execute("SELECT 1 FROM student_archive_operations WHERE student_id=? AND state='verified'", (student_id,)).fetchone()):
            raise ValueError('This installation has cloud identity history. Reconcile the hosted identity before permanent deletion; restore the existing student to reuse it safely.')
        _purge_student_conn(conn, student_pk, student_id, expected_version, actor, reason)
    return True


def _purge_student_conn(conn, student_pk, student_id, expected_version, actor, reason):
    """Internal transaction body; caller must authenticate and reconcile cloud first."""
    # These two guards protect normal history/account edits. SQLite DDL is
    # transactional: restore them before commit; any failure rolls everything back.
    names = ('attendance_events_immutable_delete', 'preserve_recycled_student_account_delete')
    guards = list(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN (?,?)", names))
    for guard in guards:
        conn.execute('DROP TRIGGER "' + guard['name'] + '"')
    conn.execute('DELETE FROM student_sessions WHERE account_id IN (SELECT id FROM student_accounts WHERE student_id=?)', (student_id,))
    conn.execute('DELETE FROM student_accounts WHERE student_id=?', (student_id,))
    conn.execute('DELETE FROM discipline_reconciliations WHERE violation_id IN (SELECT id FROM violations WHERE student_id=?)', (student_id,))
    for table in ('violations', 'attendance'):
        conn.execute('DELETE FROM term_assignment_reviews WHERE source_table=? AND record_id IN (SELECT id FROM ' + table + ' WHERE student_id=?)', (table, student_id))
    conn.execute('DELETE FROM in_person_appeals WHERE appeal_id IN (SELECT id FROM appeals WHERE student_id=?)', (student_id,))
    conn.execute('DELETE FROM evidence_forensics_runs WHERE appeal_id IN (SELECT id FROM appeals WHERE student_id=?)', (student_id,))
    conn.execute('DELETE FROM evidence_files WHERE appeal_id IN (SELECT id FROM appeals WHERE student_id=?)', (student_id,))
    conn.execute('DELETE FROM enrollment_review_events WHERE review_id IN (SELECT id FROM enrollment_reviews WHERE student_id=?)', (student_id,))
    conn.execute('DELETE FROM attendance_receipts WHERE event_key IN (SELECT event_key FROM attendance_events WHERE student_id=?)', (student_id,))
    tables = ('decision_history', 'student_notifications', 'appeals', 'strikes', 'strike_events',
              'violations', 'automatic_suspension_awards', 'student_suspensions',
              'student_status_history', 'premises_entries', 'attendance_events', 'attendance',
              'attendance_legacy', 'enrollment_revisions', 'student_term_enrollments',
              'enrollment_captures', 'enrollment_reviews', 'enrollment_review_receipts', 'student_academic_legacy')
    for table in tables:
        conn.execute(f'DELETE FROM {table} WHERE student_id=?', (student_id,))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='portal_preferences'").fetchone():
        conn.execute('DELETE FROM portal_preferences WHERE student_id=?', (student_id,))
    conn.execute('DELETE FROM students WHERE id=?', (student_pk,))
    # Keep the immutable lifecycle audit so deletion cannot erase who acted.
    import uuid
    conn.execute('''INSERT INTO student_archive_audit
        (student_id,operation_id,actor,action,occurred_at,actor_id,actor_role,
         operation_type,lifecycle_version,reason)
        VALUES(?,?,?,'permanently_deleted',datetime('now'),?,'superadmin','delete',?,?)''',
        (student_id, str(uuid.uuid4()), actor['username'], actor['id'], expected_version + 1, reason.strip()))
    for guard in guards:
        conn.execute(guard['sql'])
    return True
