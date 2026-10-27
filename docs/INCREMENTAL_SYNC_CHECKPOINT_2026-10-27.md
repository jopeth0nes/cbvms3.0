# Incremental synchronization checkpoint — 27 October 2026

## Release state

**The overall task is incomplete. Nothing from this incremental-sync implementation
has been deployed or enabled in production.** Branch `main`, base commit
`338ad83` (`Delete done`). Changes listed below are uncommitted. No production
migration, registration, identity adoption, publication, or acknowledgment was
started. No uncertain production write needs replay. Existing Windows publication
and the independent provider-admin lifecycle dispatcher remain unchanged.

Read `INCREMENTAL_SYNC.md` for ownership and rollout order. Original authorization
for compatible deployment remains valid; do not ask for it again. Do not treat this
checkpoint as permission to enable the incomplete domain integration.

## Recovered live state

Authenticated read-only verification after refreshing the existing Wrangler OAuth
session succeeded. Local database: five students, integrity OK, zero FK errors.
Cloud: seven students/accounts, six active, one lifecycle receipt and one recycled
identity. The inactive record is explained by the verified lifecycle receipt;
never reactivate it as an import repair. Import rows, credential hashes/revisions,
58 negative case mappings, and lifecycle-aware preservation verify against the
original import bundle. Windows control/ledger still match the import baseline,
generation 3. Migrations end at 0007. No MacBook general-sync config was enabled.
Windows access/backup/client update remains unconfirmed. Portal projections cannot
recover unprojected Windows history or local recognition galleries.

## Implemented and tested, dormant

- `database/incremental_sync.py`: explicit transactional local migration, device
  identity, monotonic outbox sequence, dependencies, inbox/cursor/page receipts,
  versions, full conflict details and upload/download counters. Migration backfill
  runs once under a savepoint.
- `core/incremental_sync.py`: caller-transaction enqueue; same-entity ordering
  through backoff/conflict; bounded unrelated progress; exact immutable receipt
  validation; late lost-ack recovery without version regression; bounded jitter;
  explicit auth resume; atomic inbound application with upload-loop guard;
  empty-page polling and ACL-filtered global cursor gaps. Worker uses 0.25-second
  SQLite timeout and single-flight locking.
- `core/campus_client.py`: direct HTTPS only, no redirects, device plus staff
  session headers from an injected secure credential supplier, bounded requests
  and responses, 15-second HTTP timeout, single background upload/download cycle.
- `database/shared_profiles.py`: authenticated local administrator profile change
  and outbox in the same transaction; requires an explicitly adopted baseline.
  Pending local values survive downloads. Cloud-only metadata is cached without
  fabricating students, enrollment captures or recognition assets.
- `sites/student-portal/drizzle/0008_incremental_profiles.sql`: **not applied**.
  Dormant device/staff-session/grant/profile authority, immutable operation receipts
  and journal, SQL authorization/CAS, atomic profile projection and journal update.
  Preserves lifecycle restrictions, permits exact profile-only updates after
  restore, and fences incompatible legacy changes only to explicitly adopted rows.
- `sites/student-portal/lib/incremental-sync.ts` and API route: new authenticated
  `/api/campus/operations` (maximum 10 commands) and `/api/campus/changes` (25 events).
  Only `student.profile.patch` (`name`, `email`) is implemented. Client roles,
  credentials, lifecycle fields and arbitrary JSON mutations are rejected.

Existing camera, Reports, PasswordCrypto, academic and lifecycle domain code was
not edited. The existing archive test loader now handles trigger-containing
migration files using statement breakpoints.

## Verification tied to current files

- Builder: `.venv/bin/python -m unittest tests.test_incremental_sync -v`:
  **16 passed** after final corrections. Includes actual simulated server commit
  then lost response, exact retry, full identity binding, ordering, auth resume,
  empty polling, global gaps, callback rollback, legacy dependency migration and
  late acknowledgments after a newer inbound version.
- Lead: `.venv/bin/python -m unittest tests.test_campus_client
  tests.test_report_snapshot tests.test_report_csv tests.test_student_lifecycle_cloud
  tests.test_lifecycle_delivery -q`: **41 passed in 11.323 seconds**.
- `node tests/incremental.mjs` from `sites/student-portal`: passed real Miniflare
  D1 tests for ACL/device/session isolation, forged roles, duplicate receipt replay,
  competing versions, preserved negative mapping/history, recycled denial,
  post-restore update and expiry. It also invokes
  `tests/check_incremental_client.py`: **two real Python clients** against the
  isolated Worker. One accepted edit, one preserved conflict, inbound cursors 4,
  four inbox events each, a later cycle, no duplicate outbox generation.
- `node tests/archive.mjs`, `node tests/sync.mjs`, and
  `./node_modules/.bin/tsc --noEmit`: passed. Existing legacy writer, credential,
  CSRF, evidence, lifecycle and restore regressions remain intact.
- `git diff --check`: passed. No broad native UI or physical-camera test claim.

Synthetic performance, `.venv/bin/python scripts/benchmark_incremental_sync.py`:
5,000 accepted history rows + 100 pending; 100 accepted in 10 transport batches;
queue selection median 0.396 ms, maximum 0.911 ms; idle tick maximum jitter
2.510 ms, busy 2.118 ms; held-writer lock wait 286.301 ms (configured timeout
0.25 seconds); worker tracemalloc peak 170,636 bytes. Disposable WAL SQLite and
in-process transport only: **not network throughput, process RSS, inference latency,
Tk responsiveness or physical-camera FPS**.

## Fresh recovery artifacts and rehearsal

Restricted backup directory:
`data/backups/incremental-20261026T161035Z/` (UTC timestamp; 27 October Manila).
Pointer: `.runtime/incremental-recovery-path.txt`.

Contains SQLite backup and independent restore, D1 SQL export/restore, Worker
source/settings/deployments/domains, R2 inventory, 65 downloaded objects and SHA-256
manifest. SQLite/D1 restore integrity and FK checks passed; original import and
current lifecycle checks passed. All 65 object files were read back and hashed.
This verifies recoverable private object bytes, not an upload to a replacement R2
bucket. No active publisher was paused, so these are recovery snapshots, not a
coordinated final cutover set.

`scripts/rehearse_incremental_schema.py` ran the migration ledger process twice on
a disposable copy of this D1 restore: 13 preexisting tables preserved exactly,
zero FK errors, no adopted identities/devices/commands, no duplicate migration.
This is schema-preservation rehearsal, **not complete data reconciliation**.
Results: `verification.json`, `schema-rehearsal.json` in the private directory.
Private orchestration remains under ignored `.runtime/`; never publish it or
credential/bundle data. Provider session refresh was the only credential-side
operation; no application secrets were rotated.

## First unfinished work and acceptance gaps

1. Finish profile production readiness: secure device enrollment and staff-session
   issuance/revocation, authenticated conflict resolution, reliable retry/status UI,
   verified identity/bootstrap mapping, and existing desktop write-path integration.
   The new helpers are explicit opt-in APIs; ordinary UI edits do not yet enqueue.
2. Extend named authoritative commands to registration, academic calendars and
   enrollment metadata, attendance/violations/evidence, appeals/decisions/notifications,
   reports, password protocol, strikes/suspensions and rollover. Keep biometric
   provenance local. No generic profile patch may substitute for these operations.
3. Add cloud submission consumption and shared exactly-once discipline transitions.
   The new journal currently contains profile events only; existing queued portal
   actions have not been acknowledged or imported by this new client.
4. Resolve the legacy writer transition without copying its identity. Adoption
   deliberately fences stale snapshot changes; do not adopt a record while its
   remaining workflows still depend on the incompatible publisher. Obtain Windows
   inventory/client coordination when available; complete independent code meanwhile.
5. Run full identity/evidence reconciliation twice; add missing workflow, cloud
   evidence interruption, and real camera/inference measurements. Refresh coordinated
   backups, deploy verified compatible support and only then enroll/enable devices.
6. Observe production server receipts, downloads and a subsequent background cycle.
   Neither MacBook nor Windows has passed this production acceptance. No actual
   incremental-production acknowledgment/cursor exists from this work.

Recovery: preserve all old and new receipts, queues, versions and backups. Never
reset cursor/generation to force replay or overwrite live data with these snapshots.
Restore in isolation and reconcile every accepted operation after the backup.
The lifecycle dispatcher remains authoritative; do not disable it or lose its
outstanding operations. Resume here, not from the superseded sole-writer handoff.
