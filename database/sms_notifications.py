"""Transactional, one-attempt SMS outbox; no historical violation backfill."""
import logging
import sqlite3
import threading
from contextlib import contextmanager

from core import sms_sender

MESSAGE = ("CBVMS: A possible violation was recorded. Log in to the student portal "
           "to review the record and submit an appeal if needed.")


@contextmanager
def _connect(db):
    conn = sqlite3.connect(db.db_path, timeout=db.timeout)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def migrate(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS violation_sms (
        violation_id INTEGER PRIMARY KEY REFERENCES violations(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',
        provider_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        attempted_at TEXT
    )""")


def queue(conn, db, violation, *, schedule=True):
    # Recording the event must not depend on credentials being available yet.
    # The worker waits for configuration, so a new notice is not silently lost.
    if db.student_session_ref is not None:
        return
    student = conn.execute("SELECT mobile_number FROM students WHERE student_id=? AND is_archived=0",
                           (violation['student_id'],)).fetchone()
    if not student or not sms_sender.normalize_phone(student[0]):
        return
    inserted = conn.execute("INSERT OR IGNORE INTO violation_sms(violation_id) VALUES(?)",
                            (violation['id'],)).rowcount
    if inserted and schedule:
        conn.after_commit(lambda: wake(db))


def drain(db, *, violation_id=None):
    """Claim before network I/O; concurrent processes cannot send the same row."""
    while sms_sender.configured():
        with _connect(db) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT q.violation_id,s.mobile_number FROM violation_sms q
                JOIN violations v ON v.id=q.violation_id
                JOIN students s ON s.student_id=v.student_id
                WHERE q.status='pending' AND (? IS NULL OR q.violation_id=?)
                ORDER BY q.violation_id LIMIT 1""", (violation_id,violation_id)).fetchone()
            if not row:
                return
            vid, phone = row
            valid = conn.execute("""SELECT 1 FROM violations v JOIN students s ON s.student_id=v.student_id
                WHERE v.id=? AND v.status<>'dismissed' AND s.is_archived=0""", (vid,)).fetchone()
            conn.execute("UPDATE violation_sms SET status=?,attempted_at=datetime('now') WHERE violation_id=?",
                         ('sending' if valid else 'skipped', vid))
        if not valid:
            continue
        status, provider_id = sms_sender.send_sms(phone, MESSAGE)
        with _connect(db) as conn:
            conn.execute("UPDATE violation_sms SET status=?,provider_id=? WHERE violation_id=?",
                         (status, provider_id, vid))


_lock = threading.Lock()
_running = set()


def wake(db):
    if not sms_sender.configured() or db.student_session_ref is not None:
        return
    key = str(db.db_path)
    with _lock:
        if key in _running:
            return
        _running.add(key)
    def work():
        try:
            # Polling closes the enqueue/worker-exit race and recovers pending rows.
            while True:
                drain(db)
                threading.Event().wait(2)
        except Exception:
            logging.getLogger(__name__).warning("SMS worker stopped; restart application to resume pending alerts")
        finally:
            with _lock:
                _running.discard(key)
    threading.Thread(target=work, name="violation-sms", daemon=True).start()
