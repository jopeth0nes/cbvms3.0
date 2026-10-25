# Superadmin Recycle Bin

Current release status (2026-10-25): cloud migrations 0006 and 0007 deployed and
read back successfully; trusted superadmin authority registered; per-user dispatcher
installed with exit code 0. It currently reports `waiting_for_desktop_schema`.
Normal startup of the updated desktop is still required for local schema activation.
No real student lifecycle operation has been performed or verified in production.
See `STUDENT_ARCHIVE_CHECKPOINT.md` for evidence and limitations. This supersedes
the archive-only runbook.

## Local behavior and authorization

Only an authenticated superadmin can move students to the Recycle Bin, query its
entries/metadata or restore them. These checks occur inside backend transactions;
ordinary admins and students cannot bypass them by calling the lifecycle methods.
The superadmin dashboard sidebar includes a direct Recycle Bin destination. It
opens the existing student workspace in recycled-student mode; Student Management
opens active students. The workspace exposes bin records only after a persisted
staff session has been revalidated. It includes search, sorting, pagination,
academic details, actor, reason, time, lifecycle status and Restore.

Each confirmed change commits the same permanent student identity, archive flag,
monotonic version, immutable actor/reason audit, session invalidation and durable
outbox intent together. It never moves/deletes historical relational rows. Existing
credentials, biometrics and evidence remain under their existing protections.
No permanent-delete, purge or Empty Bin action exists.

Recycling blocks local login through the archive flag. Restoration changes that
flag only; it preserves independent `is_disabled` restrictions and all academic,
discipline and suspension state. It checks the account ID/username captured at
recycling and refuses missing/reassigned identities. It neither resets passwords
nor resurrects old sessions. Current term eligibility is computed by existing
rules, with no automatic enrollment. Recognition reloads after commit; queued
recognition/audio/notification/attendance work is fenced by lifecycle version.
Historical Reports remain available under their existing reporting permissions;
that is distinct from accessing the Recycle Bin.

## Cloud authority and delivery

This is a bounded administrative lifecycle channel, not general MacBook background
sync or shared Windows/MacBook editing. The trusted campus SQLite database owns
local superadmin authentication and immutable lifecycle intents. A separate
provider-admin dispatcher validates each intent against its audit and the actor's
current superadmin role, then sends it through the authenticated Cloudflare D1
provider API. Ordinary student and campus publication endpoints cannot submit
lifecycle commands. Provider OAuth is never loaded by the UI or exposed in a
student API. The workstation database/filesystem and provider account remain
trusted administrative boundaries; this does not defend against an OS user who
can edit the campus database or steal provider credentials.

The cloud maintains a source-bound allowlist of superadmin authorities. Commands
must match an enabled authority, immutable import receipt/source checkpoint,
permanent student ID, expected username, and next lifecycle version. Initial
registration uses provider administration plus read-only inspection of the trusted
local superadmin roster. A newly added/renamed superadmin requires explicit provider
authority provisioning before its queued operations can complete. Demotion causes
queued operations to fail closed and requires administrative reconciliation.

Migration 0006 remains the historical prepared revocation schema; additive 0007
replaces its operation path with `portal_lifecycle_operations` and
`portal_student_lifecycle`. Receipts are immutable. A single INSERT and triggers
atomically change access and remove cloud sessions. Recycle and restore both
require fresh login. Independent preexisting or subsequently imposed cloud blocks
remain in `access_blocked`; restoration never clears them. Legacy 0006 revocations,
if any, migrate with a conservative independent block rather than guessing their
previous access state.

Versions apply sequentially per identity. The dispatcher drains the durable queue
in version order. Offline recycle→restore therefore delivers both transitions in
order. Out-of-order newer commands wait for their predecessor; old matching
receipts acknowledge the original operation without replay. Reusing an operation
UUID for a different payload fails. After a timeout, the dispatcher reads the
receipt before retrying. Failure to save a local acknowledgment is recovered from
the same remote receipt after restart. No new UUID or version reset is needed.

Ordinary publications cannot replace lifecycle state or re-enable a recycled
identity. Imported snapshots/case mappings are retained for lifecycle-managed
identities; an unrelated Windows snapshot is not a shared-edit authority. Queued
submissions and evidence are preserved. Frozen queued actions are not silently
acknowledged while recycled. Pending work may require explicit reconciliation
once restored. No writer registration, generation, DNS, secret or PasswordCrypto
binding is changed by the lifecycle release.

## Installed dispatcher operations

The per-user launchd label is `com.cbvms.student-lifecycle`. It runs the trusted
`scripts/lifecycle_dispatcher.py` every 60 seconds while the Mac user is logged in.
The restricted config and aggregate health/status are in
`.runtime/lifecycle-dispatcher/`; credentials remain in the existing protected
Wrangler configuration. The process uses Wrangler's supported OAuth refresh and
logs no student IDs, passwords, hashes or tokens. An expired/revoked refresh grant
requires the provider administrator to reauthenticate; failures leave the queue
pending/failed, never falsely verified.

The dispatcher never initializes or migrates the live campus database. Before the
updated desktop is first launched, status can be `waiting_for_desktop_schema`.
Normal desktop startup performs its additive local schema upgrade; thereafter
intent creation and acknowledgment use that schema. This waiting state is not a
cloud outage or proof that a real lifecycle operation has been executed.

Use the UI's Reload to fetch the latest acknowledgment. A sleeping/logged-out/offline
Mac cannot deliver; local recycling remains effective and cloud completion stays
pending until delivery resumes. The existing Windows publication worker is not
repurposed. The old `revoke_imported_student.py` executable is retired; its provider
adapter remains reusable internally, but it cannot submit legacy archive commands.

## Verification and recovery

Before release, D1 was exported/restored and checked for integrity/FKs; Worker
source/settings/deployment/domain metadata was backed up; all 65 private evidence
objects were downloaded and checksum-verified. Applying 0006+0007 to the restored
production data preserved all students/accounts/sessions/actions/receipts/writer
controls/publications and created zero lifecycle entries. Restricted evidence is
located through `.runtime/recycle-release-path.txt`.

For any interrupted migration, inspect `d1_migrations` and actual schema before
retry. For a lifecycle interruption, inspect the immutable operation receipt and
current per-student version. Retain local outbox/audit rows and the original import
bundle. Never reset generations, replace a receipt, delete queue entries, or restore
a stale database over newer accepted actions. Prefer forward repair. Do not roll
back to archive-unaware desktop code after real lifecycle operations; cloud
migration 0007 must remain while lifecycle receipts exist.

`verify_campus_portal_import.py` understands verified recycle/restore state while
still checking original history, credentials, source receipt and negative case
mappings. New bulk-import preparation refuses recycled sources. Legitimate later
credential/profile changes require reconciliation rather than reimport/upsert.
The verifier does not replace separate R2 checksum checks.

No real student is recycled/restored to test deployment. Isolated integration and
native UI evidence are distinct from a production superadmin's first deliberate
use. Exact results and live release verification belong in the checkpoint.
