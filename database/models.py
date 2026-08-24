"""SQLite table schemas for CBVMS."""

USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

STUDENTS_TABLE = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    course TEXT,
    year_and_section TEXT,
    gender TEXT DEFAULT 'Unknown',
    email TEXT DEFAULT '',
    encoding BLOB,
    photo BLOB,
    enrolled_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

ACADEMIC_TERMS_TABLE = """
CREATE TABLE IF NOT EXISTS academic_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    semester_code TEXT NOT NULL,
    semester_name TEXT NOT NULL,
    school_year TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (semester_code, school_year)
);
"""

VIOLATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT,
    student_name TEXT,
    violation_type TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    snapshot BLOB,
    status TEXT NOT NULL DEFAULT 'pending_review',
    violation_code TEXT NOT NULL DEFAULT 'unknown_violation',
    review_deadline TEXT,
    confirmed_at TEXT,
    appeal_deadline TEXT,
    appeal_window_closed_at TEXT,
    review_decided_at TEXT,
    reviewed_by TEXT DEFAULT '',
    dismissal_reason TEXT DEFAULT '',
    semester_id INTEGER REFERENCES academic_terms(id)
);
"""

STRIKES_TABLE = """
CREATE TABLE IF NOT EXISTS strikes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER NOT NULL UNIQUE REFERENCES violations(id) ON DELETE CASCADE,
    student_id TEXT NOT NULL,
    violation_code TEXT NOT NULL,
    semester_id INTEGER NOT NULL REFERENCES academic_terms(id),
    awarded_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    deactivated_at TEXT,
    deactivation_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

STRIKE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS strike_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    violation_code TEXT NOT NULL,
    semester_id INTEGER NOT NULL REFERENCES academic_terms(id),
    event_type TEXT NOT NULL DEFAULT 'third_strike_reached',
    strike_count_at_event INTEGER NOT NULL DEFAULT 3,
    triggered_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    resolved_at TEXT,
    resolution_reason TEXT DEFAULT '',
    reactivated_at TEXT,
    reactivation_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (student_id, violation_code, semester_id, event_type)
);
"""

ALL_TABLES = (
    USERS_TABLE,
    STUDENTS_TABLE,
    ACADEMIC_TERMS_TABLE,
    VIOLATIONS_TABLE,
    STRIKES_TABLE,
    STRIKE_EVENTS_TABLE,
)
