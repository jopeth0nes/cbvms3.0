# Main integration validation

Integration combines `feature/academic-term-enrollment` (`4b358ed`, including
`fix/cloud-sync-reconciliation` at `4292b11`) with `Ecc-Coc` (`3d45227`, including
`97fd43d`). Fetched `origin/main` was `13fcdc7`; `origin/mateo-branch` was already
an ancestor of main and required no separate merge. Source branches are retained.

The merge auto-combined `database/db_manager.py` and `.gitignore`; no textual
conflict required choosing one branch over another. Review retained both additive
migrations, term enrollment permissions, occurrence-time ownership, and privileged
forensic scheduling/access. Forensic results remain advisory and local; they do
not change discipline decisions or appear in the cloud student snapshot.

## Integration fixes

- `core/sync_audit.py` explicitly passes `uri=True` for SQLite `mode=ro`, retaining
  `query_only` and transaction behavior. Regression verifies existing databases
  cannot be written even after clearing query_only, and missing paths cannot create
  a database. Existing audit, WAL backup/restore and frozen-export checks also run.
- Appeal category and native workspace fixtures now explicitly seed synthetic
  verified term enrollment. The prior fixtures produced no discipline cases under
  the new eligibility policy. Production eligibility was not relaxed.

## Verification environment and scope

All database tests use disposable files and synthetic identities/evidence. No
application startup against production, production migration, sync, or deployment
was performed. Main application dependencies were preserved.

The old temporary forensic runtime and fixtures were incomplete. A fresh Python
3.12 venv at `/private/tmp/cbvms-integration-forensics-py312` uses the pinned
`requirements-forensics.txt`. Official C2PA v0.38.0 fixtures were fetched into
`/private/tmp/cbvms-integration-c2pa-fixtures` at
`192023c3edc1cd74962650e0fa0f1ec431304e9c`. Test trust anchors/keys remain there,
not in this repository or production trust configuration. Real C2PA tests passed
2/2; the actual DB-to-optional-worker test passed 1/1.

Portal `npm run typecheck`, `npm run build`, and `npm test` passed. Integration
tests exercise local D1/R2 fixtures, HTTP authorization, old-writer rejection,
concurrent publication, action retry/ack safety, and evidence repair.

Native forensic/appeal workspace tests passed 15/15 (41.870 seconds). Final
consolidated Python and remaining UI/HTTP results are recorded below.

Compilation passed. The application/integration diff passes whitespace checks.
The inherited vendor file `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/
smol-toml/primitive.js` contains one existing trailing space on line 129; it remains
byte-for-byte identical to the source branch to preserve upstream integrity hashes.
No production database, evidence, credentials, student records or model weights
were added to the integration commit.

## Remaining operational checks

Physical-camera matching/capture quality, audible alarms and production restoration
remain operator checks. The experimental learned forensic detector has no configured
weights or validated production warning threshold; these checks do not claim model
accuracy. The real C2PA adapter was exercised separately from that detector.

Production migration/deployment/sync still require separate approval. Follow
`ACADEMIC_TERMS.md`, `CLOUD_SYNC_RECONCILIATION.md` and `APPEAL_FORENSICS.md` for
coordinated backup, migration order, optional runtime setup and recovery. A Git
merge/push does not perform those production actions.

## Final acceptance results

- Consolidated regression command: 410 tests passed in 64.642 seconds, no skips
  (`/tmp/cbvms-integration-final.log`). Covers terms/enrollment, discipline,
  suspension rollover, audit/backup/export/retry, forensic permissions/migrations,
  evidence ownership, appeal categories, attendance, unknown sightings, camera
  switching/preview and recognition safeguards.
- Native forensic/appeal workspace: 15 tests passed in 41.870 seconds.
- Native evidence/calendar/reporting plus local HTTP portal: 23 tests passed in
  50.852 seconds (`/tmp/cbvms-integration-ui-http-final.log`).
- Real C2PA adapter: 2 tests passed; optional-runtime DB worker: 1 test passed.
- Portal typecheck, build and isolated HTTP integration tests passed.
- Final fetch confirmed main remained `13fcdc7` before the integration commit;
  semester and Ecc-Coc remote tips remained `4b358ed` and `3d45227`.

The first broad runs exposed only the outdated synthetic appeal fixtures described
above. Those were corrected and the final affected/full gates passed. The initial
optional-runtime attempts failed due to stale temporary environment/fixture paths;
all real-adapter checks subsequently passed in the fresh pinned environment.
