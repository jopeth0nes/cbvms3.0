#!/usr/bin/env python3
"""Prove confirmed violations route to the correct student's portal.

Two checks, using the real workflow APIs rather than reimplementing portal rules:

  1. Isolation proof on a THROWAWAY temp DB. Enrolls two students with one
     ``pending_review`` violation each, then exercises ``confirm_violation`` and
     ``get_visible_violations_for_student``. It asserts that pending detections stay hidden and
     each confirmed violation becomes visible only to its owner.

  2. Real-DB summary on data/cbvms.db through a SQLite ``mode=ro`` connection. The real database
     is not initialized, migrated, or changed by default. ``--confirm <violation_id>`` explicitly
     opts into initialization/migration and confirmation of that one record.

Usage:
    python scripts/verify_portal_routing.py                       # temp proof + real read-only summary
    python scripts/verify_portal_routing.py --confirm 33          # MUTATES: confirm real violation 33
    python scripts/verify_portal_routing.py --mark-reviewed 33    # deprecated alias for --confirm
    python scripts/verify_portal_routing.py --db /path/cbvms.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.db_manager import CBVMSDatabase  # noqa: E402
from core.discipline import CONFIRMED_STATUSES, PENDING_REVIEW  # noqa: E402

DEFAULT_DB = ROOT / "data" / "cbvms.db"

def portal_visible(db: CBVMSDatabase, student_id: str) -> list[dict]:
    """Use the same server-side visibility API as the student portal."""
    return db.get_visible_violations_for_student(student_id)


def admin_confirm(db: CBVMSDatabase, violation_id: int) -> bool:
    """Use the validated admin workflow, including its atomic side effects."""
    return db.confirm_violation(violation_id, decided_by="verification_script")


def _ok(cond: bool, msg: str) -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return cond


def temp_proof() -> bool:
    print("=" * 78)
    print("1) ISOLATION PROOF — throwaway temp DB, two students, pure student-number routing")
    print("=" * 78)
    with tempfile.TemporaryDirectory(prefix="cbvms_verify_") as tmp_dir:
        db = CBVMSDatabase(db_path=Path(tmp_dir) / "tmp.db")
        db.initialize()

        a_sid, b_sid = "2099-00001", "2099-00002"
        violation_ids: dict[str, int] = {}
        for sid, name in ((a_sid, "Alice Test"), (b_sid, "Bob Test")):
            db.insert_student(
                student_id=sid,
                name=name,
                course="BSIT",
                year_and_section="3A",
                encoding=b"",
                photo=b"",
                gender="Unknown",
            )
            db.upsert_student_account(sid, sid, "pw-" + sid)
            violation_ids[sid] = db.log_violation(
                student_id=sid,
                student_name=name,
                violation_type="Wrong uniform (90%)",
                violation_code="wrong_uniform",
                snapshot_jpeg=None,
                status=PENDING_REVIEW,
            )

        va = db.get_violations_for_student(a_sid)
        vb = db.get_violations_for_student(b_sid)
        ok = True
        ok &= _ok(
            len(va) == 1
            and va[0]["student_id"] == a_sid
            and va[0]["status"] == PENDING_REVIEW,
            "A has exactly 1 pending-review violation, keyed to A",
        )
        ok &= _ok(
            len(vb) == 1
            and vb[0]["student_id"] == b_sid
            and vb[0]["status"] == PENDING_REVIEW,
            "B has exactly 1 pending-review violation, keyed to B",
        )
        ok &= _ok(
            portal_visible(db, a_sid) == [] and portal_visible(db, b_sid) == [],
            "review gate: both portals are empty while violations are pending review",
        )

        # Login resolves through the account's student number, never a display name.
        session = db.verify_student_account(a_sid, "pw-" + a_sid)
        ok &= _ok(
            session is not None and session["student_id"] == a_sid,
            f"login as A resolves to student_id={a_sid}",
        )

        ok &= _ok(
            admin_confirm(db, violation_ids[a_sid]),
            "admin workflow confirms A's pending violation",
        )
        vis_a, vis_b = portal_visible(db, a_sid), portal_visible(db, b_sid)
        ok &= _ok(
            len(vis_a) == 1 and vis_a[0]["student_id"] == a_sid,
            "after confirmation, A sees A's violation",
        )
        ok &= _ok(
            len(vis_b) == 0,
            "after confirming A, B still sees zero of A's violations",
        )

        ok &= _ok(
            admin_confirm(db, violation_ids[b_sid]),
            "admin workflow confirms B's pending violation",
        )
        vis_a, vis_b = portal_visible(db, a_sid), portal_visible(db, b_sid)
        ok &= _ok(
            len(vis_b) == 1 and vis_b[0]["student_id"] == b_sid,
            "after confirmation, B sees B's violation",
        )
        ok &= _ok(
            len(vis_a) == 1 and all(v["student_id"] == a_sid for v in vis_a),
            "A's portal remains isolated from B's violation",
        )

    print(f"\n  => isolation proof {'PASSED' if ok else 'FAILED'} (temp DB discarded)\n")
    return ok


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file without creating, journaling, or migrating it."""
    resolved = db_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _confirm_real_violation(db_path: Path, violation_id: int) -> bool:
    """Explicitly migrate, then confirm one real record through the workflow API."""
    print(
        f"\n  --confirm {violation_id}: explicit write requested; "
        "running idempotent migrations first."
    )
    db = CBVMSDatabase(db_path=db_path)
    # Apply schema migrations without processing unrelated workflow deadlines; this
    # explicitly mutating command promises to target only the requested record.
    db.initialize(process_deadlines=False)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, student_id, status, violation_type FROM violations WHERE id = ?",
            (violation_id,),
        ).fetchone()
    if row is None:
        print(f"  No violation exists with id={violation_id}.")
        return False

    if not db.confirm_violation(violation_id, decided_by="verification_script"):
        print(
            f"  Confirmation rejected for id={violation_id}; "
            f"current status is '{row['status']}'."
        )
        return False

    with db.connect() as conn:
        confirmed = conn.execute(
            "SELECT status FROM violations WHERE id = ?", (violation_id,)
        ).fetchone()
    status = confirmed["status"] if confirmed is not None else "unknown"
    print(
        f"  Confirmed violation id={violation_id} ('{row['violation_type']}', "
        f"student_id={row['student_id']}); persisted status='{status}'."
    )
    print("  This audited workflow transition is intentionally not reversible.")
    return True


def real_summary(db_path: Path, confirm_id: int | None) -> bool:
    print("=" * 78)
    print(f"2) REAL DB SUMMARY (READ-ONLY) — {db_path}")
    print("=" * 78)
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_read_only(db_path)
        visible_statuses = tuple(CONFIRMED_STATUSES)
        placeholders = ",".join("?" for _ in visible_statuses)
        rows = conn.execute(
            "SELECT student_id, COUNT(*) AS n, "
            f"SUM(CASE WHEN status IN ({placeholders}) THEN 1 ELSE 0 END) AS visible "
            "FROM violations GROUP BY student_id ORDER BY n DESC",
            visible_statuses,
        ).fetchall()
        students = {r[0] for r in conn.execute("SELECT student_id FROM students")}
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"  Unable to read database without modification: {exc}")
        return False
    finally:
        if conn is not None:
            conn.close()

    print("  student_id      | violations | confirmed/visible | enrolled?")
    print("  " + "-" * 70)
    for sid, count, visible in rows:
        sid_text = str(sid or "unknown")
        print(
            f"  {sid_text:15} | {count:10} | {visible or 0:17} | "
            f"{'yes' if sid in students else 'NO (orphan)'}"
        )

    print("\n  Read-only summary complete; no initialization or migration was performed.")
    if confirm_id is not None:
        return _confirm_real_violation(db_path, confirm_id)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify portal violation routing (isolation + real DB).")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="real DB to summarize (default data/cbvms.db)")
    action = ap.add_mutually_exclusive_group()
    action.add_argument(
        "--confirm",
        type=int,
        default=None,
        help="MUTATE the real DB by confirming this violation through the workflow API",
    )
    action.add_argument(
        "--mark-reviewed",
        type=int,
        default=None,
        help="deprecated alias for --confirm",
    )
    ap.add_argument("--skip-temp", action="store_true", help="skip the throwaway isolation proof")
    args = ap.parse_args()

    ok = True
    if not args.skip_temp:
        ok = temp_proof()
    confirm_id = args.confirm
    if args.mark_reviewed is not None:
        print("WARNING: --mark-reviewed is deprecated; use --confirm instead.", file=sys.stderr)
        confirm_id = args.mark_reviewed
    ok &= real_summary(Path(args.db), confirm_id)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
