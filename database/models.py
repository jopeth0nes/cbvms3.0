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
    encoding BLOB,
    photo BLOB,
    enrolled_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    status TEXT NOT NULL DEFAULT 'unreviewed'
);
"""

ALL_TABLES = (USERS_TABLE, STUDENTS_TABLE, VIOLATIONS_TABLE)
