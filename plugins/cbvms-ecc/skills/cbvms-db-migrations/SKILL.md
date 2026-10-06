---
name: cbvms-db-migrations
description: "Create or review CBVMS schema/data migrations, including intentional SQLite-to-PostgreSQL work. Skip ordinary queries and persistence changes without a migration."
---

# Migrate deliberately

Current storage is SQLite. Inspect `database/models.py`, `CBVMSDatabase.initialize()` and the specific `database/*migration.py` or store migration. Initialization mutates schemas: never point a verification run at `data/cbvms.db`. Existing preview helpers under `scripts/preview_*migration.py` must be inspected for copy/dry-run behavior before use.

Identify supported old schemas and dependent readers/writers. Preserve deployed migrations; add a new change for already-deployed behavior. Specify keys, foreign keys, uniqueness, null/default semantics, indexes, timestamp representation and UTC versus Asia/Manila display boundaries.

Test on disposable legacy and populated fixtures: migrate, rerun, compare counts and important values, check duplicate/orphan records (`PRAGMA foreign_key_check` for SQLite), and exercise rollback or backup restoration. Bound transaction/locking duration and failure recovery. Separate schema expansion/backfill/contraction where compatibility requires it.

For SQLite-to-PostgreSQL work, explicitly map autoincrement/sequence state, text IDs, BLOBs, booleans, placeholders, upserts, timestamps/time zones and foreign-key enforcement; validate record counts and referential integrity after transfer.

Identify destructive/data-loss effects before execution; use a tested backup and an explicit authorized production plan. Never infer production mutation authority from a request to design a migration. Astra reviews migration safety independently; reuse reliable worker test output.
