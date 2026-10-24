> Latest update: the one-time additive MacBook portal import is complete and verified.
> Seven cloud accounts now exist; username `2023-00883` and its linked history are
> present. Actual password sign-in awaits user confirmation. Ongoing MacBook/shared
> desktop synchronization remains disabled. See the final production-import section.

# MacBook cloud connection checkpoint — 24 October 2026

## Status at resume

Production connection is **not complete**. No production registration, migration,
writer handoff, publication, acknowledgment, credential rotation or sync configuration
write has been attempted. The sole interrupted process was a synthetic test run;
its recovered exit status was 0: 56 tests passed in 20.008 seconds. No operation is
currently in flight at this checkpoint. Existing Reports and camera work is intact.

Checkout: main, c7c6e2882688609d164523224449b2632eecb813. Remote main matched before
interruption. Initial post-resume remote check hit sandbox DNS failure; retry is
pending. Working changes are listed below. Previously read deployment/reconciliation
runbooks describe a separate Windows publication, not a MacBook publication.

## Verified local and cloud state

- Intended local database: `/Users/jopethones/Documents/GitHub/cbvms3.0/data/cbvms.db`.
  Read through SQLite read-only transactions, without application initialization.
  Five students/accounts, 82 violations, eight appeals, seven evidence files, two
  academic terms, zero term enrollments, zero cloud receipts, no registered source.
  `data/cloud-sync.json` absent. Local logical checkpoint still matches backup on
  resume. Originals with stored checksums have zero missing/mismatched bytes.
- Cloudflare OAuth succeeded using installed Node/Wrangler. Protected credentials
  are under ignored `.runtime/cloud-config/.wrangler/config/default.toml`.
  Set `XDG_CONFIG_HOME` to this checkout's `.runtime/cloud-config` for Wrangler.
  The Worker SYNC_KEY is **not** available locally; OAuth does not reveal its value.
- Supported provider API confirms `studentportal.bond` routes to production Worker
  `cbvms-student-portal`, bound to D1 `640d2258-ca44-45b3-b83a-5e1f0caec663`, R2
  `cbvms-student-evidence`, and internal `PASSWORD_CRYPTO` / `PasswordCrypto`.
  Account: `88e619a8bc929cbec00901fdadd3edc1`.
- D1 export restored in isolated SQLite: integrity OK, zero foreign-key errors.
  Migration history includes 0000 through 0004_publication_roster.sql.
  Protocol 2, minimum snapshot version 3, generation 3, three publication ledger
  rows, published_student_count 1. Source/writer and run IDs retained in private
  SQL backup; no ownership changes made.
- Two cloud students/accounts; zero overlapping permanent student IDs with MacBook;
  five local-only and two cloud-only. Zero username collisions. No cloud violation
  snapshots or original/appeal evidence references. One applied password action
  and one queued password action, whose owner is cloud-only.
- R2 inventory is empty; managed public domain disabled, no custom domains.
  No object downloads/restores required for this inventory, but repeat at cutover.
- Username 2023-00883 exists exactly once locally with a valid student join and
  credential revision 2; it does not exist in the cloud export. Actual password
  login and synchronized credentials have **not** been verified.

## Backups and verification

Private directory (0700; all files 0600):
`data/backups/macbook-connect-20261024T072556Z/`

- `campus.db`: SQLite backup API, includes committed WAL.
- `restore.db`: separate SQLite backup/restore; integrity and foreign keys passed,
  logical dump digest matches campus.db.
- `cloud-d1.sql`, `cloud-restore.db`: provider export and isolated restore.
- `manifest.json`, `comparison-private.json`, `username-conflicts-private.json`:
  counts, local checkpoint and comparison evidence.
- `domains.json`, `settings.json`, `deployments.json`, `r2.json`, and
  `r2-inventory.json`, `r2-managed-domain.json`, `r2-custom-domains.json`.
- `verification.json`: exact tested file hashes, command and result.

These are independent safety backups, **not a coordinated cutover set**: Windows
writer pause is unconfirmed and cloud students can still submit actions.
Repeat fresh snapshots/inventory after ownership and writer coordination. Do not
restore these over live databases or reset generations/receipts. No production
rollback is presently needed; preserve all copies and prefer forward repair.

## Code changes and recovered test evidence

- `scripts/configure_student_cloud.py`: validates current protocol fields and
  matching local registration, optional Sites token, exact HTTPS origin and disabled
  initial state; rejects existing files/symlinks; writes 0600; hidden input fails
  closed if terminal echo cannot be disabled; errors do not expose parsed values.
- `core/cloud_sync.py`: omits absent Sites gateway header for direct Cloudflare;
  retains it when configured. Leaves cloud-only commands queued without creating
  rejected local receipts or advancing the deadline watermark. Other owned actions
  and snapshots can proceed.
- `tests/test_configure_student_cloud.py`: five secure configuration tests.
- `tests/test_cloud_sync.py`: regression proves cloud-only commands stay queued
  across two publications, no foreign receipt, no watermark, owned report once.
  It failed before the queue fix and passed after it.

Command:
`.venv/bin/python -m unittest tests.test_configure_student_cloud tests.test_cloud_sync tests.test_report_snapshot tests.test_report_csv -q`

Result: **56 passed in 20.008 seconds**; disposable synthetic databases only.
`git diff --check` passed. This does not constitute a real-data merge rehearsal,
production sync, deployed-code comparison, or actual login verification.

## Exact next actions / unresolved gates

1. Recover live D1 control/ledger/queue and compare with export after interruption;
   retry remote Git check with network access. No production write was in flight.
2. Obtain answer to pending writer question: is Windows sync stopped, and should
   MacBook permanently replace it as sole publisher? Databases have disjoint IDs;
   do not copy Windows source/writer identities. If both must publish independently,
   the single-writer architecture must be resolved before ownership changes.
3. Obtain existing matching SYNC_KEY through secure local input/transfer when needed,
   never chat/arguments/logs. Do not rotate solely because OAuth cannot retrieve it.
4. Rehearse verified additive publication against isolated D1/R2 and campus copies,
   preserving two cloud-only identities and their queued password change. Implement
   and test an explicit, guarded ownership handoff only once source scope is clear;
   preserve generation ledger and use a new writer fence, not copied credentials.
5. Take coordinated fresh recovery set, recheck source/queue fingerprints, then
   perform authorized handoff/configuration and two controlled syncs, followed by
   an observed background cycle. Keep uploads unconfigured until gates pass.

No real-data merge rehearsal or publication has yet run. Proposed additions: five;
proposed overlapping updates: zero; retained cloud-only identities: two. These are
identity comparison counts, not applied changes or verified whole-campus coverage.
All production records added/updated/deleted by this task: **0/0/0**.

## Resume follow-up

- Remote Git retry succeeded: main still c7c6e28. Local checkpoint still matches
  the safety backup. No source registration/configuration or receipts appeared.
- Fresh cloud query failed: direct API 401; Wrangler query error 7403 (account not
  valid or not authorized for service). Do not assume current production equals
  earlier export. Official OAuth reconnect opened; last in-flight operation is
  **Wrangler login**, process session 26439, output `.runtime/cloud-login-resume.log`.
  The user was asked to complete that secure flow. No mutating production command
  was started. Windows publisher pause/sole-publisher decision is still unanswered.
- Real restored-data rehearsal completed in isolated Miniflare D1/R2 with the
  existing API handlers and synthetic sync credentials. A proposed handoff was
  simulated only in disposable copies, preserving generation 3 and using distinct
  rehearsal source/writer IDs. Two actual client sync cycles succeeded, generations
  4 and 5, five students each. Result: seven cloud students/accounts, two original
  cloud identities/accounts and all existing cloud actions byte-for-field unchanged,
  target username present, 58 checksum-verified evidence objects. No local receipt
  was made for the cloud-only pending password action; watermark stayed unchanged.
  All campus business tables were compared with the original backup and unchanged
  (only isolated sync identity/checkpoints differ). This is rehearsal evidence,
  not production publication or actual student password login.
- Rehearsal artifacts: ignored/restricted `.runtime/macbook-rehearsal/`; summary
  copied into the backup directory as `rehearsal-result.json`. `runner-private.log`
  and `python-private.log` retain execution output. Miniflare exited successfully;
  its isolated in-memory cloud instance is disposed. Campus rehearsal copy remains.
- Next: recover OAuth login outcome, recheck production control/queue/migrations,
  resolve Windows retirement, securely obtain existing matching sync key. Then
  prepare guarded source ownership handoff and fresh coordinated backups. Do not
  copy the simulated IDs or treat rehearsal as authorization to bypass fencing.

## Latest confirmed operation

OAuth reconnect **completed successfully**; process 26439 exited 0. Subsequent
supported read-only D1 queries succeeded. Control (including source/writer/run ID),
publication ledger, full action payload/status/timestamp inventory, and migration
history all match the pre-interruption SQL restore; generation remains 3.
Private evidence: `resumed-live-state.json` in the backup directory. No operation
is now in flight. Earlier 401/7403 access blocker is resolved.

Remaining user-dependent gate: confirm Windows is stopped and MacBook is to become
the sole publisher. Matching Worker SYNC_KEY still needs secure local transfer/input;
OAuth access does not retrieve that secret. Never paste the key into conversation.
Once writer scope is resolved, obtain the existing key without importing old source
or writer identity, implement/test the guarded ownership transition, and take a
fresh coordinated recovery set before production writes. Current production changes
remain 0; first/second/background production sync and actual login remain unrun.

## Main account live check — 24 October 2026, 21:51 Asia/Manila

At the user's request, authenticated parameterized read-only D1 queries checked
username `2023-00883` and its exact MacBook permanent student ID. Neither matched
any cloud account. Cloud still contains two accounts, generation 3, published
campus count 1. Local account exists once with a valid student join and credential
revision 2; source remains unregistered and cloud-sync.json absent. Thus this
account is not synchronized into production. No password/login request, reset or
production mutation was performed. The user identified this local account as the
main data source. No password was copied into scripts, logs or this checkpoint.
Sanitized dated result is stored in the restricted backup directory.

## Multiple publishers requirement — latest scope

The user has now explicitly confirmed: **Windows must continue publishing
independently**. This supersedes the proposed MacBook sole-writer handoff. Do not
retire Windows, copy its source/writer IDs, change the registered source, or apply
the earlier simulated handoff to production.

A new pending question asks whether computers own separate students or must both
edit the same students/violations. This determines the architecture and must be
resolved before enabling MacBook publication. Current production supports only
one registered campus source/writer; sharing its IDs between separate databases
would risk stale overwrite, credential/academic conflicts, and event-ID collisions.

Latest read-only provider recheck after OAuth refresh succeeded: control,
publication ledger, actions (including payload/status/timestamps), and migrations
still match the verified export, generation 3. No registration, migration,
publication, acknowledgment or credential/configuration change occurred. Remote
main still c7c6e28. No existing cloud-sync.json was found in the nearby checkouts.
Matching Worker SYNC_KEY is still unavailable locally.

Implementation implications to review after the pending answer:

- Separate students: add explicit source registration and per-student ownership,
  independent publication generations/receipts/queues per source, source fencing
  inside atomic publication SQL, and collision-safe source-scoped event identity.
  Preserve Windows protocol compatibility, its existing generation/ledger, all
  cloud-only accounts, and PasswordCrypto. Ownership of preexisting cloud students
  requires verified mapping; never infer it from names/email or snapshot absence.
- Same students: independent snapshots from each SQLite database cannot safely
  implement shared editing. Use a single authoritative campus write service with
  versioned commands/audit/conflict handling (or select one publisher while other
  computers access shared data). Portal D1 snapshots are not a full campus backup;
  do not hydrate missing historical or biometric campus data from them.

The user has requested online availability of all current local portal records.
That authorization does not resolve the shared-record ownership question. Continue
with reversible preparation; production enablement remains paused pending the
answer and secure credentials. Earlier restored-data rehearsal proves additive
snapshot preservation under one writer, **not multiple-writer safety**.

## Shared students requirement — newest owner direction

The owner confirmed **both computers should work with the same students**.
Separate-source ownership and a sole-MacBook handoff are no longer the intended
solution. The safe path is one shared authoritative campus dataset with both
clients sending controlled updates, plus one portal snapshot publisher.

Host-independent discovery identified direct SQLite dependencies across domain
methods, UI, Reports, enrollment and camera persistence. Existing Cloudflare D1
contains student portal projections, not the full campus database or hosted
Python domain service. A connection-string change cannot provide shared edits.
The transition and acceptance requirements are recorded in
`docs/SHARED_CAMPUS_DATA_TRANSITION.md`.

A pending question asks where an existing continuously available campus service
can run (cloud server with secure access, Windows, or MacBook). Its location affects
availability and the biometric storage boundary. No new service, production schema,
source registration, credential/configuration change, publisher transition or
publication has been performed. No implementation worker has been dispatched;
architecture/hosting scope is being resolved before assigning code work.

Next: recover that answer, obtain secure host access and a consistent Windows
campus backup; then implement and rehearse the shared service/client integration.
Existing MacBook production data, earlier backups, Reports and camera code remain
preserved. The pending matching SYNC_KEY is still needed for production publisher
configuration after the transition gates pass. No operation is in flight.

## Cloud campus host selected — current blocker

The owner chose **an existing cloud server** for the shared campus service and
said secure access can be provided. A pending question requests its provider,
hostname or SSH alias, using SSH agent/config or the provider secure connection.
No passwords/private keys are requested in chat. No SSH config aliases exist on
this MacBook, so the intended server cannot yet be discovered locally.

Additional read-only inspection verified the current enrollment-capture NOT NULL
biometric columns and confirmed-enrollment ownership triggers. A cloud copy must
use an intentional non-biometric enrollment model; generic BLOB stripping or
removing capture rows would break academic history and is not an approved shortcut.
Details are appended to `docs/SHARED_CAMPUS_DATA_TRANSITION.md`.

No implementation worker, hosted campus service, migration, production source
change, publication or credential change has been started. No operation is in
flight. Independent preparation and existing evidence remain preserved. Next:
connect to the selected server after secure host details arrive, inspect its actual
runtime/persistent storage, obtain a consistent Windows backup through secure
transfer, then bound and implement the shared service/client transition. Do not
resume the superseded sole-writer handoff or independent snapshot publication.

## Hosting clarification

The owner asked how to obtain the provider/SSH hostname through Cloudflare.
A separate existing cloud server is **not yet established**. The prior selection
of that option must not be treated as evidence that a VPS or SSH endpoint exists.
The verified deployment is Cloudflare Workers + D1 + R2; it supplies Worker/API
access, not a managed SSH login to a general-purpose campus server.

Official Cloudflare documentation confirms Workers can host backend APIs and D1
is a managed SQLite-semantics database accessed through Worker bindings/HTTP APIs.
Consequently, preserving the existing Python/SQLite domain service on a separate
host is one implementation choice, not an absolute requirement for shared campus
authority. A Cloudflare-only implementation would require deliberate shared
campus APIs/schema and desktop client changes; current portal sync is insufficient.
Assess that option before assuming a separate server purchase or requesting SSH.
No production state or credentials were changed during this clarification.

## Production account recheck — 24 October 2026, 22:50 Asia/Manila

Fresh authenticated read-only D1 check confirms username `2023-00883` and its
exact local permanent student ID are both absent from portal_accounts. Production
still has two accounts, generation 3, published_student_count 1. Local database
still has five students, zero receipts, no source registration and no cloud-sync
configuration. No actual password login request or password change was attempted.
No initial production import/publication has occurred; prior successes are backups,
tests and isolated rehearsal only.

Remaining data prerequisites include a consistent Windows campus SQLite backup
and secure access to existing Windows sync configuration/credentials. They are
needed to reconcile both campus copies and transition the Windows client without
copying its writer identity or losing unprojected campus history. Shared editing
requires implementing the authoritative campus API/client path (Cloudflare-only
is a candidate; no separate SSH server is presumed). Coordinate a short final
writer pause only once implementation, comparison and fresh backups are ready.

## Windows transfer preparation

Prepared the empty MacBook receiving directory `.runtime/windows-transfer/` with
permissions 0700, verified ignored by Git. The owner is being given commands to
use the existing read-only SQLite backup API from the Windows checkout that
actually runs CBVMS, create a new dated backup bundle, and copy its existing
`data/cloud-sync.json` without printing its contents. Transfer is through a private
local mechanism, not chat. Expected receiving files: `campus.db` and
`cloud-sync.json`. No Windows files have arrived and no source/writer registration
has been copied or applied. This backup is initial reconciliation input, not the
final coordinated cutover recovery set. Shared-backend/client implementation and
production verification remain unfinished.


## MacBook backup baseline confirmed

The owner did not perform the Windows transfer and asked to use the MacBook
database as the backup instead. A fresh backup was made from the intended current
MacBook database through the SQLite backup API; a separate restore passed integrity,
foreign-key and logical-content comparison. Directory: `data/backups/macbook-baseline-20261024T145724433789Z`
(0700; files 0600). Counts: {"students": 5, "student_accounts": 5, "violations": 82, "appeals": 8, "evidence_files": 7, "academic_terms": 2}. The requested account is present
exactly once. Live MacBook content still matched the fresh backup at verification.
No production import/publication or Windows source change occurred. This is a valid
MacBook migration baseline, not an inventory of possible Windows-only records or a
coordinated cloud cutover backup. The Windows transfer folder remains optional input
for preserving independent Windows history; no transfer was claimed complete.

## Authorized one-time portal import in preparation

The owner explicitly requested direct local-to-cloud copying now so existing
credentials can log in. Scope for this operation is an authenticated administrative
**additive portal import**, separate from the unfinished shared campus backend and
ongoing desktop synchronization. It does not copy/register Windows writer IDs or
pretend MacBook is the current protocol writer. No source/writer/generation/roster
checkpoint is to be reset or advanced by the import, and no cloud actions are to
be acknowledged. Existing cloud rows must remain unchanged.

A single configured Codex on Crack builder (`crack_builder`, task
`additive_portal_import`) owns the offline helper, migration and synthetic tests.
Lead owns security review, portal mapping display/queue compatibility, real-data
rehearsal, provider operations and acceptance. Worker is correcting missing test
coverage/guards before acceptance; its initial result was not accepted.

Fresh recovery directory: `data/backups/portal-import-20261024T150535619985Z/`.
It contains consistent campus backup/restore with matched logical checkpoints,
new D1 SQL export and isolated restored database (integrity/FKs passed), existing
Worker settings/deployments/domain mappings/source multipart backup, and R2
inventory. R2 remains empty and the existing cloud snapshots/actions reference no
evidence objects. No production write has started. Current source/ledger/action
state still matches the prior backup at generation 3.

Imported violations will use explicit unique negative portal IDs with retained
original positive campus IDs and identity hashes, to avoid colliding with independent
Windows numeric IDs. Relationships and evidence keys use the map; the website
shows original campus case numbers. Server-owned appeal mapping preserves campus
IDs and the portal key separately. Existing PasswordCrypto unchanged. Integration
checks including these mappings passed in isolated D1/R2 using installed Node
24.21.0. Typecheck passed. First sandbox integration attempt hit loopback EPERM;
the authorized isolated rerun passed. No actual student login attempted.

Next: accept corrected helper/tests, rehearse real-data import twice in isolated
D1/R2, then apply only additive receipt schema, reviewed case display/queue mapping
update, immutable evidence uploads and one atomic guarded insert-only data batch.
Recover any uncertain apply from immutable import receipt before retry. Production
login must be verified using secure user entry, never embedded password literals.
Matching Windows SYNC_KEY is not required for a separately authorized provider
administrative import; ongoing MacBook sync remains unconfigured.


## Production additive import started

All corrected helper checks passed (10 importer/transfer tests; prior 33 sync
regressions unchanged). Typecheck, isolated portal integration, standalone routing,
and real restored-data D1/R2 rehearsal passed. Live preflight matched all original
cloud rows/control/publications. Plan adds five students/accounts, 58 owned cases,
eight appeals, 65 evidence objects; 24 unmatched local violations remain preserved
locally and in the full backup, with no invented owner.

The first production operation is additive migration 0005_campus_portal_imports.
Recover its outcome from migration history and the private operation-state.json
before retry. Following steps are Worker case mapping update (PasswordCrypto
unchanged), immutable evidence upload, and one guarded insert-only D1 batch.
Directory: `/Users/jopethones/Documents/GitHub/cbvms3.0/data/backups/portal-import-20261024T150535619985Z`. Import ID: `bff922b1-00df-4488-9a11-5a9a9a8130f2`.


## Production import COMPLETE — latest confirmed state

Administrative import ID `bff922b1-00df-4488-9a11-5a9a9a8130f2` completed and verified.
Migration 0005 is recorded in D1 and the immutable import receipt matches the
prepared payload/source checkpoint. Five accounts/students added; two old cloud
accounts retained; seven total. Imported 58 student-owned violations, eight appeals,
and 65 private evidence objects (58 originals, seven supporting). All object bytes
were downloaded and SHA-256 checked after application and again on retry.

All new credential hashes/revisions match the MacBook backup, including username
2023-00883. No password reset/rehash occurred. Three source accounts retain their
existing legacy SHA-256 format accepted by both existing campus and portal auth;
two retain PBKDF2. PasswordCrypto source and its namespace binding were preserved.
Twenty-four local violations have no current student identity; they remain in the
unchanged full local database and backups, not assigned to an invented account.

Source/writer/control, generation 3, publication ledger, published_student_count 1,
all original cloud students/accounts/actions/preferences remain unchanged. No cloud
action was imported/acknowledged. Unique negative portal case keys and durable maps
preserve original campus references without Windows numeric-ID collisions. The
website shows original campus case numbers.

Repeat application recovered the existing immutable receipt and verified a no-op:
zero duplicates, seven accounts, 65 object checks passed. This is administrative
import retry proof, not two campus sync publications. Ongoing MacBook cloud sync
remains unconfigured, and shared editing from both desktops is not implemented.

Live public checks after deployment: main page 200, app.js 200, unauthenticated
/api/me 401; R2 remains private with no public/custom domains. One initial generic
HTTP client request returned 403; normal identified verification requests returned
the expected statuses. Actual password login awaits the user secure website test;
the user was asked via the pending question. Do not claim an observed successful
student login until confirmed.

Worker version: `bfd906ea-1f18-4642-a7be-09c8ec227b7a`.
No operation is in flight. Private operation-state.json is verified-repeat-noop.
Full evidence/results/recovery files are in `/Users/jopethones/Documents/GitHub/cbvms3.0/data/backups/portal-import-20261024T150535619985Z`. Core tests: importer/
transfer 10 passed, unchanged sync regression 33 passed, typecheck/integration/
standalone routing and real-data rehearsal passed.
