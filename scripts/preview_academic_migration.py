"""Verify Step 1 on an ephemeral SQLite backup, never write the source database.

Usage: python scripts/preview_academic_migration.py [path/to/database.db]
Only counts and integrity results are printed; no student or account data.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.academic_migration import migrate_academics
from database.student_management import migrate_student_management
from core.academics import resolve_pair


def verify(source):
    with tempfile.TemporaryDirectory(prefix='cbvms-academic-preview-') as folder:
        target = Path(folder) / 'copy.db'
        with sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True) as original:
            with sqlite3.connect(target) as copy:
                original.backup(copy)
        with sqlite3.connect(target) as conn:
            conn.row_factory = sqlite3.Row
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            academic_tables = {'students', 'student_academic_legacy', 'academic_migrations', 'sqlite_sequence'}
            preserved = {table: [tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')]
                         for table in tables if table not in academic_tables}
            students = {r['id']: dict(r) for r in conn.execute('SELECT * FROM students')}
            already_applied = ('academic_migrations' in tables and
                conn.execute('SELECT 1 FROM academic_migrations WHERE version=1').fetchone() is not None)
            migrate_student_management(conn)
            migrate_academics(conn)
            conn.commit()
            after = [tuple(r) for r in conn.execute('SELECT * FROM students')]
            migrate_academics(conn)
            assert after == [tuple(r) for r in conn.execute('SELECT * FROM students')], 'Repeat migration changed students'
            for table, rows in preserved.items():
                assert rows == [tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')], f'Changed table: {table}'
            changed_keys = {'college_department', 'course', 'report_section', 'report_year_level', 'year_and_section'}
            for row in conn.execute('SELECT * FROM students'):
                for key, value in students[row['id']].items():
                    if key not in changed_keys:
                        assert row[key] == value, f'Changed protected student column: {key}'
            for row in conn.execute('SELECT * FROM student_academic_legacy'):
                original = students.get(row['student_pk'])
                if original and not already_applied:
                    for key in ('course', 'college_department', 'report_section', 'report_year_level', 'year_and_section'):
                        assert row[key] == original.get(key, ''), f'Legacy text not preserved: {key}'
            result = conn.execute('PRAGMA integrity_check').fetchone()[0]
            assert result == 'ok', result
            unresolved = sum(resolve_pair(r['college_department'], r['course']) is None
                             for r in conn.execute('SELECT * FROM students'))
            return dict(students=len(students), unresolved=unresolved, protected_tables=len(preserved),
                        integrity=result, repeat_safe=True, source_unchanged=True)


if __name__ == '__main__':
    print(json.dumps(verify(sys.argv[1] if len(sys.argv) > 1 else 'data/cbvms.db'), indent=2))
