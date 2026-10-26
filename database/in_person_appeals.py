"""Additive audit records for appeals transcribed by authenticated staff."""


def migrate_in_person_appeals(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS in_person_appeals (
        appeal_id INTEGER PRIMARY KEY REFERENCES appeals(id),
        recorded_by TEXT NOT NULL REFERENCES users(username),
        recorded_at TEXT NOT NULL,
        meeting_notes TEXT NOT NULL
    )""")
