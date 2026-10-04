"""Read-only source backup; test additive attendance migration twice on a disposable copy."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.attendance import migrate_attendance


def verify(source):
    with tempfile.TemporaryDirectory(prefix='cbvms-attendance-preview-') as folder:
        target=Path(folder)/'copy.db'
        with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as original:
            with sqlite3.connect(target) as copy:
                original.backup(copy)
        with sqlite3.connect(target) as conn:
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            columns={table:[r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')] for table in tables}
            before={table:list(conn.execute(f'SELECT * FROM "{table}"')) for table in tables}
            migrate_attendance(conn)
            conn.commit()
            after={r[0]:list(conn.execute('SELECT * FROM "'+r[0]+'"'))
                   for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            migrate_attendance(conn)
            conn.commit()
            for table,rows in after.items():
                assert rows==list(conn.execute(f'SELECT * FROM "{table}"')),f'Repeat changed {table}'
            for table,rows in before.items():
                fields=','.join('"'+c+'"' for c in columns[table])
                assert rows==list(conn.execute(f'SELECT {fields} FROM "{table}"')),f'Changed existing {table} data'
            assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
            events=conn.execute('SELECT COUNT(*) FROM attendance_events').fetchone()[0]
            assert events==len(before.get('attendance_events',[])), 'Fabricated historic events'
            return dict(legacy_summaries=conn.execute('SELECT COUNT(*) FROM attendance_legacy').fetchone()[0],
                events=events,protected_tables=len(before),repeat_safe=True,integrity='ok',source_unchanged=True)


if __name__=='__main__':
    print(json.dumps(verify(sys.argv[1] if len(sys.argv)>1 else 'data/cbvms.db'),indent=2))
