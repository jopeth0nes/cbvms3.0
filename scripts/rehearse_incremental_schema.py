#!/usr/bin/env python3
"""Rehearse additive profile schema twice on disposable copies of a D1 restore.

Never opens the supplied backup for writing or initializes the campus database.
This is schema preservation evidence, not identity adoption or production sync.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def snapshot(conn, tables):
    result={}
    for name in tables:
        quoted='"'+name.replace('"','""')+'"'
        rows=conn.execute('SELECT * FROM '+quoted).fetchall()
        result[name]=hashlib.sha256(repr(sorted(rows,key=repr)).encode()).hexdigest()
    return result


def rehearse(source):
    source=Path(source).resolve()
    if not source.is_file():raise ValueError('A restored backup is required')
    with tempfile.TemporaryDirectory(prefix='cbvms-schema-rehearsal-') as directory:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(Path(directory)/'copy.db') as conn:
            original.backup(conn)
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            baseline=snapshot(conn,tables)
            migration=ROOT/'sites/student-portal/drizzle/0008_incremental_profiles.sql'
            for iteration in range(2):
                conn.execute('BEGIN IMMEDIATE')
                if not conn.execute("SELECT 1 FROM d1_migrations WHERE name=?",(migration.name,)).fetchone():
                    for statement in migration.read_text().split('--> statement-breakpoint'):
                        if statement.strip():conn.execute(statement)
                    conn.execute('INSERT INTO d1_migrations(name) VALUES(?)',(migration.name,))
                conn.commit()
                assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                assert not conn.execute('PRAGMA foreign_key_check').fetchall()
                preserved=[t for t in tables if t!='d1_migrations']
                assert snapshot(conn,preserved)=={t:baseline[t] for t in preserved}
                for table in ['campus_devices','campus_actors','campus_shared_profiles','campus_profile_operations','campus_profile_changes']:
                    assert conn.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
            return {'rehearsals':2,'existing_tables_preserved':len(tables)-1,'integrity':'ok','foreign_key_errors':0,
                    'adopted_students':0,'device_credentials_created':0,'production_writes':0}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cloud-backup',required=True)
    args=parser.parse_args();print(json.dumps(rehearse(args.cloud_backup),sort_keys=True))
