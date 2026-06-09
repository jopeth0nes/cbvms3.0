#!/usr/bin/env python3
"""Prove admin-logged violations route to the correct student's portal — generally.

Two checks, using the REAL database functions (no reimplementation of the join):

  1. Isolation proof on a THROWAWAY temp DB (non-destructive). Enrolls TWO students A and B with
     portal accounts + one unreviewed violation each, then exercises exactly what the apps do:
        * portal load filter  -> [v for v in db.get_violations_for_student(sid)
                                     if v["status"] == "reviewed"]   (student_portal.py:94-97)
        * admin Mark Reviewed -> UPDATE violations SET status='reviewed' WHERE id=?
                                                                       (violation_log.py:_toggle_status)
        * login -> number     -> db.verify_student_account(username, password)  (db_manager.py:443)
     Asserts: nothing shows until reviewed; a reviewed violation shows ONLY for its owner; the
     other student sees zero of it; logging+reviewing a violation for B routes only to B.
     Pure student-number matching, no per-student special-casing.

  2. Real-DB summary (READ-ONLY by default) on data/cbvms.db: prints, per student_id, how many
     violations exist and how many the portal filter would currently show (reviewed only). With
     --mark-reviewed <violation_id> it performs the admin Mark-as-Reviewed on ONE real row to
     demonstrate it then surfaces in that student's portal, and prints how to revert.

Usage:
    python scripts/verify_portal_routing.py                       # temp proof + real read-only summary
    python scripts/verify_portal_routing.py --mark-reviewed 33    # also surface real violation id 33
    python scripts/verify_portal_routing.py --db /path/cbvms.db
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.db_manager import CBVMSDatabase  # noqa: E402

DEFAULT_DB = ROOT / "data" / "cbvms.db"

# The portal's exact visibility rule (student_portal.py:94-97).
REVIEWED = "reviewed"


def portal_visible(db: CBVMSDatabase, student_id: str) -> list[dict]:
    """What the student's 'My Violations' page would load (server-side filter + review gate)."""
    return [v for v in db.get_violations_for_student(student_id) if v.get("status") == REVIEWED]


def admin_mark_reviewed(db: CBVMSDatabase, violation_id: int) -> None:
    """The admin 'Mark as Reviewed' action (same UPDATE as violation_log._toggle_status)."""
    with db.connect() as conn:
        conn.execute("UPDATE violations SET status = ? WHERE id = ?", (REVIEWED, violation_id))
        conn.commit()


def _ok(cond: bool, msg: str) -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return cond


def temp_proof() -> bool:
    print("=" * 78)
    print("1) ISOLATION PROOF — throwaway temp DB, two students, pure student-number routing")
    print("=" * 78)
    tmp = Path(tempfile.mkdtemp(prefix="cbvms_verify_")) / "tmp.db"
    db = CBVMSDatabase(db_path=tmp)
    db.initialize()

    A, B = "2099-00001", "2099-00002"   # arbitrary distinct numbers — nothing hardcoded elsewhere
    for sid, name in ((A, "Alice Test"), (B, "Bob Test")):
        db.insert_student(student_id=sid, name=name, course="BSIT",
                          year_and_section="3A", encoding=b"", photo=b"", gender="Unknown")
        db.upsert_student_account(sid, sid, "pw-" + sid)   # username = student number
        db.log_violation(student_id=sid, student_name=name,
                         violation_type="Wrong uniform (90%)", snapshot_jpeg=None,
                         status="unreviewed")

    va = db.get_violations_for_student(A)
    vb = db.get_violations_for_student(B)
    ok = True
    ok &= _ok(len(va) == 1 and va[0]["student_id"] == A, "A has exactly 1 violation, keyed to A")
    ok &= _ok(len(vb) == 1 and vb[0]["student_id"] == B, "B has exactly 1 violation, keyed to B")
    ok &= _ok(portal_visible(db, A) == [] and portal_visible(db, B) == [],
              "review gate: BOTH portals empty while unreviewed")

    # login -> number mapping resolves by account, not display name
    sess = db.verify_student_account(A, "pw-" + A)
    ok &= _ok(sess is not None and sess["student_id"] == A,
              f"login as A's account resolves to student_id={A} (not by name)")

    # admin reviews A's violation only
    admin_mark_reviewed(db, va[0]["id"])
    vis_a, vis_b = portal_visible(db, A), portal_visible(db, B)
    ok &= _ok(len(vis_a) == 1 and vis_a[0]["student_id"] == A,
              "after reviewing A: A's portal shows A's violation")
    ok &= _ok(len(vis_b) == 0, "after reviewing A: B's portal still shows ZERO of A's")

    # now a separate violation for B, reviewed -> routes to B only
    db.log_violation(student_id=B, student_name="Bob Test",
                     violation_type="Wrong uniform (88%)", snapshot_jpeg=None, status="unreviewed")
    new_b = [v for v in db.get_violations_for_student(B) if v["status"] != REVIEWED][0]
    admin_mark_reviewed(db, new_b["id"])
    vis_a, vis_b = portal_visible(db, A), portal_visible(db, B)
    ok &= _ok(len(vis_b) == 1 and vis_b[0]["student_id"] == B,
              "after reviewing B: B's portal shows B's violation")
    ok &= _ok(all(v["student_id"] == A for v in vis_a) and len(vis_a) == 1,
              "A's portal unchanged — never sees B's violation")

    try:
        tmp.unlink(); tmp.parent.rmdir()
    except Exception:
        pass
    print(f"\n  => isolation proof {'PASSED' if ok else 'FAILED'} (temp DB discarded)\n")
    return ok


def real_summary(db_path: Path, mark_reviewed: int | None) -> None:
    print("=" * 78)
    print(f"2) REAL DB SUMMARY — {db_path}")
    print("=" * 78)
    db = CBVMSDatabase(db_path=db_path)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT student_id, COUNT(*) AS n, "
            "SUM(CASE WHEN status='reviewed' THEN 1 ELSE 0 END) AS reviewed "
            "FROM violations GROUP BY student_id ORDER BY n DESC"
        ).fetchall()
        students = {r[0] for r in conn.execute("SELECT student_id FROM students")}
    print("  student_id      | violations | reviewed(=portal-visible) | enrolled?")
    print("  " + "-" * 70)
    for sid, n, rev in rows:
        print(f"  {sid:15} | {n:10} | {rev or 0:25} | {'yes' if sid in students else 'NO (orphan)'}")

    if mark_reviewed is not None:
        with db.connect() as conn:
            row = conn.execute("SELECT id, student_id, status, violation_type FROM violations WHERE id=?",
                               (mark_reviewed,)).fetchone()
        if row is None:
            print(f"\n  --mark-reviewed {mark_reviewed}: no such violation id."); return
        vid, sid, status, vtype = row
        admin_mark_reviewed(db, vid)
        vis = portal_visible(db, sid)
        print(f"\n  Marked violation id={vid} ('{vtype}', student_id={sid}) REVIEWED (admin action).")
        print(f"  Portal for {sid} now shows {len(vis)} reviewed violation(s) — "
              f"it surfaces in their 'My Violations'.")
        other = next((s for s in students if s != sid), None)
        if other:
            print(f"  Portal for a different student ({other}) shows {len(portal_visible(db, other))} "
                  f"of {sid}'s violations.")
        print(f"  Revert (un-review) with: UPDATE violations SET status='unreviewed' WHERE id={vid};")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify portal violation routing (isolation + real DB).")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="real DB to summarize (default data/cbvms.db)")
    ap.add_argument("--mark-reviewed", type=int, default=None,
                    help="ALSO mark this real violation id reviewed to demonstrate it surfaces")
    ap.add_argument("--skip-temp", action="store_true", help="skip the throwaway isolation proof")
    args = ap.parse_args()

    ok = True
    if not args.skip_temp:
        ok = temp_proof()
    real_summary(Path(args.db), args.mark_reviewed)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
