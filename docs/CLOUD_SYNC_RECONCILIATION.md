# Campus/cloud reconciliation — local implementation, production paused

Review date: 20 October 2026 (Asia/Manila). Branch:
`fix/cloud-sync-reconciliation`. `origin/main` was fetched and still matches
`13fcdc75d1c65aaeb9b4d2a45b784fb19d518d35`. The initial worktree was clean.
No applicable repository `AGENTS.md` was found. No production application,
production sync, migration, registration, deployment, or data repair was run.

## Confirmed findings

- `database/db_manager.py`: default campus path is `data/cbvms.db`. Constructing
  the database creates directories; `initialize()` migrates and processes
  deadlines. Many ordinary portal reads also process deadlines.
- `main.py` starts a background sync worker after initialization. The previous
  CLI also initialized the database before inspecting anything.
- `core/portal_state.py` and `web_portal.py`: portal reads and local HTTP startup
  are unsuitable for initial read-only reconciliation.
- `core/cloud_sync.py` previously imported one queued command, published records
  separately from acknowledgments, trusted a local evidence cache, and sent an
  absence-based roster. Receipts did not bind IDs to payloads.
- `sites/student-portal/app/api/[...path]/route.ts` previously disabled absent
  roster entries and deleted cloud accounts when given null credentials.
- `sites/student-portal/lib/student-auth.ts` uses separate server-only credentials
  and authenticated sessions. Profile snapshots exclude credential hashes and
  biometrics. CSRF and per-student authorization remain enforced.
- `.openai/hosting.json` declares project
  `appgprj_6ad63eb439e08191b42fdcd1035145c2`, D1 binding `DB`, R2 binding `BUCKET`.
  These are repository declarations, not verification of the live deployment.
- `data/cloud-sync.json` and the former `data/cloud-sync-state.json` were absent.
  This checkout cannot start its configured cloud worker. Other computers and
  externally scheduled workers have not been inventoried.
- Sites `get_site` and `read_database_overview` returned “project not found” for
  the declared project in the connected account. Live schema, migrations,
  accounts, queue, objects, domain and deployment version remain unverified.

## Sanitized read-only report

The implemented audit used SQLite URI `mode=ro`, `query_only=ON`, and a read
transaction. It did not instantiate the application database, process deadlines,
create receipts, acknowledge actions, upload evidence, or deactivate students.

| Item | Result |
|---|---|
| Local students | 5 |
| Local receipts | 0 |
| Campus source registered | No |
| Local SQLite user_version | 0 (legacy application uses additive migrations) |
| Missing originals with stored checksum | 0 |
| Original checksum mismatches | 0 |
| Local schema SHA-256 | `98b49cd16f743fa536509be2195287237cbbba9caaa420472117bb3744af231e` |
| Local data checkpoint | `dd69a562b5d07db0e33b12e75bc297cfd17101bb97e9334668a720c3769969e7` |
| Local-only/cloud-only students | Unknown: live cloud unavailable |
| Cloud queue kind/age/status and unacknowledged receipts | Unknown |
| Profile/credential conflicts and remote evidence integrity | Unknown |
| Proposed production additions/updates | Unresolved pending live comparison |
| Proposed deactivations/deletions | **0** |
| Production apply allowed | **No** |

The five-student roster is **not established as the complete authoritative campus
roster**. The owner must identify independently edited campus copies and records
that must be preserved. Compare those copies read-only before registration.
Do not treat cloud-only students as deletable. Do not publish this laptop's roster
based only on its small size or possession of credentials.

## Ownership and conflicts

| Data | Owner and rule |
|---|---|
| Identity, enrollment, course and profile | Campus SQLite. Cloud name commands require their server-captured base name to equal the current campus name. Other enrollment edits stay local. |
| Violations, sightings, strikes, suspensions, notifications | Campus SQLite. Preserve integer IDs and relationships. Source registration plus violation identity checks reject conflicting database copies. Sightings/biometrics are not uploaded. |
| Original evidence | Campus originals. R2 contains checksum-addressed copies; GET/hash verifies bytes before snapshot publication. No originals or unrelated R2 objects are deleted. |
| Appeals and appeal evidence | Cloud accepts authenticated commands with server timestamps and evidence ownership/checksum. Campus validates ownership, eligibility and evidence; mutation and receipt commit together. Decisions remain campus-owned. |
| Reports | Cloud submissions are commands; campus stores authoritative reports. |
| Password changes | Compare expected hash and credential revision. Campus resets take precedence. D1's atomic account update preserves a concurrent pending cloud change only when its base and revision still match campus. Hashes remain server-only. |
| Notification read state | Monotonic read commands scoped to the owner. Read-all affects notifications at or before the command time. |
| Preferences | Cloud owns its visual preferences (`dark_mode`, `compact_sidebar`); local preferences provide fallback before first cloud choice. Local email-notification preferences stay campus-owned. There is no generic preference/table merge. |

There is no blanket timestamp last-write-wins policy. Timestamps establish appeal
eligibility and read-all boundaries, not ownership. A conflicting, invalid or
legacy unbound receipt is retained for review, not silently reported as applied.

## Implemented protocol

- Local additive migration creates `campus_sync_identity` and adds receipt
  digests. Registration is explicit, never generated automatically on first sync.
- D1 migration `0002_sync_protocol.sql` creates source/writer/protocol control and
  an immutable publication-generation ledger, and adds credential revisions.
  It intentionally does **not** register a source.
- All sync routes require protocol 2 and the registered source/writer. Retired
  student/account/roster/ack routes reject old clients. Rotate the old sync key
  during cutover; never run an old server after enabling the new client.
- A database-scoped macOS/Windows file lock prevents overlapping local workers.
  Durable generations and pending run IDs reject stale copies and recover a
  lost response after a committed publication. D1 serializes publication batches;
  duplicate generations roll back the entire batch.
- Up to ten pages of 25 commands are imported per cycle, in durable insertion
  order. Missing or malformed evidence isolates that student/action kind while unrelated
  commands, including reports from the same student, proceed. Infrastructure failures retry; rejected commands remain available for
  office review. No queue row is deleted.
- An SQLite backup-API snapshot supplies all related exported records and
  credentials. Legacy getters operate on this disposable copy, not the source.
- R2 verification runs every cycle regardless of cache contents. Content-addressed
  objects are uploaded before publication. Student snapshots, account updates and
  acknowledgments share one D1 transaction. Failed publications leave prior
  snapshots and queued indicators intact; orphaned immutable uploads are retained.
- Empty and greater-than-20% reduced rosters stop for reconciliation. Absence never
  deactivates anyone, and missing credentials never delete an account. This version
  intentionally provides no automated student-deactivation operation.
- Registered/configured campuses defer deadline finalization while the cloud is
  unavailable. A drained queue advances a verified watermark; unresolved appeals
  prevent advancement. Timely appeals imported after legacy deadline processing
  correct only proven deadline strikes and unsupported automatic suspensions,
  using the centralized discipline helpers and an audit/notification trail.
  Mixed/manual escalation histories remain for administrator review. A later
  rejected appeal can restore its strike and earn a new automatic suspension,
  preserving the corrected suspension's history.
- Settings exposes read-only audit, explicit retry of enabled sync, last verified
  publication, pending/oldest action, verified evidence count, and sanitized states.
  Status lives beside the database in `cbvms.sync-status.json`; durable checkpoints
  live in SQLite, not that disposable cache.

## Commands supported by this implementation

From the repository root (replace `.venv/bin/python` with
`.\.venv\Scripts\python.exe` on Windows):

```sh
.venv/bin/python scripts/sync_student_cloud.py --audit
.venv/bin/python scripts/sync_student_cloud.py --dry-run --database /path/to/campus.db
.venv/bin/python scripts/sync_student_cloud.py --backup /secure/new-directory/campus.db
```

No flags defaults to audit. `--backup` uses SQLite's backup API, includes committed
WAL, refuses an existing destination, and opens the result read-only for integrity
and foreign-key verification. Keep backups access-restricted because they contain
student data, credentials and evidence. Backup is not performed by audit.

Only **after approval and backup/restore verification**:

```sh
.venv/bin/python scripts/sync_student_cloud.py --register-source SOURCE_UUID --writer-id WRITER_UUID
.venv/bin/python scripts/sync_student_cloud.py --once
```

Use actual UUIDs, generated and recorded at the approved registration step. The
local registration inserts exactly one source and does not enable uploads. The
matching D1 registration is a reviewed parameterized insert:

```sql
INSERT INTO campus_sync_control (singleton,source_id,writer_id,protocol)
VALUES (1, :approved_source_uuid, :approved_writer_uuid, 2);
```

The ignored configuration retains `origin`, `sync_key`, `service_token` and adds
`source_id`, `writer_id`, `enabled` (default false). Use an exact HTTPS origin.
Do not copy its writer credentials to an independent campus database. A clone is
not a new authoritative source; use read-only reconciliation for failover.
`--once` and Settings retry require `enabled: true`. The desktop background worker
uses the same gate and retries every minute. No-flag CLI continuous sync is retired.

## Production gate, deployment order and recovery

1. Confirm campus authority and inventory every writer. Obtain access to the
   actual Sites project and provider storage; verify binding IDs and migration
   history rather than using the placeholder preview database ID. Review the
   cloud's existing roster and queued actions before any registration.
2. Produce a fresh sanitized comparison and a restricted detailed reconciliation
   manifest. Resolve database-copy collisions, missing credentials and legacy
   evidence/receipt ambiguities. Owner approval must name the source, writer,
   deployment version, migrations and concrete proposed record changes.
   An overlapping student is a *candidate update*, not proof its fields differ.
3. Back up SQLite with the command above. Export D1 and preserve an R2 object
   inventory plus every referenced original/appeal object in a new dated backup.
   Store object keys privately; they can contain student IDs. Record schemas,
   migration history, source identity, local checkpoint, D1 generation and queue
   inventory. Provider access was unavailable here; none of these live backups
   or remote restore tests has been claimed as complete.
4. Restore all backups into a separate SQLite file and isolated D1/R2 environment.
   Verify integrity, foreign keys, counts, receipt/queue relationships and every
   referenced object's checksum. Retain the originals and the pre-cutover backups.
   Quiesce writes during the final backup/comparison window, or repeat inventory
   and checkpoint validation until a consistent cutover set is obtained.
5. After approval, pause old campus workers and revoke/rotate their sync key.
   Apply additive D1 migration 0002, then deploy protocol-v2 server with **no source
   registered yet**. Old clients must receive a refusal. Preserve student access
   controls. Apply the additive local migration/register only the approved source.
6. Register matching D1 source/writer; keep uploads disabled. Re-run audit against
   v2. If local checkpoint, roster, credential conflicts, queue or cloud generation
   changed, recompute the plan and obtain approval for changed effects. Before
   registration the old server has no v2 audit API, so provider read-only inspection
   is required for the initial report.
7. Enable only that approved worker and run `--once`. Verify records, evidence,
   account behavior, receipts and status. Then permit background sync. These
   procedures are an operator approval gate; the software does not infer approval
   from a report file or grant itself production permission.

Provider command templates (require verified real resource names/configuration;
not executed in this task):

```sh
npx wrangler d1 export VERIFIED_DATABASE --remote --output=/secure/dated-backup/d1.sql
aws s3api list-objects-v2 --bucket VERIFIED_BUCKET --endpoint-url https://ACCOUNT_ID.r2.cloudflarestorage.com > /secure/dated-backup/r2-inventory.json
aws s3 sync s3://VERIFIED_BUCKET /secure/dated-backup/r2 --endpoint-url https://ACCOUNT_ID.r2.cloudflarestorage.com --only-show-errors
```

D1's supported export/import workflow is documented by
[Cloudflare](https://developers.cloudflare.com/d1/best-practices/import-export-data/).
R2's supported S3/AWS CLI access is documented
[here](https://developers.cloudflare.com/r2/examples/aws/aws-cli/). Use provider
credentials through their normal protected credential store, never command-line
secret values. Copy to a fresh directory and verify SHA-256; do not assume an
ETag is a SHA-256 checksum or native object versioning is enabled.

For interrupted sync, preserve both SQLite receipts and D1 publication history;
retry with the same database. Lost publication responses recover from pending
run/generation. Corrupted status/cache files can be discarded without losing
checkpoints. For conflicts, stop uploads and audit; do not reset generations,
clear receipts, acknowledge unresolved commands as applied, or restore an old
SQLite copy over a running database. Review rejected appeals in the campus admin
workflow, retaining their cloud submission/audit trail. Automatic retry intentionally
does not rewrite terminal receipts or discard rejected cloud commands.

Rollback means disable uploads and keep the v2 source/protocol gate. Fix forward
or restore backups **in isolation**, reconcile actions accepted since the backup,
and present a recovery plan. Do not roll back to the unsafe v1 writer endpoints.

## Verification and limits

Tests use synthetic students and temporary SQLite/D1/R2 storage. The isolated
portal integration suite, TypeScript check and production build passed. The final combined backend/HTTP run passed **90 tests** in 27.279 seconds.
The focused sync suite passed 30 tests. Initial
HTTP attempts were blocked by sandbox loopback restrictions and passed after
local-server permission. A broad run containing native UI tests aborted in the
macOS environment; backend-specific selections passed. No native UI success is
claimed. Commands:

```sh
.venv/bin/python -m unittest tests.test_cloud_sync tests.test_web_portal tests.test_suspensions tests.test_password_setup tests.test_portal_requests tests.test_appeal_flow_completion.AppealFlowBackendTests tests.test_evidence_association.EvidenceAssociationTests tests.test_appeal_alerts -q
.venv/bin/python -m tests.check_cloud_portal_http
cd sites/student-portal
npm run typecheck
npm test
npm run build
```

The implementation deliberately uses a single bounded publication request (15 MB,
1.5 MB per student snapshot) and D1 batch. It fails closed if provider request,
statement or runtime limits are exceeded. Large-campus load verification and
staged publication would be needed before enabling a roster that exceeds these
limits; no partial publication fallback is used. The 20% guard is a stop signal,
not a completeness proof. A cloned database/configuration with the same current
generation must still be treated as the same writer: keep credentials exclusive;
the generation fence ensures only one competing publication commits.

Native Windows execution and Settings visual QA were not performed. The lock has
platform-specific Windows/macOS implementations, and paths/temporary snapshots use
standard cross-platform Python APIs. Live cloud restore, schema compatibility,
queue reconciliation and full campus inventory remain blocked by missing access
and authority confirmation. These limits preclude production approval now.
