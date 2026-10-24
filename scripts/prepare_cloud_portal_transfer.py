"""Restore-check a legacy Sites JSON backup and prepare persistent D1 rows.

Only operates on new disposable output files. Outputs contain private records
and password hashes; keep them in the ignored data/backups directory.
"""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

TABLES = ('portal_students', 'portal_accounts', 'portal_actions',
          'portal_preferences', 'portal_sessions', 'portal_login_limits')
PERSISTENT = TABLES[:4]


def prepare(source, restored, output, migrations):
    if restored.exists() or output.exists():
        raise ValueError('Output paths must be new; backups are never overwritten')
    tables = json.loads(source.read_text(encoding='utf-8'))['tables']
    if {t['name'] for t in tables} != set(TABLES) or len(tables) != len(TABLES):
        raise ValueError('A complete six-table backup is required')
    for t in tables:
        if t['name'] == 'portal_students':
            for row in t['rows']:
                snapshot = json.loads(row['snapshot'])
                if any(v.get('has_snapshot') for v in snapshot.get('violations', [])):
                    raise ValueError('Original evidence requires a separate verified R2 backup')
        if t['name'] == 'portal_actions':
            if any(json.loads(r['payload']).get('evidence_key') for r in t['rows']):
                raise ValueError('Appeal evidence requires a separate verified R2 backup')
    with closing(sqlite3.connect(restored)) as conn, conn:
        files = sorted(migrations.glob('*.sql'))
        if [p.name for p in files] != ['0000_furry_moira_mactaggert.sql', '0001_overrated_medusa.sql',
                                      '0002_sync_protocol.sql', '0003_academic_snapshots.sql',
                                      '0004_publication_roster.sql']:
            raise ValueError('Review the migration set before transferring')
        for migration in files[:2]:
            conn.executescript(migration.read_text(encoding='utf-8'))
        for table in tables:
            name, columns, rows = table['name'], table['columns'], table['rows']
            actual = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            if columns != actual:
                raise ValueError('Backup schema differs from the supported legacy schema')
            fields = ','.join('"'+c+'"' for c in actual)
            placeholders = ','.join('?' for _ in actual)
            conn.executemany(f'INSERT INTO "{name}" ({fields}) VALUES ({placeholders})',
                             [[r[c] for c in actual] for r in rows])
            if conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] != len(rows):
                raise ValueError('Restore count mismatch')
            got = conn.execute(f'SELECT {fields} FROM "{name}"').fetchall()
            expected = [tuple(r[c] for c in actual) for r in rows]
            if sorted(map(repr, got)) != sorted(map(repr, expected)):
                raise ValueError('Restore value mismatch')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Restore integrity check failed')
        for migration in files[2:]:
            conn.executescript(migration.read_text(encoding='utf-8'))
        statements = []
        for line in conn.iterdump():
            if any(line.startswith(f'INSERT INTO "{name}" ') for name in PERSISTENT):
                statements.append(line)
        output.write_text('\n'.join(statements)+'\n', encoding='utf-8')
    return {'restore_check': 'passed', 'persistent_rows': sum(len(t['rows']) for t in tables if t['name'] in PERSISTENT),
            'transient_rows_backed_up_only': sum(len(t['rows']) for t in tables if t['name'] not in PERSISTENT)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--restored', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    migrations = Path(__file__).resolve().parents[1] / 'sites/student-portal/drizzle'
    print(json.dumps(prepare(args.backup, args.restored, args.output, migrations)))
