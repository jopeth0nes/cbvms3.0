"""Additive, repeat-safe storage for immutable evidence-forensics runs."""

TABLE = """
CREATE TABLE IF NOT EXISTS evidence_forensics_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 evidence_id INTEGER NOT NULL REFERENCES evidence_files(id) ON DELETE CASCADE,
 appeal_id INTEGER NOT NULL REFERENCES appeals(id) ON DELETE CASCADE,
 source_sha256 TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','analyzing','complete','error')),
 classification TEXT NOT NULL DEFAULT 'inconclusive',
 analyzer_version TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 started_at TEXT,
 completed_at TEXT,
 lease_expires_at TEXT,
 claim_token TEXT,
 signals_json TEXT NOT NULL DEFAULT '[]',
 metadata_json TEXT NOT NULL DEFAULT '{}',
 facts_json TEXT NOT NULL DEFAULT '{}',
 provenance_json TEXT NOT NULL DEFAULT '{}',
 model_json TEXT NOT NULL DEFAULT '{}',
 reliability_json TEXT NOT NULL DEFAULT '{}',
 risk REAL,
 localization_png BLOB,
 reliability_png BLOB,
 duration_ms INTEGER,
 error_code TEXT
)
"""

def migrate_evidence_forensics(conn):
    conn.execute(TABLE)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evidence_forensics_runs)")}
    if "claim_token" not in cols:
        conn.execute("ALTER TABLE evidence_forensics_runs ADD COLUMN claim_token TEXT")
    if "facts_json" not in cols:
        conn.execute("ALTER TABLE evidence_forensics_runs ADD COLUMN facts_json TEXT NOT NULL DEFAULT '{}'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forensics_evidence ON evidence_forensics_runs(evidence_id,id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forensics_appeal ON evidence_forensics_runs(appeal_id,id DESC)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_forensics_active_evidence ON evidence_forensics_runs(evidence_id) WHERE status IN ('pending','analyzing')")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_forensics_single_claim ON evidence_forensics_runs(status) WHERE status='analyzing'")
