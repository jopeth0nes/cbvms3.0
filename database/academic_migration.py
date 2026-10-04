"""Repeat-safe academic-only migration; snapshots retain exact legacy text."""
from core.academics import resolve_pair, legacy_year_section, normalize_year


def migrate_academics(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS academic_migrations (version INTEGER PRIMARY KEY)')
    conn.execute('''CREATE TABLE IF NOT EXISTS student_academic_legacy (
        student_pk INTEGER PRIMARY KEY, student_id TEXT, college_department TEXT,
        course TEXT, report_year_level TEXT, report_section TEXT, year_and_section TEXT)''')
    if conn.execute('SELECT 1 FROM academic_migrations WHERE version=1').fetchone():
        return
    conn.execute('''INSERT OR IGNORE INTO student_academic_legacy
        SELECT id,student_id,college_department,course,report_year_level,report_section,year_and_section FROM students''')
    for row in conn.execute('SELECT * FROM student_academic_legacy').fetchall():
        pair = resolve_pair(row['college_department'], row['course'])
        if pair:
            conn.execute('UPDATE students SET college_department=?,course=? WHERE id=?', (*pair, row['student_pk']))
        year, section = legacy_year_section(row['year_and_section'])
        # Structured report fields take precedence; the combined field is a compatibility mirror.
        if row['report_year_level']:
            try:
                year = normalize_year(row['report_year_level'])
            except ValueError:
                year = ''  # Preserve unresolved structured text for review, never guess.
        section = row['report_section'] or section
        conn.execute("UPDATE students SET report_year_level=?,report_section=? WHERE id=?",
                     (year or row['report_year_level'], section, row['student_pk']))
        if year:
            combined = ' - '.join(v for v in (year, section) if v)
            conn.execute('UPDATE students SET year_and_section=? WHERE id=?', (combined, row['student_pk']))
    conn.execute('INSERT INTO academic_migrations VALUES (1)')
