"""Additive student-management storage; no changes to historical discipline rules."""
from core.academics import academic_values
from core.discipline import utc_now, parse_db_datetime, format_db_datetime
from core.student_status import STUDENT_STATUSES, CONTACT_FIELDS, validate_contacts


def migrate_student_management(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS report_csv_snapshot (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
    columns = {r[1] for r in conn.execute("PRAGMA table_info(students)")}
    additions = {key: "TEXT NOT NULL DEFAULT ''" for _, key in CONTACT_FIELDS}
    additions.update(student_status="TEXT NOT NULL DEFAULT 'Enrolled'",
                     registration_pending="INTEGER NOT NULL DEFAULT 0",
                     college_department="TEXT NOT NULL DEFAULT ''",
                     report_section="TEXT NOT NULL DEFAULT ''",
                     report_year_level="TEXT NOT NULL DEFAULT ''")
    for key, ddl in additions.items():
        if key not in columns:
            conn.execute(f"ALTER TABLE students ADD COLUMN {key} {ddl}")
    conn.execute('''CREATE TABLE IF NOT EXISTS student_status_history (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL, previous_status TEXT NOT NULL,
        new_status TEXT NOT NULL, reason TEXT NOT NULL, changed_by TEXT NOT NULL,
        changed_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS student_suspensions (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL, reason TEXT NOT NULL,
        starts_at TEXT NOT NULL, ends_at TEXT, imposed_by TEXT NOT NULL,
        imposed_at TEXT NOT NULL, violation_id INTEGER,
        lifted_at TEXT, lifted_by TEXT, lift_reason TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS premises_entries (
        id INTEGER PRIMARY KEY, student_id TEXT NOT NULL, student_name TEXT NOT NULL,
        student_status TEXT NOT NULL, entered_at TEXT NOT NULL, last_seen TEXT NOT NULL,
        suspension_id INTEGER)''')
    entry_columns = {r[1] for r in conn.execute("PRAGMA table_info(premises_entries)")}
    if "last_seen" not in entry_columns:
        conn.execute("ALTER TABLE premises_entries ADD COLUMN last_seen TEXT")
        conn.execute("UPDATE premises_entries SET last_seen=entered_at WHERE last_seen IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_suspensions_student ON student_suspensions(student_id, starts_at)")
    conn.execute('''CREATE TABLE IF NOT EXISTS automatic_suspension_awards (
        student_id TEXT NOT NULL, semester_id INTEGER NOT NULL,
        threshold INTEGER NOT NULL, suspension_id INTEGER NOT NULL,
        PRIMARY KEY (student_id, semester_id, threshold))''')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_student ON premises_entries(student_id, entered_at)")


class StudentManagement:
    def update_student_details(self, student_id, *, student_status, contacts,
                               changed_by, reason="", verify_registration=False, academics=None):
        if student_status not in STUDENT_STATUSES:
            raise ValueError("Choose Enrolled, Graduate, or Unenrolled.")
        if not changed_by.strip():
            raise ValueError("An administrator identity is required.")
        values = validate_contacts(contacts)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
            if old is None:
                raise ValueError("Student not found.")
            if academics is not None:
                keys = ('college_department', 'course', 'report_year_level', 'report_section')
                proposed = {key: academics.get(key, old[key]) for key in keys}
                if any(proposed[key] != old[key] for key in keys):
                    validated = academic_values(*(proposed[key] for key in keys))
                    assignments = ', '.join(f'{key}=?' for key in validated)
                    conn.execute(f'UPDATE students SET {assignments} WHERE student_id=?',
                                 (*validated.values(), student_id))
            pending = bool(old["registration_pending"])
            if pending and student_status != "Enrolled":
                raise ValueError("Verify this registration as Enrolled first.")
            changed = (pending and verify_registration) or old["student_status"] != student_status
            if changed and not reason.strip():
                raise ValueError("Give a reason for the status change or enrollment verification.")
            assignments = ", ".join(f"{key}=?" for key in values)
            conn.execute(f"UPDATE students SET {assignments}, student_status=?, registration_pending=? WHERE student_id=?",
                         (*values.values(), student_status, int(pending and not verify_registration), student_id))
            if changed:
                conn.execute("""INSERT INTO student_status_history
                    (student_id, previous_status, new_status, reason, changed_by, changed_at)
                    VALUES (?, ?, ?, ?, ?, ?)""", (student_id,
                    "Pending verification" if pending else old["student_status"], student_status,
                    reason.strip(), changed_by.strip(), format_db_datetime(utc_now())))
        return True

    def get_student_status_history(self, student_id):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM student_status_history WHERE student_id=? ORDER BY id DESC", (student_id,))]

    def impose_suspension(self, student_id, *, reason, starts_at, ends_at,
                          imposed_by, violation_id=None):
        start = parse_db_datetime(starts_at)
        end = parse_db_datetime(ends_at) if ends_at else None
        if not start or (ends_at and not end) or (end and end <= start):
            raise ValueError("End must be later than start; leave end blank only for indefinite suspension.")
        if not reason.strip() or not imposed_by.strip():
            raise ValueError("A reason and administrator identity are required.")
        start_text = format_db_datetime(start)
        end_text = format_db_datetime(end) if end else None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM students WHERE student_id=?", (student_id,)).fetchone():
                raise ValueError("Student not found.")
            if violation_id is not None and not conn.execute(
                    "SELECT 1 FROM violations WHERE id=? AND student_id=?", (violation_id, student_id)).fetchone():
                raise ValueError("The linked violation must belong to this student.")
            overlap = conn.execute("""SELECT 1 FROM student_suspensions WHERE student_id=?
                AND lifted_at IS NULL AND (ends_at IS NULL OR ends_at>?)
                AND (? IS NULL OR starts_at<?)""", (student_id, start_text, end_text, end_text)).fetchone()
            if overlap:
                raise ValueError("An overlapping suspension exists. Lift it before replacing it.")
            cur = conn.execute("""INSERT INTO student_suspensions
                (student_id, reason, starts_at, ends_at, imposed_by, imposed_at, violation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", (student_id, reason.strip(), start_text,
                end_text, imposed_by.strip(), format_db_datetime(utc_now()), violation_id))
            self._queue_discipline_email_conn(conn, student_id, "CBVMS - Suspension Notice",
                f"OSA has assigned a suspension.\nReason: {reason.strip()}\n"
                f"Starts (UTC): {start_text}\nEnds (UTC): {end_text or 'Until lifted by OSA'}\n"
                "Contact OSA for further instructions.")
            return cur.lastrowid

    def lift_suspension(self, suspension_id, *, lifted_by, reason):
        if not lifted_by.strip() or not reason.strip():
            raise ValueError("Give a reason and administrator identity.")
        now = format_db_datetime(utc_now())
        with self.connect() as conn:
            cur = conn.execute("""UPDATE student_suspensions SET lifted_at=?, lifted_by=?, lift_reason=?
                WHERE id=? AND lifted_at IS NULL AND (ends_at IS NULL OR ends_at>?)""",
                (now, lifted_by.strip(), reason.strip(), suspension_id, now))
            return cur.rowcount == 1

    def get_active_suspension(self, student_id, *, now=None):
        timestamp = format_db_datetime(parse_db_datetime(now) if now is not None else utc_now())
        with self.connect() as conn:
            row = conn.execute("""SELECT * FROM student_suspensions WHERE student_id=?
                AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) AND lifted_at IS NULL
                ORDER BY starts_at DESC LIMIT 1""", (student_id, timestamp, timestamp)).fetchone()
            return dict(row) if row else None

    def get_suspension_history(self, student_id):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM student_suspensions WHERE student_id=? ORDER BY id DESC", (student_id,))]

    def record_premises_entry(self, student_id, *, observed_at=None, cooldown_seconds=300,
                              valid_if=None):
        observed = parse_db_datetime(observed_at) if observed_at is not None else utc_now()
        if observed is None:
            raise ValueError("Invalid entry time.")
        timestamp = format_db_datetime(observed)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if valid_if is not None and not valid_if():
                conn.rollback()
                return False
            student = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
            if not student or student["registration_pending"] or student["student_status"] not in ("Graduate", "Unenrolled"):
                return False
            last = conn.execute("SELECT id, last_seen, student_status FROM premises_entries WHERE student_id=? ORDER BY entered_at DESC LIMIT 1", (student_id,)).fetchone()
            if last and last["student_status"] == student["student_status"] and (observed - parse_db_datetime(last["last_seen"])).total_seconds() < cooldown_seconds:
                conn.execute("UPDATE premises_entries SET last_seen=MAX(last_seen, ?) WHERE id=?", (timestamp, last["id"]))
                if valid_if is not None and not valid_if():
                    conn.rollback()
                return False
            suspension = conn.execute("""SELECT id FROM student_suspensions WHERE student_id=?
                AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) AND lifted_at IS NULL""",
                (student_id, timestamp, timestamp)).fetchone()
            conn.execute("""INSERT INTO premises_entries
                (student_id, student_name, student_status, entered_at, last_seen, suspension_id) VALUES (?, ?, ?, ?, ?, ?)""",
                (student_id, student["name"], student["student_status"], timestamp, timestamp, suspension[0] if suspension else None))
            if valid_if is not None and not valid_if():
                conn.rollback()
                return False
        return True

    def get_premises_entries(self, search="", status="All"):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("""SELECT * FROM premises_entries
                WHERE (?='All' OR student_status=?) AND (student_name LIKE ? OR student_id LIKE ?)
                ORDER BY entered_at DESC, id DESC LIMIT 1000""", (status, status, f"%{search}%", f"%{search}%"))]
