#!/usr/bin/env python3
"""Re-key a student across the whole CBVMS database: student_id <old> -> <new>.

Why: the recognised student / portal account / their violations are all linked by the TEXT
`student_id` (the student NUMBER, e.g. "2023-00884"). If a student was re-enrolled under a new
number, their historical violations + notifications stay keyed to the OLD number and never reach
their portal. This migration moves EVERY row that references the old number onto the new one, so
the join (and the portal's "My Violations") lines up again.

It is GENERAL and parameterised — nothing is hardcoded to a particular student. It:
  * discovers every table that has a `student_id` column (PRAGMA table_info),
  * pre-flight aborts if <new> already exists in a UNIQUE table (students / student_accounts),
    so the remap can never violate a UNIQUE constraint or merge two distinct people,
  * runs all UPDATEs in ONE transaction (rolls back on any error),
  * also syncs student_accounts.username when it equals the old number (username = student_id
    convention), unless that would collide with an existing username,
  * prints before/after row counts per table,
  * is idempotent: re-running after the move reports 0 rows and changes nothing.

Usage:
    python scripts/remap_student_id.py --from 2023-00884 --to 2023-00883
    python scripts/remap_student_id.py --from 2023-00884 --to 2023-00883 --dry-run
    python scripts/remap_student_id.py --from A --to B --db /path/to/cbvms.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "cbvms.db"

# Tables whose student_id is UNIQUE (one row per person) — remapping into an existing value
# there would collide / merge identities, so we refuse if <new> already exists.
_UNIQUE_STUDENT_TABLES = ("students", "student_accounts")


def _tables_with_column(conn: sqlite3.Connection, column: str) -> list[str]:
    """All user tables that have the given column."""
    out = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
        if column in cols:
            out.append(name)
    return out


def _count(conn: sqlite3.Connection, table: str, column: str, value: str) -> int:
    return int(conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)
    ).fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-key a student_id across the whole CBVMS database.")
    ap.add_argument("--from", dest="old", required=True, help="old student_id (e.g. 2023-00884)")
    ap.add_argument("--to", dest="new", required=True, help="new student_id (e.g. 2023-00883)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path (default data/cbvms.db)")
    ap.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    args = ap.parse_args()

    old, new = args.old.strip(), args.new.strip()
    if not old or not new:
        print("ERROR: --from and --to must be non-empty"); return 2
    if old == new:
        print("ERROR: --from and --to are identical; nothing to do"); return 2

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}"); return 1

    conn = sqlite3.connect(str(db_path))
    try:
        tables = _tables_with_column(conn, "student_id")
        print(f"DB: {db_path}")
        print(f"Remap student_id  '{old}'  ->  '{new}'   ({'DRY-RUN' if args.dry_run else 'WRITE'})")
        print(f"Tables with student_id: {tables}\n")

        # --- Before counts ---
        print("BEFORE:")
        total_old = 0
        for t in tables:
            n_old, n_new = _count(conn, t, "student_id", old), _count(conn, t, "student_id", new)
            total_old += n_old
            print(f"  {t:22} student_id='{old}': {n_old:3}   '{new}': {n_new:3}")
        if total_old == 0:
            # Nothing references the old id — either never present or already migrated. Either
            # way there is nothing to move, so this is a clean idempotent no-op (NOT a collision).
            print(f"\nNothing to remap — no rows reference student_id='{old}'. (idempotent no-op)")
            return 0

        # --- Pre-flight: refuse to collide/merge on UNIQUE tables (only matters when there ARE
        #     old rows to move; otherwise the no-op above already returned). ---
        for t in _UNIQUE_STUDENT_TABLES:
            if t in tables and _count(conn, t, "student_id", new) > 0:
                print(f"\nABORT: {t} already has a row with student_id='{new}' — remapping would "
                      f"collide/merge two distinct students. No changes made.")
                return 1
        # username sync feasibility (student_accounts.username = old -> new)
        sync_username = False
        if "student_accounts" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(student_accounts)")]
            if "username" in cols:
                old_uname = _count(conn, "student_accounts", "username", old)
                new_uname = _count(conn, "student_accounts", "username", new)
                if old_uname > 0 and new_uname == 0:
                    sync_username = True
                elif old_uname > 0 and new_uname > 0:
                    print(f"NOTE: student_accounts.username='{new}' already exists — leaving "
                          f"username unchanged (only student_id will be remapped).")

        if args.dry_run:
            print("\nDRY-RUN: would UPDATE the above 'old' rows to the new id"
                  + (" and sync student_accounts.username" if sync_username else "")
                  + ". No changes written.")
            return 0

        # --- Apply in one transaction ---
        changed = {}
        try:
            conn.execute("BEGIN")
            for t in tables:
                cur = conn.execute(
                    f"UPDATE {t} SET student_id = ? WHERE student_id = ?", (new, old)
                )
                changed[t] = cur.rowcount
            if sync_username:
                conn.execute(
                    "UPDATE student_accounts SET username = ? WHERE username = ?", (new, old)
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"\nERROR during update (rolled back, no changes): {exc}")
            return 1

        # --- After counts ---
        print("\nAFTER:")
        for t in tables:
            n_old, n_new = _count(conn, t, "student_id", old), _count(conn, t, "student_id", new)
            print(f"  {t:22} student_id='{old}': {n_old:3}   '{new}': {n_new:3}   "
                  f"(rows moved: {changed.get(t, 0)})")
        if sync_username:
            print(f"  student_accounts.username synced '{old}' -> '{new}'")
        print(f"\nDONE. {sum(changed.values())} rows remapped to student_id='{new}'.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
