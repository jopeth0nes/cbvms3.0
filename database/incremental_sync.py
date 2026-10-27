"""Durable, opt-in local storage for the independent incremental sync protocol."""
from __future__ import annotations

import sqlite3
import json


def migrate(conn: sqlite3.Connection) -> None:
    """Create protocol tables inside the caller's active transaction; never commit."""
    if not conn.in_transaction:
        raise ValueError('Migration requires an active caller transaction')
    conn.execute('SAVEPOINT incremental_schema_migration')
    try:
        _migrate(conn)
    except BaseException:
        conn.execute('ROLLBACK TO incremental_schema_migration')
        conn.execute('RELEASE incremental_schema_migration')
        raise
    else:
        conn.execute('RELEASE incremental_schema_migration')


def _migrate(conn: sqlite3.Connection) -> None:
    marker_exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='incremental_schema_revision'").fetchone()
    if marker_exists and conn.execute('SELECT 1 FROM incremental_schema_revision WHERE version=1').fetchone():
        return
    script = '''
    CREATE TABLE IF NOT EXISTS incremental_device (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), device_id TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS incremental_entity_versions (
      entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=0),
      PRIMARY KEY(entity_type,entity_id));
    CREATE TABLE IF NOT EXISTS incremental_outbox (
      sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL, entity_type TEXT NOT NULL,
      entity_id TEXT NOT NULL, expected_version INTEGER NOT NULL CHECK(expected_version>=0),
      operation_type TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
      dependencies TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL CHECK(state IN ('pending','accepted','conflict','auth_required')),
      retry_count INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
      last_error TEXT, receipt TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS incremental_dependencies (
      operation_id TEXT NOT NULL REFERENCES incremental_outbox(operation_id) ON DELETE CASCADE,
      dependency_id TEXT NOT NULL REFERENCES incremental_outbox(operation_id),
      PRIMARY KEY(operation_id,dependency_id));
    CREATE INDEX IF NOT EXISTS incremental_dependencies_parent ON incremental_dependencies(dependency_id,operation_id);
    CREATE TABLE IF NOT EXISTS incremental_sync_status (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), auth_required INTEGER NOT NULL DEFAULT 0,
      uploaded_count INTEGER NOT NULL DEFAULT 0, downloaded_count INTEGER NOT NULL DEFAULT 0,
      last_upload_at REAL, last_download_at REAL);
    INSERT OR IGNORE INTO incremental_sync_status(singleton) VALUES(1);
    CREATE TABLE IF NOT EXISTS incremental_inbound_guard (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), applying INTEGER NOT NULL DEFAULT 0);
    INSERT OR IGNORE INTO incremental_inbound_guard(singleton) VALUES(1);
    CREATE TABLE IF NOT EXISTS incremental_inbox (
      cursor INTEGER PRIMARY KEY CHECK(cursor>0), operation_id TEXT NOT NULL UNIQUE,
      payload_digest TEXT NOT NULL, received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS incremental_conflicts (
      operation_id TEXT PRIMARY KEY REFERENCES incremental_outbox(operation_id),
      detail TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS incremental_cursor (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), cursor INTEGER NOT NULL CHECK(cursor>=0));
    INSERT OR IGNORE INTO incremental_cursor(singleton,cursor) VALUES(1,0);
    CREATE TABLE IF NOT EXISTS incremental_pages (
      after_cursor INTEGER PRIMARY KEY, next_cursor INTEGER NOT NULL, page_digest TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS incremental_schema_revision (
      version INTEGER PRIMARY KEY CHECK(version>0), applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    '''
    for statement in script.split(';'):
        if statement.strip():
            conn.execute(statement)
    cols = {r[1] for r in conn.execute('PRAGMA table_info(incremental_outbox)')}
    if 'sequence' not in cols:
        conn.execute('ALTER TABLE incremental_outbox ADD COLUMN sequence INTEGER')
        conn.execute('UPDATE incremental_outbox SET sequence=rowid WHERE sequence IS NULL')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS incremental_outbox_sequence ON incremental_outbox(sequence)')
    if 'receipt' not in cols:
        conn.execute('ALTER TABLE incremental_outbox ADD COLUMN receipt TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS incremental_outbox_order ON incremental_outbox(sequence,state)')
    conn.execute('CREATE INDEX IF NOT EXISTS incremental_outbox_entity ON incremental_outbox(entity_type,entity_id,sequence)')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS incremental_outbox_sequence_fill
      AFTER INSERT ON incremental_outbox WHEN NEW.sequence IS NULL
      BEGIN UPDATE incremental_outbox SET sequence=(SELECT COALESCE(MAX(sequence),0)+1
        FROM incremental_outbox WHERE rowid<>NEW.rowid) WHERE rowid=NEW.rowid; END''')
    # Older unreleased local databases stored dependencies only as JSON. Validate
    # and backfill them once before recording the schema revision.
    for row in conn.execute('SELECT operation_id,sequence,dependencies FROM incremental_outbox').fetchall():
        try:
            deps=json.loads(row[2])
        except (TypeError, ValueError):
            raise ValueError('Cannot migrate malformed incremental dependencies')
        if not isinstance(deps,list) or len(deps)>32 or any(not isinstance(d,str) or not d for d in deps):
            raise ValueError('Cannot migrate invalid incremental dependencies')
        for dep in deps:
            target=conn.execute('SELECT sequence FROM incremental_outbox WHERE operation_id=?',(dep,)).fetchone()
            if target is None or target[0] >= row[1]:
                raise ValueError('Cannot migrate unknown or non-earlier incremental dependency')
            conn.execute('INSERT OR IGNORE INTO incremental_dependencies(operation_id,dependency_id) VALUES(?,?)',(row[0],dep))
    conn.execute('INSERT OR IGNORE INTO incremental_schema_revision(version) VALUES(1)')
