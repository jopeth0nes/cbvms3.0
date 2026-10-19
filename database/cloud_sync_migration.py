"""Additive local protocol migration; registration is an explicit operator action."""
def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS campus_sync_identity (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), source_id TEXT NOT NULL,
        writer_id TEXT NOT NULL, protocol INTEGER NOT NULL DEFAULT 2,
        generation INTEGER NOT NULL DEFAULT 0, pending_run TEXT,
        verified_through TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS cloud_portal_receipts (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT NOT NULL, digest TEXT)''')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(cloud_portal_receipts)')}
    if 'digest' not in columns:
        conn.execute('ALTER TABLE cloud_portal_receipts ADD COLUMN digest TEXT')
