"""Synthetic verified records for tests of downstream discipline, not capture validation.

Only use on disposable test databases. Capture security is exercised separately in
 test_term_enrollment; these fixtures never run in application/migration code.
"""
import json
import uuid
from core.academic_calendar import bounds


def verified_term(db, student_id, year=2026):
    with db.connect() as conn:
        row=conn.execute("SELECT id FROM academic_terms WHERE semester_code='semester_1' AND school_year=?",(f'{year}-{year+1}',)).fetchone()
        if row:term=row[0]
        else:
            start,end=bounds(f'{year}-01-01',f'{year}-12-31')
            term=conn.execute("""INSERT INTO academic_terms(semester_code,semester_name,school_year,start_date,end_date,
                starts_at,ends_before,publication_state) VALUES('semester_1','First Semester',?,?,?,?,?,'published')""",
                (f'{year}-{year+1}',f'{year}-01-01',f'{year}-12-31',start,end)).lastrowid
        capture=uuid.uuid4().hex
        actor=conn.execute("SELECT username FROM users WHERE role='superadmin' LIMIT 1").fetchone()[0]
        conn.execute('''INSERT INTO enrollment_captures(id,student_id,semester_id,encoding,photo,photo_sha256,provenance,captured_at,actor)
            VALUES(?,?,?,X'01',X'01',?,'{"capture_hashes":[]}',?,?)''',(capture,student_id,term,capture,f'{year-1}-01-01',actor))
        conn.execute('''INSERT INTO student_term_enrollments(student_id,semester_id,college_department,course,report_year_level,
            report_section,status,capture_id,confirmed_at,confirmed_by)
            SELECT student_id,?,college_department,course,report_year_level,report_section,'confirmed',?,?,?
            FROM students WHERE student_id=?''',(term,capture,f'{year-1}-01-01',actor,student_id))
        return term
