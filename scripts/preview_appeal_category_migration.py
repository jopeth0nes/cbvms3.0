"""Verify category migration twice on a disposable read-only-source SQLite backup."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database.appeal_category_migration import migrate_appeal_categories


def verify(source):
    with tempfile.TemporaryDirectory(prefix='cbvms-category-preview-') as folder:
        target=Path(folder)/'copy.db'
        with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as original:
            with sqlite3.connect(target) as copy:
                original.backup(copy)
        with sqlite3.connect(target) as conn:
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            columns={table:[r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')] for table in tables}
            before={table:list(conn.execute(f'SELECT * FROM "{table}"')) for table in tables}
            migrate_appeal_categories(conn);conn.commit()
            after={table:list(conn.execute(f'SELECT * FROM "{table}"')) for table in tables}
            migrate_appeal_categories(conn);conn.commit()
            for table,rows in after.items():
                assert rows==list(conn.execute(f'SELECT * FROM "{table}"')),f'Repeat changed {table}'
            for table,rows in before.items():
                fields=','.join('"'+column+'"' for column in columns[table])
                assert rows==list(conn.execute(f'SELECT {fields} FROM "{table}"')),f'Changed existing {table} data'
            for table in ('appeals','decision_history'):
                if 'decision_category_code' not in columns[table]:
                    assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE decision_category_code IS NOT NULL OR decision_category_label IS NOT NULL OR decision_category_version IS NOT NULL').fetchone()[0]==0,'Fabricated historical categories'
            assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
            return dict(appeals=len(before['appeals']),historical_decisions=len(before['decision_history']),
                preserved_tables=len(tables),repeat_safe=True,integrity='ok',source_unchanged=True)


if __name__=='__main__':
    print(json.dumps(verify(sys.argv[1] if len(sys.argv)>1 else 'data/cbvms.db'),indent=2))
