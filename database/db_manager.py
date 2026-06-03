"""SQLite operations for CBVMS."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from database.models import ALL_TABLES

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

# Defined here (not models.py) so the student portal's report feature is self-contained.
SYSTEM_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS system_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id TEXT,
    reporter_name TEXT,
    category TEXT,
    title TEXT,
    description TEXT,
    submitted_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'open'
);
"""

STUDENT_ACCOUNTS_TABLE = """
CREATE TABLE IF NOT EXISTS student_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

STUDENT_NOTIFICATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS student_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    violation_id INTEGER,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

EVIDENCE_FILES_TABLE = """
CREATE TABLE IF NOT EXISTS evidence_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appeal_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT 'image',
    file_data BLOB NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DECISION_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appeal_id INTEGER NOT NULL,
    violation_id INTEGER,
    student_id TEXT,
    student_name TEXT,
    violation_type TEXT,
    decision TEXT NOT NULL,
    previous_status TEXT DEFAULT 'pending',
    admin_notes TEXT DEFAULT '',
    decided_by TEXT DEFAULT 'admin',
    ai_recommendation TEXT DEFAULT '',
    decided_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

APPEALS_TABLE = """
CREATE TABLE IF NOT EXISTS appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER NOT NULL UNIQUE,
    student_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'pending',
    admin_notes TEXT DEFAULT '',
    ai_recommendation TEXT DEFAULT '',
    ai_confidence TEXT DEFAULT '',
    ai_analysis TEXT DEFAULT '',
    ai_analyzed_at TEXT DEFAULT ''
);
"""


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class CBVMSDatabase:
    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            root = Path(__file__).resolve().parent.parent
            db_path = root / "data" / "cbvms.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            for ddl in ALL_TABLES:
                conn.execute(ddl)
            conn.execute(SYSTEM_REPORTS_TABLE)
            conn.execute(STUDENT_ACCOUNTS_TABLE)
            conn.execute(STUDENT_NOTIFICATIONS_TABLE)
            conn.execute(EVIDENCE_FILES_TABLE)
            conn.execute(DECISION_HISTORY_TABLE)
            conn.execute(APPEALS_TABLE)
            # Migrations for existing databases
            appeal_cols = [r[1] for r in conn.execute("PRAGMA table_info(appeals)").fetchall()]
            for col, default in (
                ("ai_recommendation", "''"),
                ("ai_confidence", "''"),
                ("ai_analysis", "''"),
                ("ai_analyzed_at", "''"),
            ):
                if col not in appeal_cols:
                    conn.execute(f"ALTER TABLE appeals ADD COLUMN {col} TEXT DEFAULT {default}")
            cols = [row[1] for row in conn.execute("PRAGMA table_info(students)").fetchall()]
            if "gender" not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN gender TEXT DEFAULT 'Unknown'")
            if "year_level" in cols and "year_and_section" not in cols:
                conn.execute("ALTER TABLE students RENAME COLUMN year_level TO year_and_section")
            if "email" not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN email TEXT DEFAULT ''")
            conn.commit()
        self._seed_default_admin()

    def _seed_default_admin(self) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (DEFAULT_ADMIN_USERNAME,),
            ).fetchone()
            if row is not None:
                return
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (DEFAULT_ADMIN_USERNAME, hash_password(DEFAULT_ADMIN_PASSWORD)),
            )
            conn.commit()

    def verify_user(self, username: str, password: str) -> bool:
        password_hash = hash_password(password)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ? AND password_hash = ?",
                (username.strip(), password_hash),
            ).fetchone()
        return row is not None

    def get_all_students(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, student_id, name, course, year_and_section, gender, email, encoding, photo, enrolled_at
                FROM students
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return list(rows)

    def get_student(self, student_pk: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, student_id, name, course, year_and_section, gender, email, encoding, photo, enrolled_at
                FROM students WHERE id = ?
                """,
                (student_pk,),
            ).fetchone()

    def student_id_exists(self, student_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM students WHERE student_id = ?",
                (student_id.strip(),),
            ).fetchone()
        return row is not None

    def insert_student(
        self,
        student_id: str,
        name: str,
        course: str,
        year_and_section: str,
        encoding: bytes,
        photo: bytes,
        gender: str = "Unknown",
        email: str = "",
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO students (student_id, name, course, year_and_section, gender, email, encoding, photo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id.strip(),
                    name.strip(),
                    course.strip(),
                    year_and_section.strip(),
                    gender.strip() or "Unknown",
                    (email or "").strip(),
                    encoding,
                    photo,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_student_encoding(self, student_pk: int, encoding: bytes, photo: bytes) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE students SET encoding = ?, photo = ? WHERE id = ?",
                (encoding, photo, student_pk),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_student(self, student_pk: int) -> bool:
        with self.connect() as conn:
            # Resolve student_id string before deleting the row
            row = conn.execute(
                "SELECT student_id FROM students WHERE id = ?", (student_pk,)
            ).fetchone()
            if row is None:
                return False
            sid = row[0]
            # Cascade: remove login account so the student can no longer log in
            conn.execute("DELETE FROM student_accounts WHERE student_id = ?", (sid,))
            cursor = conn.execute("DELETE FROM students WHERE id = ?", (student_pk,))
            conn.commit()
            return cursor.rowcount > 0

    def log_violation(
        self,
        student_id: str,
        student_name: str,
        violation_type: str,
        snapshot_jpeg: bytes | None = None,
        status: str = "unreviewed",
    ) -> int:
        """
        Persist a detected violation into the `violations` table.

        Returns the inserted row id.
        """

        safe_student_id = (student_id or "").strip() or "unknown"
        safe_student_name = (student_name or "").strip() or "Unknown"
        safe_violation_type = (violation_type or "").strip() or "unknown_violation"
        safe_status = (status or "").strip() or "unreviewed"

        with self.connect() as conn:
            conn.execute(STUDENT_NOTIFICATIONS_TABLE)
            cursor = conn.execute(
                """
                INSERT INTO violations (student_id, student_name, violation_type, snapshot, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (safe_student_id, safe_student_name, safe_violation_type, snapshot_jpeg, safe_status),
            )
            violation_id = int(cursor.lastrowid)
            title = f"Violation Detected: {safe_violation_type.replace('_', ' ').title()}"
            message = (
                f"A '{safe_violation_type.replace('_', ' ')}' violation was recorded for your account. "
                "You may submit an appeal within 7 days if you believe this is incorrect."
            )
            conn.execute(
                """
                INSERT INTO student_notifications (student_id, title, message, violation_id)
                VALUES (?, ?, ?, ?)
                """,
                (safe_student_id, title, message, violation_id),
            )
            conn.commit()
            return violation_id

    def delete_violation(self, violation_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM violations WHERE id = ?", (violation_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_violations(self, violation_ids: list[int]) -> int:
        if not violation_ids:
            return 0
        placeholders = ",".join("?" for _ in violation_ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM violations WHERE id IN ({placeholders})", violation_ids
            )
            conn.commit()
            return cursor.rowcount

    def delete_all_violations(self, where: str = "", params: list | None = None) -> int:
        # The WHERE clause uses the alias "v" (built by violation_log._filters_to_where for
        # SELECT queries). Wrap in a subquery so the alias is valid for DELETE too.
        if where:
            sql = f"DELETE FROM violations WHERE id IN (SELECT v.id FROM violations v {where})"
        else:
            sql = "DELETE FROM violations"
        with self.connect() as conn:
            cursor = conn.execute(sql, params or [])
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Student-portal helpers
    # ------------------------------------------------------------------

    def get_student_by_student_id(self, student_id: str) -> dict | None:
        """Fetch a student row by their student_id string (not PK). Dict or None."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM students WHERE student_id = ?",
                ((student_id or "").strip(),),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_violations_for_student(self, student_id: str) -> list[dict]:
        """All violations for a student, newest first (snapshot column included)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM violations WHERE student_id = ? ORDER BY timestamp DESC",
                ((student_id or "").strip(),),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_student_name(self, student_id: str, name: str) -> bool:
        """Update a student's display name. Returns True if a row was changed."""
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE students SET name = ? WHERE student_id = ?",
                ((name or "").strip(), (student_id or "").strip()),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Student account helpers (self-registration)
    # ------------------------------------------------------------------

    def username_exists(self, username: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM student_accounts WHERE username = ?",
                ((username or "").strip(),),
            ).fetchone()
        return row is not None

    def insert_student_account(self, student_id: str, username: str, password: str) -> bool:
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO student_accounts (student_id, username, password_hash)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (student_id or "").strip(),
                        (username or "").strip(),
                        hash_password(password),
                    ),
                )
                conn.commit()
            return True
        except Exception as exc:
            print(f"[DB] insert_student_account error: {exc}")
            return False

    def get_all_student_accounts(self) -> list[dict]:
        """Return all student accounts joined with student info, sorted by name."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sa.id, sa.student_id, sa.username, sa.password_hash, sa.created_at,
                       s.name, s.course, s.year_and_section, s.gender
                FROM student_accounts sa
                LEFT JOIN students s ON s.student_id = sa.student_id
                ORDER BY s.name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def reset_student_password(self, student_id: str, new_password: str) -> bool:
        """Reset a student account password. Returns True on success."""
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    "UPDATE student_accounts SET password_hash = ? WHERE student_id = ?",
                    (hash_password(new_password), (student_id or "").strip()),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[DB] reset_student_password error: {exc}")
            return False

    def upsert_student_account(self, student_id: str, username: str, password: str) -> tuple[bool, str]:
        """Create or update a student account.

        If an account already exists for this student_id (e.g. from self-registration),
        the password is updated but the existing username is preserved.
        Returns (success, actual_username_used).
        """
        sid = (student_id or "").strip()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT username FROM student_accounts WHERE student_id = ?", (sid,)
            ).fetchone()
        if row is not None:
            existing_username = row[0]
            try:
                with self.connect() as conn:
                    conn.execute(
                        "UPDATE student_accounts SET password_hash = ? WHERE student_id = ?",
                        (hash_password(password), sid),
                    )
                    conn.commit()
                return True, existing_username
            except Exception as exc:
                print(f"[DB] upsert_student_account update error: {exc}")
                return False, existing_username
        success = self.insert_student_account(sid, username, password)
        return success, username

    def verify_student_account(self, username: str, password: str) -> dict | None:
        """Return {student_id, display_name} if credentials match, else None."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT sa.student_id, s.name
                FROM student_accounts sa
                LEFT JOIN students s ON s.student_id = sa.student_id
                WHERE sa.username = ? AND sa.password_hash = ?
                """,
                ((username or "").strip(), hash_password(password)),
            ).fetchone()
        if row is None:
            return None
        return {"student_id": row[0], "display_name": row[1] or row[0]}

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    def insert_notification(self, student_id: str, title: str,
                             message: str, violation_id: int | None = None) -> int | None:
        try:
            with self.connect() as conn:
                conn.execute(STUDENT_NOTIFICATIONS_TABLE)
                cursor = conn.execute(
                    """INSERT INTO student_notifications
                       (student_id, title, message, violation_id)
                       VALUES (?, ?, ?, ?)""",
                    ((student_id or "").strip(), (title or "").strip(),
                     (message or "").strip(), violation_id),
                )
                conn.commit()
                return int(cursor.lastrowid)
        except Exception as exc:
            print(f"[DB] insert_notification error: {exc}")
            return None

    def get_notifications_for_student(self, student_id: str) -> list[dict]:
        """All notifications for a student, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM student_notifications WHERE student_id = ? ORDER BY created_at DESC",
                ((student_id or "").strip(),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_unread_notification_count(self, student_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM student_notifications WHERE student_id = ? AND is_read = 0",
                ((student_id or "").strip(),),
            ).fetchone()
        return row[0] if row else 0

    def mark_notification_read(self, notif_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE student_notifications SET is_read = 1 WHERE id = ?", (notif_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_all_notifications_read(self, student_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE student_notifications SET is_read = 1 WHERE student_id = ? AND is_read = 0",
                ((student_id or "").strip(),),
            )
            conn.commit()
            return cursor.rowcount

    def ensure_notifications_for_student(self, student_id: str) -> None:
        """Create missing notifications for violations that don't have one yet."""
        sid = (student_id or "").strip()
        with self.connect() as conn:
            conn.execute(STUDENT_NOTIFICATIONS_TABLE)
            rows = conn.execute(
                """
                SELECT v.id, v.violation_type, v.timestamp
                FROM violations v
                LEFT JOIN student_notifications n ON n.violation_id = v.id
                WHERE v.student_id = ? AND n.id IS NULL
                ORDER BY v.timestamp
                """,
                (sid,),
            ).fetchall()
            for row in rows:
                vtype = (row["violation_type"] or "unknown_violation").strip()
                title = f"Violation Detected: {vtype.replace('_', ' ').title()}"
                message = (
                    f"A '{vtype.replace('_', ' ')}' violation was recorded for your account "
                    f"on {row['timestamp']}. You may submit an appeal within 7 days."
                )
                conn.execute(
                    """
                    INSERT INTO student_notifications (student_id, title, message, violation_id, is_read)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (sid, title, message, row["id"]),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Appeals helpers
    # ------------------------------------------------------------------

    def get_appeals_for_student(self, student_id: str) -> list[dict]:
        """All appeals for a student, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, v.violation_type, v.timestamp AS violation_ts
                FROM appeals a
                JOIN violations v ON v.id = a.violation_id
                WHERE a.student_id = ?
                ORDER BY a.submitted_at DESC
                """,
                ((student_id or "").strip(),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_appeal_for_violation(self, violation_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM appeals WHERE violation_id = ?", (violation_id,)
            ).fetchone()
        return dict(row) if row else None

    def insert_appeal(self, violation_id: int, student_id: str, reason: str) -> int | None:
        """Insert an appeal. Returns new id, or None if already exists."""
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO appeals (violation_id, student_id, reason)
                    VALUES (?, ?, ?)
                    """,
                    (violation_id, (student_id or "").strip(), (reason or "").strip()),
                )
                conn.commit()
                return int(cursor.lastrowid)
        except Exception:
            return None

    def update_appeal_ai_analysis(
        self,
        appeal_id: int,
        recommendation: str,
        confidence: str,
        analysis: str,
    ) -> bool:
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE appeals
                    SET ai_recommendation = ?,
                        ai_confidence = ?,
                        ai_analysis = ?,
                        ai_analyzed_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        (recommendation or "").strip(),
                        (confidence or "").strip(),
                        (analysis or "").strip(),
                        appeal_id,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[DB] update_appeal_ai_analysis error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Evidence file helpers
    # ------------------------------------------------------------------

    def insert_evidence_file(self, appeal_id: int, student_id: str,
                              filename: str, file_type: str, file_data: bytes) -> int | None:
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO evidence_files
                       (appeal_id, student_id, filename, file_type, file_data)
                       VALUES (?, ?, ?, ?, ?)""",
                    (appeal_id, (student_id or "").strip(),
                     (filename or "").strip(), (file_type or "image").strip(), file_data),
                )
                conn.commit()
                return int(cursor.lastrowid)
        except Exception as exc:
            print(f"[DB] insert_evidence_file error: {exc}")
            return None

    def get_evidence_for_appeal(self, appeal_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence_files WHERE appeal_id = ? ORDER BY uploaded_at",
                (appeal_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_evidence_file(self, evidence_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM evidence_files WHERE id = ?", (evidence_id,)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Decision history helpers
    # ------------------------------------------------------------------

    def log_decision(self, appeal_id: int, violation_id: int, student_id: str,
                     student_name: str, violation_type: str, decision: str,
                     previous_status: str, admin_notes: str,
                     decided_by: str = "admin", ai_recommendation: str = "") -> bool:
        try:
            with self.connect() as conn:
                conn.execute(
                    """INSERT INTO decision_history
                       (appeal_id, violation_id, student_id, student_name,
                        violation_type, decision, previous_status, admin_notes,
                        decided_by, ai_recommendation)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (appeal_id, violation_id, (student_id or "").strip(),
                     (student_name or "").strip(), (violation_type or "").strip(),
                     (decision or "").strip(), (previous_status or "pending").strip(),
                     (admin_notes or "").strip(), (decided_by or "admin").strip(),
                     (ai_recommendation or "").strip()),
                )
                conn.commit()
            return True
        except Exception as exc:
            print(f"[DB] log_decision error: {exc}")
            return False

    def get_decision_history(self, limit: int = 200) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decision_history ORDER BY decided_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_decision_history_for_appeal(self, appeal_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decision_history WHERE appeal_id = ? ORDER BY decided_at DESC",
                (appeal_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_appeal_decision(self, appeal_id: int, decision: str,
                                admin_notes: str, decided_by: str = "admin") -> bool:
        """Update appeal status and log the decision."""
        try:
            with self.connect() as conn:
                row = conn.execute(
                    """SELECT a.status, a.violation_id, a.student_id, a.ai_recommendation,
                              v.student_name, v.violation_type
                       FROM appeals a
                       LEFT JOIN violations v ON v.id = a.violation_id
                       WHERE a.id = ?""",
                    (appeal_id,),
                ).fetchone()
                if row is None:
                    return False
                prev_status = row[0]
                conn.execute(
                    "UPDATE appeals SET status = ?, admin_notes = ? WHERE id = ?",
                    (decision, (admin_notes or "").strip(), appeal_id),
                )
                conn.execute(
                    """INSERT INTO decision_history
                       (appeal_id, violation_id, student_id, student_name,
                        violation_type, decision, previous_status, admin_notes,
                        decided_by, ai_recommendation)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (appeal_id, row[1], row[2], row[4] or "", row[5] or "",
                     decision, prev_status, (admin_notes or "").strip(),
                     decided_by, row[3] or ""),
                )
                conn.commit()
            return True
        except Exception as exc:
            print(f"[DB] update_appeal_decision error: {exc}")
            return False

    def get_all_appeals_full(self) -> list[dict]:
        """All appeals joined with violation + student info, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT a.*,
                          v.violation_type, v.timestamp AS violation_ts,
                          v.snapshot AS violation_snapshot,
                          s.name AS student_name_full, s.course, s.year_and_section
                   FROM appeals a
                   LEFT JOIN violations v ON v.id = a.violation_id
                   LEFT JOIN students s ON s.student_id = a.student_id
                   ORDER BY a.submitted_at DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_violations_full(self) -> list[dict]:
        """All violations joined with student info, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT v.*, s.course, s.year_and_section
                   FROM violations v
                   LEFT JOIN students s ON s.student_id = v.student_id
                   ORDER BY v.timestamp DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    def insert_system_report(
        self,
        reporter_id: str,
        reporter_name: str,
        category: str,
        title: str,
        description: str,
    ) -> bool:
        """Persist a student-submitted system report. Returns True on success."""
        try:
            with self.connect() as conn:
                conn.execute(SYSTEM_REPORTS_TABLE)  # defensive: ensure table exists
                conn.execute(
                    """
                    INSERT INTO system_reports
                        (reporter_id, reporter_name, category, title, description)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (reporter_id or "").strip(),
                        (reporter_name or "").strip(),
                        (category or "").strip(),
                        (title or "").strip(),
                        (description or "").strip(),
                    ),
                )
                conn.commit()
            return True
        except Exception as exc:
            print(f"[DB] insert_system_report error: {exc}")
            return False
