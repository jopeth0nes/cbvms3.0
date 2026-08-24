"""SQLite operations for CBVMS."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from core.discipline import (
    ADMIN_REVIEW_DAYS,
    AUTO_CONFIRMED,
    CONFIRMED,
    CONFIRMED_STATUSES,
    DISMISSED,
    PENDING_REVIEW,
    STRIKE_LIMIT,
    STUDENT_APPEAL_DAYS,
    academic_term_code,
    add_calendar_days,
    default_academic_term,
    format_db_datetime,
    is_disciplinary_code,
    normalize_violation_code,
    parse_db_datetime,
    utc_now,
    violation_display_name,
)
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    event_key TEXT
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
    ai_analyzed_at TEXT DEFAULT '',
    decided_at TEXT,
    decided_by TEXT DEFAULT ''
);
"""

WORKFLOW_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_violations_review_deadline "
    "ON violations(status, review_deadline)",
    "CREATE INDEX IF NOT EXISTS idx_violations_student_status "
    "ON violations(student_id, status, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_violations_term_category "
    "ON violations(semester_id, student_id, violation_code)",
    "CREATE INDEX IF NOT EXISTS idx_strikes_student_term_category "
    "ON strikes(student_id, semester_id, violation_code, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_appeals_status ON appeals(status, submitted_at)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_student_unread "
    "ON student_notifications(student_id, is_read, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_event_key "
    "ON student_notifications(event_key) WHERE event_key IS NOT NULL AND event_key <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_current_academic_term "
    "ON academic_terms(is_current) WHERE is_current = 1",
)


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
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def initialize(self, *, process_deadlines: bool = True) -> None:
        with self.connect() as conn:
            for ddl in ALL_TABLES:
                conn.execute(ddl)
            conn.execute(SYSTEM_REPORTS_TABLE)
            conn.execute(STUDENT_ACCOUNTS_TABLE)
            conn.execute(STUDENT_NOTIFICATIONS_TABLE)
            conn.execute(EVIDENCE_FILES_TABLE)
            conn.execute(DECISION_HISTORY_TABLE)
            conn.execute(APPEALS_TABLE)

            # Idempotent migrations for existing cbvms.db installations.
            appeal_cols = {r[1] for r in conn.execute("PRAGMA table_info(appeals)").fetchall()}
            for col, default in (
                ("ai_recommendation", "''"),
                ("ai_confidence", "''"),
                ("ai_analysis", "''"),
                ("ai_analyzed_at", "''"),
                ("decided_by", "''"),
            ):
                if col not in appeal_cols:
                    conn.execute(f"ALTER TABLE appeals ADD COLUMN {col} TEXT DEFAULT {default}")
            if "decided_at" not in appeal_cols:
                conn.execute("ALTER TABLE appeals ADD COLUMN decided_at TEXT")

            notification_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(student_notifications)").fetchall()
            }
            if "event_key" not in notification_cols:
                conn.execute("ALTER TABLE student_notifications ADD COLUMN event_key TEXT")

            violation_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(violations)").fetchall()
            }
            violation_migrations = {
                "violation_code": "TEXT DEFAULT 'unknown_violation'",
                "review_deadline": "TEXT",
                "confirmed_at": "TEXT",
                "appeal_deadline": "TEXT",
                "appeal_window_closed_at": "TEXT",
                "review_decided_at": "TEXT",
                "reviewed_by": "TEXT DEFAULT ''",
                "dismissal_reason": "TEXT DEFAULT ''",
                "semester_id": "INTEGER",
            }
            for col, declaration in violation_migrations.items():
                if col not in violation_cols:
                    conn.execute(f"ALTER TABLE violations ADD COLUMN {col} {declaration}")

            strike_event_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(strike_events)").fetchall()
            }
            if "reactivated_at" not in strike_event_cols:
                conn.execute("ALTER TABLE strike_events ADD COLUMN reactivated_at TEXT")
            if "reactivation_count" not in strike_event_cols:
                conn.execute(
                    "ALTER TABLE strike_events ADD COLUMN "
                    "reactivation_count INTEGER NOT NULL DEFAULT 0"
                )

            cols = {row[1] for row in conn.execute("PRAGMA table_info(students)").fetchall()}
            if "gender" not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN gender TEXT DEFAULT 'Unknown'")
            if "year_level" in cols and "year_and_section" not in cols:
                conn.execute("ALTER TABLE students RENAME COLUMN year_level TO year_and_section")
            if "email" not in cols:
                conn.execute("ALTER TABLE students ADD COLUMN email TEXT DEFAULT ''")

            self._ensure_current_academic_term_conn(conn)
            legacy_term_id = self._ensure_legacy_term_conn(conn)
            conn.execute(
                "UPDATE violations SET semester_id = ? WHERE semester_id IS NULL",
                (legacy_term_id,),
            )

            # Backfill only identity/timestamps. Pre-feature `unreviewed` rows deliberately
            # remain legacy records with no automatic deadline: retroactively confirming the
            # user's old camera history would create an unexpected strike/notification flood.
            rows = conn.execute(
                "SELECT id, violation_type, violation_code FROM violations"
            ).fetchall()
            for row in rows:
                source = row["violation_code"]
                if not source or source == "unknown_violation":
                    source = row["violation_type"]
                stable_code = normalize_violation_code(source)
                if stable_code != row["violation_code"]:
                    conn.execute(
                        "UPDATE violations SET violation_code = ? WHERE id = ?",
                        (stable_code, row["id"]),
                    )

            # Old eager notices sometimes promised a seven-day appeal period.  Keep
            # every historical notification row, but repeat-safely neutralize wording
            # that conflicts with the new five-day, strike-backed workflow.  Notices
            # linked to legacy unreviewed rows remain hidden by delivery queries.
            conn.execute(
                """UPDATE student_notifications
                   SET title = 'Historical Violation Record',
                       message = ('A historical violation record is available in your '
                                  || 'portal. Migration did not create a retroactive '
                                  || 'strike or a new appeal window.')
                   WHERE violation_id IN (
                       SELECT id FROM violations
                       WHERE status IN ('unreviewed', 'reviewed')
                   )
                     AND lower(message) LIKE '%7 day%'"""
            )

            conn.execute(
                """
                UPDATE violations
                SET review_deadline = datetime(timestamp, '+' || ? || ' days')
                WHERE status = ? AND review_deadline IS NULL
                """,
                (ADMIN_REVIEW_DAYS, PENDING_REVIEW),
            )
            confirmed_placeholders = ",".join("?" for _ in CONFIRMED_STATUSES)
            conn.execute(
                f"""
                UPDATE violations
                SET confirmed_at = COALESCE(confirmed_at, timestamp),
                    appeal_deadline = COALESCE(
                        appeal_deadline,
                        datetime(COALESCE(confirmed_at, timestamp), '+' || ? || ' days')
                    )
                WHERE status IN ({confirmed_placeholders})
                """,
                (STUDENT_APPEAL_DAYS, *CONFIRMED_STATUSES),
            )

            # Repair multiple current rows before installing the partial unique index.
            current_rows = conn.execute(
                "SELECT id FROM academic_terms WHERE is_current = 1 ORDER BY id"
            ).fetchall()
            for extra in current_rows[1:]:
                conn.execute("UPDATE academic_terms SET is_current = 0 WHERE id = ?", (extra["id"],))
            for ddl in WORKFLOW_INDEXES:
                conn.execute(ddl)
            conn.commit()
        self._seed_default_admin()
        # Normal startup processing makes persisted deadlines reliable even after the
        # app was closed.  Migration/diagnostic tools may explicitly suppress this
        # workflow mutation while still applying the repeat-safe schema migration.
        if process_deadlines:
            self.process_expired_deadlines()

    @staticmethod
    def _ensure_legacy_term_conn(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            """SELECT id FROM academic_terms
               WHERE semester_code = 'legacy' AND school_year = 'Unassigned'"""
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = conn.execute(
            """INSERT INTO academic_terms
               (semester_code, semester_name, school_year, is_current)
               VALUES ('legacy', 'Legacy / Unassigned', 'Unassigned', 0)"""
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _ensure_current_academic_term_conn(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT id FROM academic_terms WHERE is_current = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if row is not None:
            return int(row["id"])
        term = default_academic_term()
        existing = conn.execute(
            """SELECT id FROM academic_terms
               WHERE semester_code = ? AND school_year = ?""",
            (term["semester_code"], term["school_year"]),
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE academic_terms SET is_current = 1, updated_at = datetime('now') WHERE id = ?",
                (existing["id"],),
            )
            return int(existing["id"])
        cursor = conn.execute(
            """INSERT INTO academic_terms
               (semester_code, semester_name, school_year, is_current)
               VALUES (?, ?, ?, 1)""",
            (term["semester_code"], term["semester_name"], term["school_year"]),
        )
        return int(cursor.lastrowid)

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

    # ------------------------------------------------------------------
    # Academic-term configuration
    # ------------------------------------------------------------------

    def get_current_academic_term(self) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT id, semester_code, semester_name, school_year,
                          is_current, created_at, updated_at
                   FROM academic_terms WHERE is_current = 1 LIMIT 1"""
            ).fetchone()
            if row is None:
                conn.execute("BEGIN IMMEDIATE")
                term_id = self._ensure_current_academic_term_conn(conn)
                row = conn.execute(
                    "SELECT * FROM academic_terms WHERE id = ?", (term_id,)
                ).fetchone()
                conn.commit()
        return dict(row) if row is not None else {}

    # Readable alias used by portal code and integrations.
    get_current_semester = get_current_academic_term

    def set_current_academic_term(self, semester_name: str, school_year: str) -> dict | None:
        """Switch the logical current semester without deleting any history."""

        name = (semester_name or "").strip()
        year = (school_year or "").strip()
        if not name or not year:
            return None
        code = academic_term_code(name)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE academic_terms SET is_current = 0, updated_at = datetime('now') "
                "WHERE is_current = 1"
            )
            row = conn.execute(
                """SELECT id FROM academic_terms
                   WHERE semester_code = ? AND school_year = ?""",
                (code, year),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """INSERT INTO academic_terms
                       (semester_code, semester_name, school_year, is_current)
                       VALUES (?, ?, ?, 1)""",
                    (code, name, year),
                )
                term_id = int(cursor.lastrowid)
            else:
                term_id = int(row["id"])
                conn.execute(
                    """UPDATE academic_terms
                       SET semester_name = ?, is_current = 1, updated_at = datetime('now')
                       WHERE id = ?""",
                    (name, term_id),
                )
            result = conn.execute(
                "SELECT * FROM academic_terms WHERE id = ?", (term_id,)
            ).fetchone()
            conn.commit()
        return dict(result) if result is not None else None

    set_current_semester = set_current_academic_term

    def get_academic_terms(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM academic_terms
                   ORDER BY is_current DESC, id DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

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
        status: str = PENDING_REVIEW,
        violation_code: str | None = None,
        detected_at: datetime | str | None = None,
        semester_id: int | None = None,
    ) -> int:
        """Persist a camera/manual detection in administrative review.

        ``violation_type`` remains the evidence/display text. ``violation_code`` is
        the stable category identity and never includes classifier confidence.
        Legacy callers passing ``unreviewed`` are normalized to ``pending_review``.
        """

        safe_student_id = (student_id or "").strip() or "unknown"
        safe_student_name = (student_name or "").strip() or "Unknown"
        safe_violation_type = (violation_type or "").strip() or "unknown_violation"
        safe_code = normalize_violation_code(violation_code or safe_violation_type)
        requested_status = (status or PENDING_REVIEW).strip().lower()
        detected_dt = parse_db_datetime(detected_at) if detected_at is not None else utc_now()
        if detected_dt is None:
            raise ValueError("detected_at must be a valid datetime")
        detected_text = format_db_datetime(detected_dt)
        review_deadline = format_db_datetime(add_calendar_days(detected_dt, ADMIN_REVIEW_DAYS))

        with self.connect() as conn:
            # Serialize term selection with semester switches so every detection
            # permanently captures exactly one authoritative current term.
            conn.execute("BEGIN IMMEDIATE")
            if semester_id is None:
                term_row = conn.execute(
                    "SELECT id FROM academic_terms WHERE is_current = 1 LIMIT 1"
                ).fetchone()
                if term_row is None:
                    semester_id = self._ensure_current_academic_term_conn(conn)
                else:
                    semester_id = int(term_row["id"])
            else:
                term_row = conn.execute(
                    "SELECT id FROM academic_terms WHERE id = ?", (semester_id,)
                ).fetchone()
                if term_row is None:
                    raise ValueError(f"Unknown academic term id: {semester_id}")
            cursor = conn.execute(
                """
                INSERT INTO violations
                    (student_id, student_name, violation_type, timestamp, snapshot,
                     status, violation_code, review_deadline, semester_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_student_id,
                    safe_student_name,
                    safe_violation_type,
                    detected_text,
                    snapshot_jpeg,
                    PENDING_REVIEW,
                    safe_code,
                    review_deadline,
                    semester_id,
                ),
            )
            violation_id = int(cursor.lastrowid)
            conn.commit()

        # Preserve callers that historically requested an already-reviewed state,
        # while ensuring every side effect still flows through one validated method.
        if requested_status in CONFIRMED_STATUSES:
            # A caller cannot logically auto-confirm at the detection instant (the
            # review deadline has not elapsed), so legacy pre-confirmed requests are
            # treated as an explicit early confirmation.
            self.confirm_violation(
                violation_id,
                decided_by="legacy_caller",
                confirmed_at=detected_dt,
                auto=False,
            )
        elif requested_status == DISMISSED:
            self.dismiss_violation(
                violation_id,
                decided_by="legacy_caller",
                reason="Created as dismissed",
                decided_at=detected_dt,
            )
        return violation_id

    record_detected_violation = log_violation

    # ------------------------------------------------------------------
    # Administrative review, deadline processing, and strike ledger
    # ------------------------------------------------------------------

    @staticmethod
    def _active_strike_count_conn(
        conn: sqlite3.Connection,
        student_id: str,
        violation_code: str,
        semester_id: int,
    ) -> int:
        row = conn.execute(
            """SELECT COUNT(*) AS count
               FROM strikes
               WHERE student_id = ? AND violation_code = ?
                 AND semester_id = ? AND is_active = 1""",
            (student_id, violation_code, semester_id),
        ).fetchone()
        return int(row["count"] if row else 0)

    @staticmethod
    def _student_exists_conn(conn: sqlite3.Connection, student_id: str) -> bool:
        if not student_id or student_id == "unknown":
            return False
        row = conn.execute(
            "SELECT id FROM students WHERE student_id = ?", (student_id,)
        ).fetchone()
        return row is not None

    @staticmethod
    def _insert_event_notification_conn(
        conn: sqlite3.Connection,
        *,
        student_id: str,
        title: str,
        message: str,
        event_key: str,
        created_at: str,
        violation_id: int | None = None,
        reuse_legacy_violation_notification: bool = False,
    ) -> int | None:
        existing = conn.execute(
            "SELECT id FROM student_notifications WHERE event_key = ?", (event_key,)
        ).fetchone()
        if existing is not None:
            return int(existing["id"])

        if reuse_legacy_violation_notification and violation_id is not None:
            legacy = conn.execute(
                """SELECT id FROM student_notifications
                   WHERE violation_id = ? AND (event_key IS NULL OR event_key = '')
                   ORDER BY id LIMIT 1""",
                (violation_id,),
            ).fetchone()
            if legacy is not None:
                notification_id = int(legacy["id"])
                conn.execute(
                    """UPDATE student_notifications
                       SET student_id = ?, title = ?, message = ?, event_key = ?,
                           is_read = 0, created_at = ?
                       WHERE id = ?""",
                    (
                        student_id,
                        title,
                        message,
                        event_key,
                        created_at,
                        notification_id,
                    ),
                )
                # Some early development databases contain duplicate eager notices.
                conn.execute(
                    """DELETE FROM student_notifications
                       WHERE violation_id = ? AND id <> ?
                         AND (event_key IS NULL OR event_key = '')""",
                    (violation_id, notification_id),
                )
                return notification_id

        cursor = conn.execute(
            """INSERT OR IGNORE INTO student_notifications
               (student_id, title, message, violation_id, is_read, created_at, event_key)
               VALUES (?, ?, ?, ?, 0, ?, ?)""",
            (student_id, title, message, violation_id, created_at, event_key),
        )
        if cursor.rowcount:
            return int(cursor.lastrowid)
        row = conn.execute(
            "SELECT id FROM student_notifications WHERE event_key = ?", (event_key,)
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def _sync_third_strike_event_conn(
        self,
        conn: sqlite3.Connection,
        *,
        student_id: str,
        violation_code: str,
        semester_id: int,
        changed_at: str,
        resolution_reason: str = "strike_count_below_threshold",
    ) -> tuple[sqlite3.Row | None, bool]:
        count = self._active_strike_count_conn(
            conn, student_id, violation_code, semester_id
        )
        event = conn.execute(
            """SELECT * FROM strike_events
               WHERE student_id = ? AND violation_code = ? AND semester_id = ?
                 AND event_type = 'third_strike_reached'""",
            (student_id, violation_code, semester_id),
        ).fetchone()
        created = False
        if count >= STRIKE_LIMIT:
            if event is None:
                cursor = conn.execute(
                    """INSERT INTO strike_events
                       (student_id, violation_code, semester_id, event_type,
                        strike_count_at_event, triggered_at, is_active)
                       VALUES (?, ?, ?, 'third_strike_reached', ?, ?, 1)""",
                    (student_id, violation_code, semester_id, STRIKE_LIMIT, changed_at),
                )
                event = conn.execute(
                    "SELECT * FROM strike_events WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                created = True
            elif not int(event["is_active"]):
                conn.execute(
                    """UPDATE strike_events
                       SET is_active = 1, reactivated_at = ?,
                           reactivation_count = COALESCE(reactivation_count, 0) + 1
                       WHERE id = ?""",
                    (changed_at, event["id"]),
                )
                event = conn.execute(
                    "SELECT * FROM strike_events WHERE id = ?", (event["id"],)
                ).fetchone()
        elif event is not None and int(event["is_active"]):
            conn.execute(
                """UPDATE strike_events
                   SET is_active = 0, resolved_at = ?, resolution_reason = ?
                   WHERE id = ?""",
                (changed_at, resolution_reason, event["id"]),
            )
            event = conn.execute(
                "SELECT * FROM strike_events WHERE id = ?", (event["id"],)
            ).fetchone()
        return event, created

    def _ensure_confirmation_side_effects_conn(
        self,
        conn: sqlite3.Connection,
        violation: sqlite3.Row,
    ) -> None:
        status = (violation["status"] or "").lower()
        if status not in (CONFIRMED, AUTO_CONFIRMED):
            return
        student_id = (violation["student_id"] or "").strip()
        code = normalize_violation_code(violation["violation_code"])
        semester_id = violation["semester_id"]
        confirmed_at = violation["confirmed_at"] or format_db_datetime(utc_now())
        if (
            semester_id is None
            or not is_disciplinary_code(code)
            or not self._student_exists_conn(conn, student_id)
        ):
            return

        conn.execute(
            """INSERT OR IGNORE INTO strikes
               (violation_id, student_id, violation_code, semester_id, awarded_at, is_active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (violation["id"], student_id, code, semester_id, confirmed_at),
        )

        label = violation_display_name(code, violation["violation_type"])
        appeal_deadline = violation["appeal_deadline"] or ""
        self._insert_event_notification_conn(
            conn,
            student_id=student_id,
            title=f"Violation Confirmed: {label}",
            message=(
                f"Your {label} violation was confirmed. One strike is active. "
                f"You may submit an appeal until {appeal_deadline} UTC."
            ),
            violation_id=int(violation["id"]),
            event_key=f"violation:{violation['id']}:confirmed",
            created_at=confirmed_at,
            reuse_legacy_violation_notification=True,
        )

        event, created = self._sync_third_strike_event_conn(
            conn,
            student_id=student_id,
            violation_code=code,
            semester_id=int(semester_id),
            changed_at=confirmed_at,
        )
        if event is not None and created:
            term = conn.execute(
                "SELECT semester_name, school_year FROM academic_terms WHERE id = ?",
                (semester_id,),
            ).fetchone()
            term_label = (
                f"{term['semester_name']} {term['school_year']}" if term else "current semester"
            )
            self._insert_event_notification_conn(
                conn,
                student_id=student_id,
                title=f"Third Strike Reached: {label}",
                message=(
                    f"You have reached {STRIKE_LIMIT} active {label} strikes for "
                    f"{term_label}. Action is required."
                ),
                violation_id=int(violation["id"]),
                event_key=f"strike_event:{event['id']}:reached",
                created_at=confirmed_at,
            )

    def confirm_violation(
        self,
        violation_id: int,
        *,
        decided_by: str = "admin",
        confirmed_at: datetime | str | None = None,
        auto: bool = False,
    ) -> bool:
        """Confirm a pending violation and atomically deliver its strike/notice."""

        now_dt = parse_db_datetime(confirmed_at) if confirmed_at is not None else utc_now()
        if now_dt is None:
            return False
        now_text = format_db_datetime(now_dt)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            current = (row["status"] or "").lower()
            if current == "reviewed":
                # Legacy reviewed records remain historical and do not gain retroactive strikes.
                conn.commit()
                return True
            if current == DISMISSED:
                conn.rollback()
                return False
            if current in (CONFIRMED, AUTO_CONFIRMED):
                self._ensure_confirmation_side_effects_conn(conn, row)
                conn.commit()
                return True
            # Pre-feature ``unreviewed`` rows are immutable legacy history.  Only
            # records created by the new workflow may enter a strike-bearing state.
            if current != PENDING_REVIEW:
                conn.rollback()
                return False
            deadline = parse_db_datetime(row["review_deadline"])
            if auto:
                if deadline is None or now_dt < deadline:
                    conn.rollback()
                    return False
            elif deadline is not None and now_dt >= deadline:
                # A late manual click cannot bypass the automatic transition that
                # logically occurred when the review window expired.
                auto = True
                now_dt = deadline
                now_text = format_db_datetime(now_dt)

            code = normalize_violation_code(row["violation_code"] or row["violation_type"])
            eligible = (
                is_disciplinary_code(code)
                and self._student_exists_conn(conn, (row["student_id"] or "").strip())
            )
            appeal_deadline = (
                format_db_datetime(add_calendar_days(now_dt, STUDENT_APPEAL_DAYS))
                if eligible
                else None
            )
            new_status = AUTO_CONFIRMED if auto else CONFIRMED
            conn.execute(
                """UPDATE violations
                   SET status = ?, violation_code = ?, confirmed_at = ?,
                       appeal_deadline = ?, appeal_window_closed_at = NULL,
                       review_decided_at = ?, reviewed_by = ?, dismissal_reason = ''
                   WHERE id = ?""",
                (
                    new_status,
                    code,
                    now_text,
                    appeal_deadline,
                    now_text,
                    (decided_by or ("system" if auto else "admin")).strip(),
                    violation_id,
                ),
            )
            confirmed = conn.execute(
                "SELECT * FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()
            self._ensure_confirmation_side_effects_conn(conn, confirmed)
            conn.commit()
        return True

    def dismiss_violation(
        self,
        violation_id: int,
        *,
        decided_by: str = "admin",
        reason: str = "",
        decided_at: datetime | str | None = None,
    ) -> bool:
        """Dismiss a pending false detection while preserving its audit record."""

        now_dt = parse_db_datetime(decided_at) if decided_at is not None else utc_now()
        if now_dt is None:
            return False
        now_text = format_db_datetime(now_dt)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM violations WHERE id = ?", (violation_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            status = (row["status"] or "").lower()
            if status == DISMISSED:
                conn.commit()
                return True
            # Do not route pre-feature ``unreviewed`` history through the new
            # decision workflow; doing so could create retroactive student effects.
            if status != PENDING_REVIEW:
                conn.rollback()
                return False
            deadline = parse_db_datetime(row["review_deadline"])
            if deadline is not None and now_dt >= deadline:
                conn.rollback()
                # The review window already ended; establish the persisted automatic
                # outcome at the deadline and reject this late admin transition.
                self.confirm_violation(
                    violation_id,
                    decided_by="system:auto_review",
                    confirmed_at=deadline,
                    auto=True,
                )
                return False
            conn.execute(
                """UPDATE violations
                   SET status = ?, review_decided_at = ?, reviewed_by = ?,
                       dismissal_reason = ?, confirmed_at = NULL,
                       appeal_deadline = NULL, appeal_window_closed_at = NULL
                   WHERE id = ?""",
                (
                    DISMISSED,
                    now_text,
                    (decided_by or "admin").strip(),
                    (reason or "").strip(),
                    violation_id,
                ),
            )
            # Defensive repair for databases previously modified by raw SQL.
            strike = conn.execute(
                "SELECT * FROM strikes WHERE violation_id = ? AND is_active = 1",
                (violation_id,),
            ).fetchone()
            if strike is not None:
                conn.execute(
                    """UPDATE strikes SET is_active = 0, deactivated_at = ?,
                              deactivation_reason = 'violation_dismissed'
                       WHERE id = ?""",
                    (now_text, strike["id"]),
                )
                self._sync_third_strike_event_conn(
                    conn,
                    student_id=strike["student_id"],
                    violation_code=strike["violation_code"],
                    semester_id=int(strike["semester_id"]),
                    changed_at=now_text,
                    resolution_reason="violation_dismissed",
                )
            conn.commit()
        return True

    def process_expired_deadlines(
        self, *, now: datetime | str | None = None
    ) -> dict[str, int]:
        """Idempotently auto-confirm reviews and close unused appeal windows."""

        now_dt = parse_db_datetime(now) if now is not None else utc_now()
        if now_dt is None:
            raise ValueError("now must be a valid datetime")
        now_text = format_db_datetime(now_dt)
        with self.connect() as conn:
            pending_rows = [
                (int(row["id"]), row["review_deadline"])
                for row in conn.execute(
                    """SELECT id, review_deadline FROM violations
                       WHERE status = ? AND review_deadline IS NOT NULL
                         AND review_deadline <= ?
                       ORDER BY review_deadline, id""",
                    (PENDING_REVIEW, now_text),
                ).fetchall()
            ]

        auto_confirmed = 0
        for violation_id, review_deadline in pending_rows:
            # Auto-confirmation logically occurs at the persisted deadline even if the
            # desktop app was closed and only processes it on a later restart.
            if self.confirm_violation(
                violation_id,
                decided_by="system:auto_review",
                confirmed_at=review_deadline,
                auto=True,
            ):
                auto_confirmed += 1

        placeholders = ",".join("?" for _ in CONFIRMED_STATUSES)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""UPDATE violations
                    SET appeal_window_closed_at = ?
                    WHERE status IN ({placeholders})
                      AND appeal_deadline IS NOT NULL
                      AND appeal_deadline < ?
                      AND appeal_window_closed_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM appeals a WHERE a.violation_id = violations.id
                      )""",
                (now_text, *CONFIRMED_STATUSES, now_text),
            )
            appeal_windows_expired = int(cursor.rowcount)
            conn.commit()
        return {
            "auto_confirmed": auto_confirmed,
            "appeal_windows_expired": appeal_windows_expired,
        }

    def delete_violation(self, violation_id: int) -> bool:
        """Delete only a pre-workflow legacy row.

        Confirmed/dismissed/pending-review records are an audit ledger and must be
        preserved; callers should use the validated status transitions instead.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                """DELETE FROM violations
                   WHERE id = ? AND status IN ('unreviewed', 'reviewed')""",
                (violation_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_violations(self, violation_ids: list[int]) -> int:
        if not violation_ids:
            return 0
        placeholders = ",".join("?" for _ in violation_ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""DELETE FROM violations
                    WHERE id IN ({placeholders})
                      AND status IN ('unreviewed', 'reviewed')""",
                violation_ids,
            )
            conn.commit()
            return cursor.rowcount

    def delete_all_violations(self, where: str = "", params: list | None = None) -> int:
        # The WHERE clause uses the alias "v" (built by violation_log._filters_to_where for
        # SELECT queries). Wrap in a subquery so the alias is valid for DELETE too.
        legacy_clause = "v.status IN ('unreviewed', 'reviewed')"
        if where:
            sql = (
                "DELETE FROM violations WHERE id IN "
                f"(SELECT v.id FROM violations v {where} AND {legacy_clause})"
            )
        else:
            sql = "DELETE FROM violations WHERE status IN ('unreviewed', 'reviewed')"
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

    def get_visible_violations_for_student(
        self,
        student_id: str,
        *,
        now: datetime | str | None = None,
    ) -> list[dict]:
        """Confirmed historical records enriched for the authenticated student UI."""

        now_dt = parse_db_datetime(now) if now is not None else utc_now()
        if now_dt is None:
            raise ValueError("now must be a valid datetime")
        self.process_expired_deadlines(now=now_dt)
        placeholders = ",".join("?" for _ in CONFIRMED_STATUSES)
        sid = (student_id or "").strip()
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT v.*,
                           t.semester_code, t.semester_name, t.school_year,
                           st.id AS strike_id, st.is_active AS strike_active,
                           st.deactivated_at AS strike_deactivated_at,
                           st.deactivation_reason AS strike_removal_reason,
                           a.id AS appeal_id, a.status AS appeal_status,
                           a.submitted_at AS appeal_submitted_at,
                           a.admin_notes AS appeal_admin_notes
                    FROM violations v
                    LEFT JOIN academic_terms t ON t.id = v.semester_id
                    LEFT JOIN strikes st ON st.violation_id = v.id
                    LEFT JOIN appeals a ON a.violation_id = v.id
                    WHERE v.student_id = ? AND v.status IN ({placeholders})
                    ORDER BY datetime(v.timestamp) DESC, v.id DESC""",
                (sid, *CONFIRMED_STATUSES),
            ).fetchall()

        result: list[dict] = []
        for raw in rows:
            item = dict(raw)
            eligibility = self.get_appeal_eligibility(
                int(item["id"]), sid, now=now_dt
            )
            item["can_appeal"] = eligibility["eligible"]
            item["appeal_eligibility_reason"] = eligibility["reason"]
            item["appeal_status"] = item.get("appeal_status") or "not_submitted"
            item["violation_label"] = violation_display_name(
                item.get("violation_code"), item.get("violation_type")
            )
            deadline = parse_db_datetime(item.get("appeal_deadline"))
            item["appeal_remaining_seconds"] = (
                max(0, int((deadline - now_dt).total_seconds())) if deadline else 0
            )
            if item.get("appeal_id"):
                item["appeal_window_status"] = "submitted"
            elif eligibility["eligible"]:
                item["appeal_window_status"] = "eligible"
            else:
                item["appeal_window_status"] = "expired"
            result.append(item)
        return result

    # Backward-friendly readable alias.
    get_student_visible_violations = get_visible_violations_for_student

    def get_appeal_eligibility(
        self,
        violation_id: int,
        student_id: str,
        *,
        now: datetime | str | None = None,
    ) -> dict:
        now_dt = parse_db_datetime(now) if now is not None else utc_now()
        if now_dt is None:
            return {"eligible": False, "reason": "invalid_time", "deadline": None}
        sid = (student_id or "").strip()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT v.*, a.id AS appeal_id, st.is_active AS strike_active
                   FROM violations v
                   LEFT JOIN appeals a ON a.violation_id = v.id
                   LEFT JOIN strikes st ON st.violation_id = v.id
                   WHERE v.id = ?""",
                (violation_id,),
            ).fetchone()
        if row is None:
            return {"eligible": False, "reason": "not_found", "deadline": None}
        if (row["student_id"] or "").strip() != sid:
            return {"eligible": False, "reason": "not_owner", "deadline": row["appeal_deadline"]}
        if (row["status"] or "").lower() not in (CONFIRMED, AUTO_CONFIRMED):
            return {
                "eligible": False,
                "reason": "not_confirmed",
                "deadline": row["appeal_deadline"],
            }
        if row["appeal_id"] is not None:
            return {
                "eligible": False,
                "reason": "already_submitted",
                "deadline": row["appeal_deadline"],
            }
        if not int(row["strike_active"] or 0):
            return {
                "eligible": False,
                "reason": "no_active_strike",
                "deadline": row["appeal_deadline"],
            }
        deadline = parse_db_datetime(row["appeal_deadline"])
        if deadline is None:
            return {"eligible": False, "reason": "no_deadline", "deadline": None}
        if now_dt > deadline:
            return {
                "eligible": False,
                "reason": "deadline_expired",
                "deadline": format_db_datetime(deadline),
            }
        return {
            "eligible": True,
            "reason": "eligible",
            "deadline": format_db_datetime(deadline),
        }

    def get_strike_count(
        self,
        student_id: str,
        violation_code: str,
        *,
        semester_id: int | None = None,
    ) -> int:
        sid = (student_id or "").strip()
        code = normalize_violation_code(violation_code)
        if semester_id is None:
            term = self.get_current_academic_term()
            semester_id = int(term["id"]) if term else -1
        with self.connect() as conn:
            return self._active_strike_count_conn(conn, sid, code, int(semester_id))

    def get_strike_summary(
        self,
        student_id: str,
        *,
        semester_id: int | None = None,
    ) -> list[dict]:
        """Current active counts by stable violation category."""

        sid = (student_id or "").strip()
        if semester_id is None:
            term = self.get_current_academic_term()
            if not term:
                return []
            semester_id = int(term["id"])
        with self.connect() as conn:
            term_row = conn.execute(
                "SELECT * FROM academic_terms WHERE id = ?", (semester_id,)
            ).fetchone()
            # Derive student-facing categories from the strike ledger, not raw
            # detections.  This prevents pending/dismissed categories from leaking
            # into the portal before confirmation while retaining approved history.
            code_rows = conn.execute(
                """SELECT DISTINCT violation_code FROM strikes
                   WHERE student_id = ? AND semester_id = ?""",
                (sid, semester_id),
            ).fetchall()
            codes = {normalize_violation_code(row["violation_code"]) for row in code_rows}
            # Wrong uniform is the currently deployed detector and should display 0/3.
            codes.add("wrong_uniform")
            summary: list[dict] = []
            for code in sorted(codes):
                if not is_disciplinary_code(code):
                    continue
                count = self._active_strike_count_conn(conn, sid, code, int(semester_id))
                event = conn.execute(
                    """SELECT id, is_active FROM strike_events
                       WHERE student_id = ? AND violation_code = ? AND semester_id = ?
                         AND event_type = 'third_strike_reached'""",
                    (sid, code, semester_id),
                ).fetchone()
                summary.append({
                    "violation_code": code,
                    "violation_label": violation_display_name(code),
                    "active_count": count,
                    "strike_limit": STRIKE_LIMIT,
                    "action_required": count >= STRIKE_LIMIT and bool(
                        event is None or int(event["is_active"])
                    ),
                    "threshold_event_id": int(event["id"]) if event is not None else None,
                    "semester_id": int(semester_id),
                    "semester_name": term_row["semester_name"] if term_row else "—",
                    "school_year": term_row["school_year"] if term_row else "—",
                })
        return summary

    get_current_strike_summary = get_strike_summary

    def get_pending_review_violations(
        self, *, now: datetime | str | None = None
    ) -> list[dict]:
        now_dt = parse_db_datetime(now) if now is not None else utc_now()
        if now_dt is None:
            raise ValueError("now must be a valid datetime")
        self.process_expired_deadlines(now=now_dt)
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT v.*, t.semester_name, t.school_year,
                          (SELECT COUNT(*) FROM strikes st
                           WHERE st.student_id = v.student_id
                             AND st.violation_code = v.violation_code
                             AND st.semester_id = v.semester_id
                             AND st.is_active = 1) AS current_strike_count
                   FROM violations v
                   LEFT JOIN academic_terms t ON t.id = v.semester_id
                   WHERE v.status = ?
                   ORDER BY review_deadline, id""",
                (PENDING_REVIEW,),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def insert_notification(
        self,
        student_id: str,
        title: str,
        message: str,
        violation_id: int | None = None,
        *,
        event_key: str | None = None,
        created_at: datetime | str | None = None,
    ) -> int | None:
        try:
            created_text = format_db_datetime(created_at or utc_now())
            with self.connect() as conn:
                conn.execute(STUDENT_NOTIFICATIONS_TABLE)
                if event_key:
                    notification_id = self._insert_event_notification_conn(
                        conn,
                        student_id=(student_id or "").strip(),
                        title=(title or "").strip(),
                        message=(message or "").strip(),
                        violation_id=violation_id,
                        event_key=event_key,
                        created_at=created_text,
                    )
                else:
                    cursor = conn.execute(
                        """INSERT INTO student_notifications
                           (student_id, title, message, violation_id, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        ((student_id or "").strip(), (title or "").strip(),
                         (message or "").strip(), violation_id, created_text),
                    )
                    notification_id = int(cursor.lastrowid)
                conn.commit()
                return notification_id
        except Exception as exc:
            print(f"[DB] insert_notification error: {exc}")
            return None

    def get_notifications_for_student(self, student_id: str) -> list[dict]:
        """Delivered notifications only; pending/dismissed detections stay hidden."""
        placeholders = ",".join("?" for _ in CONFIRMED_STATUSES)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT n.* FROM student_notifications n
                    LEFT JOIN violations v ON v.id = n.violation_id
                    WHERE n.student_id = ?
                      AND (n.violation_id IS NULL
                           OR v.status IN ({placeholders}))
                    ORDER BY datetime(n.created_at) DESC, n.id DESC""",
                ((student_id or "").strip(), *CONFIRMED_STATUSES),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_unread_notification_count(self, student_id: str) -> int:
        placeholders = ",".join("?" for _ in CONFIRMED_STATUSES)
        with self.connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) AS count
                    FROM student_notifications n
                    LEFT JOIN violations v ON v.id = n.violation_id
                    WHERE n.student_id = ? AND n.is_read = 0
                      AND (n.violation_id IS NULL
                           OR v.status IN ({placeholders}))""",
                ((student_id or "").strip(), *CONFIRMED_STATUSES),
            ).fetchone()
        return int(row["count"] if row else 0)

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
        """Repair side effects for confirmed records without leaking pending reviews."""
        sid = (student_id or "").strip()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id FROM violations
                   WHERE student_id = ? AND status IN (?, ?)
                   ORDER BY timestamp""",
                (sid, CONFIRMED, AUTO_CONFIRMED),
            ).fetchall()
        for row in rows:
            self.confirm_violation(int(row["id"]))

    # ------------------------------------------------------------------
    # Appeals helpers
    # ------------------------------------------------------------------

    def get_appeals_for_student(self, student_id: str) -> list[dict]:
        """All appeals for a student, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, v.violation_type, v.violation_code,
                       v.timestamp AS violation_ts, v.confirmed_at,
                       v.appeal_deadline, v.status AS violation_status,
                       st.is_active AS strike_active,
                       st.deactivation_reason AS strike_removal_reason
                FROM appeals a
                JOIN violations v ON v.id = a.violation_id
                LEFT JOIN strikes st ON st.violation_id = v.id
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

    def insert_appeal(
        self,
        violation_id: int,
        student_id: str,
        reason: str,
    ) -> int | None:
        """Submit an appeal using the backend clock after full eligibility validation."""

        sid = (student_id or "").strip()
        safe_reason = (reason or "").strip()
        # The student-facing API intentionally has no caller-provided timestamp: a
        # forged/backdated value must never bypass the persisted appeal deadline.
        submitted_dt = utc_now()
        if not sid or not safe_reason:
            return None
        submitted_text = format_db_datetime(submitted_dt)
        try:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """SELECT v.*, a.id AS existing_appeal_id,
                              st.is_active AS strike_active
                       FROM violations v
                       LEFT JOIN appeals a ON a.violation_id = v.id
                       LEFT JOIN strikes st ON st.violation_id = v.id
                       WHERE v.id = ?""",
                    (violation_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                deadline = parse_db_datetime(row["appeal_deadline"])
                valid = (
                    (row["student_id"] or "").strip() == sid
                    and (row["status"] or "").lower() in (CONFIRMED, AUTO_CONFIRMED)
                    and row["existing_appeal_id"] is None
                    and bool(int(row["strike_active"] or 0))
                    and deadline is not None
                    and submitted_dt <= deadline
                )
                if not valid:
                    conn.rollback()
                    return None
                cursor = conn.execute(
                    """
                    INSERT INTO appeals (violation_id, student_id, reason, submitted_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (violation_id, sid, safe_reason, submitted_text),
                )
                conn.commit()
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    submit_appeal = insert_appeal

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

    def update_appeal_decision(
        self,
        appeal_id: int,
        decision: str,
        admin_notes: str,
        decided_by: str = "admin",
        *,
        decided_at: datetime | str | None = None,
    ) -> bool:
        """Atomically apply the admin's final appeal decision and audit effects."""

        safe_decision = (decision or "").strip().lower()
        if safe_decision not in ("approved", "rejected"):
            return False
        decision_dt = parse_db_datetime(decided_at) if decided_at is not None else utc_now()
        if decision_dt is None:
            return False
        decision_text = format_db_datetime(decision_dt)
        notes = (admin_notes or "").strip()
        actor = (decided_by or "admin").strip()
        try:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """SELECT a.status, a.violation_id, a.student_id,
                              a.ai_recommendation, a.submitted_at,
                              v.student_name, v.violation_type, v.violation_code,
                              st.id AS strike_id, st.is_active AS strike_active,
                              st.semester_id AS strike_semester_id
                       FROM appeals a
                       LEFT JOIN violations v ON v.id = a.violation_id
                       LEFT JOIN strikes st ON st.violation_id = a.violation_id
                       WHERE a.id = ?""",
                    (appeal_id,),
                ).fetchone()
                if row is None or (row["status"] or "").lower() != "pending":
                    conn.rollback()
                    return False
                conn.execute(
                    """UPDATE appeals
                       SET status = ?, admin_notes = ?, decided_at = ?, decided_by = ?
                       WHERE id = ? AND status = 'pending'""",
                    (safe_decision, notes, decision_text, actor, appeal_id),
                )
                if safe_decision == "approved" and row["strike_id"] is not None:
                    if int(row["strike_active"] or 0):
                        conn.execute(
                            """UPDATE strikes
                               SET is_active = 0, deactivated_at = ?,
                                   deactivation_reason = 'appeal_approved'
                               WHERE id = ?""",
                            (decision_text, row["strike_id"]),
                        )
                    self._sync_third_strike_event_conn(
                        conn,
                        student_id=(row["student_id"] or "").strip(),
                        violation_code=normalize_violation_code(row["violation_code"]),
                        semester_id=int(row["strike_semester_id"]),
                        changed_at=decision_text,
                        resolution_reason="appeal_approved",
                    )
                conn.execute(
                    """INSERT INTO decision_history
                       (appeal_id, violation_id, student_id, student_name,
                        violation_type, decision, previous_status, admin_notes,
                        decided_by, ai_recommendation, decided_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                    (
                        appeal_id,
                        row["violation_id"],
                        row["student_id"],
                        row["student_name"] or "",
                        row["violation_type"] or "",
                        safe_decision,
                        notes,
                        actor,
                        row["ai_recommendation"] or "",
                        decision_text,
                    ),
                )
                label = violation_display_name(
                    row["violation_code"], row["violation_type"]
                )
                effect = (
                    "The associated strike was removed."
                    if safe_decision == "approved"
                    else "The associated strike remains active."
                )
                message = f"Your appeal for {label} was {safe_decision}. {effect}"
                if notes:
                    message += f" Admin note: {notes}"
                self._insert_event_notification_conn(
                    conn,
                    student_id=(row["student_id"] or "").strip(),
                    title=f"Appeal {safe_decision.title()}: {label}",
                    message=message,
                    violation_id=int(row["violation_id"]),
                    event_key=f"appeal:{appeal_id}:{safe_decision}",
                    created_at=decision_text,
                )
                conn.commit()
            return True
        except Exception as exc:
            print(f"[DB] update_appeal_decision error: {exc}")
            return False

    decide_appeal = update_appeal_decision

    def get_all_appeals_full(self) -> list[dict]:
        """All appeals joined with violation + student info, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT a.*,
                          v.violation_type, v.violation_code,
                          v.timestamp AS violation_ts, v.confirmed_at,
                          v.appeal_deadline, v.status AS violation_status,
                          v.snapshot AS violation_snapshot,
                          st.is_active AS strike_active,
                          st.deactivation_reason AS strike_removal_reason,
                          s.name AS student_name_full, s.course, s.year_and_section
                   FROM appeals a
                   LEFT JOIN violations v ON v.id = a.violation_id
                   LEFT JOIN strikes st ON st.violation_id = a.violation_id
                   LEFT JOIN students s ON s.student_id = a.student_id
                   ORDER BY a.submitted_at DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_violations_full(self) -> list[dict]:
        """All violations joined with student info, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT v.*, s.course, s.year_and_section,
                          t.semester_name, t.school_year,
                          st.is_active AS strike_active,
                          st.deactivation_reason AS strike_removal_reason,
                          a.status AS appeal_status
                   FROM violations v
                   LEFT JOIN students s ON s.student_id = v.student_id
                   LEFT JOIN academic_terms t ON t.id = v.semester_id
                   LEFT JOIN strikes st ON st.violation_id = v.id
                   LEFT JOIN appeals a ON a.violation_id = v.id
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
