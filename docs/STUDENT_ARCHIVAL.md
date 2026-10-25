# Superseded archival-only design

This document records the earlier implementation and is not the current runbook.
Superadmin-only recycling and restoration supersede its admin permissions and
archive-only CLI. Migrations 0006 and 0007 are now deployed. Use
[STUDENT_RECYCLE_BIN.md](STUDENT_RECYCLE_BIN.md) for current behavior and recovery.

# Student Management archival and cloud revocation

Implementation is complete and fixture-tested; production D1 migration 0006 has not
been applied. An archived local student can still use the cloud portal until the
provider-administrative revocation has been verified. This is not shared desktop
editing or background MacBook synchronization.

## Behavior

Archive Selected captures the selected database key, permanent student ID and
archive version before confirmation. A current authenticated admin/superadmin
session is revalidated inside the write transaction. The transaction archives the
identity, disables its local account, invalidates sessions, records the actor in
an audit event, and creates a durable cloud-operation UUID. History, student
standing and academic eligibility are not rewritten. Failed transactions roll
back all of those changes.

The active-management list excludes archives. Show Archived requires a current
staff session and displays pending, verified or failed cloud status. Reload reads
updated provider-reconciliation results. The recognition gallery reloads off the
Tk thread; database guards reject stale observations after archive commit.
Historical reports retain the identity and historical facts.

Permanent deletion is unavailable, including for apparently unused local rows:
absence of local history cannot prove absence of an imported/cloud account.
Nothing silently cascades or disables foreign keys. Restoration is also not
implemented; it requires a separate authorized operation with a version check.

## Cloud administration and deployment boundary

`0006_student_revocations.sql` is additive and compatible with the current Worker.
No Worker redeployment, credential change, new student HTTP route, Windows writer
handoff or generation reset is required. Provider authorization is held only by
the separately invoked administrative CLI, never by Student Management or the
student portal. The existing provider OAuth session can administer D1; it is not
a student/staff session or the campus SYNC_KEY.

Before applying 0006 in an authorized production release, export D1 and back up
Worker settings and private R2 inventory/objects using the existing runbook.
Restore D1 in isolation and verify integrity, foreign keys, import receipt,
negative case mappings, writer controls, pending actions and object checksums.
Review pending migrations: the expected next migration is exactly 0006. Apply
using the existing `sites/student-portal/cloudflare/wrangler.jsonc` D1 migration
configuration, then read migration history and sqlite_master to verify the table
and all triggers. If the command times out, read migration history/schema before
retrying. Do not test production with a real student's revocation.

The provider CLI takes an existing local archive operation; it cannot select a
cloud-only or Windows-owned identity merely by student number. It validates the
restricted import bundle, immutable receipt, source checkpoint, username and
credential revision. D1 repeats ownership/version checks inside the single
revocation INSERT. Trigger execution disables access and removes sessions
atomically. It preserves snapshots, account credentials, pending submissions,
import receipts, case mappings and evidence. The independent revocation ledger
prevents stale publications, account upserts or receipt replacements from undoing
revocation. Requests that authenticate immediately before revocation cannot add
new actions after its commit. Existing queued actions remain queued for explicit
future reconciliation, not silently acknowledged or discarded.

## Reconcile one authorized archive

Run from the repository on the provider administrator's machine, after migration
0006 is verified. Use a restricted local directory for the command file (mode
0700); the CLI creates the file with mode 0600 and stores no passwords/tokens in
it. Obtain the operation UUID from `student_archive_operations`, using a read-only
local connection. Arguments below are placeholders, not real student data.

```sh
.venv/bin/python scripts/revoke_imported_student.py \
  --local-db /absolute/path/to/campus.db \
  --operation-id EXISTING-LOCAL-OPERATION-UUID \
  --import-bundle /restricted/original-import/prepared/bundle.json \
  --command-file /restricted/archive/operation.json \
  --account CLOUDFLARE-ACCOUNT-ID \
  --cloud-database D1-DATABASE-UUID \
  --provider-credentials /restricted/wrangler/config/default.toml
```

Without `--apply`, this performs read-only preflight. An explicitly authorized
application uses the same arguments with `--apply`. The command file is persisted
before the write. Reuse the same file and operation UUID on every retry. A lost
response is resolved by reading the remote operation receipt and checking inactive
status and zero sessions; the local state becomes verified only after those
checks. Offline/ambiguous outcomes remain failed/unverified, and retry remains
available. The UI never performs provider calls and does not block Tk on network
work. A verified local result cannot be downgraded by a late failure.

Provider credential expiry requires refreshing the existing authorized provider
session; never copy provider credentials into the application or use Windows sync
credentials as a substitute. Identity or revision conflicts require reconciliation
against the preserved command/receipt, not a fresh UUID or an edited version.

## Import verification after archive and recovery

Use `scripts/verify_campus_portal_import.py --bundle RESTRICTED_BUNDLE
--cloud-restore CURRENT_ISOLATED_D1_RESTORE` for row/receipt/mapping verification.
It accepts an inactive imported identity only when the matching revocation receipt
is present. Snapshot and credential checks remain strict; unrelated changes need
reconciliation. It does not verify R2 bytes; retain the existing object checksum
verification separately. The old private one-time import script's assumption that
the current campus file and every active bit still equal the original import is
no longer suitable after authorized archival. Do not rerun its apply mode to
repair that expected difference. New import preparation refuses archived sources.

Do not roll back production by restoring an old SQLite/D1 file, deleting the
outbox/receipts, dropping revocation triggers, or re-enabling accounts. Preserve
all operation files and reconcile forward. Rolling back application code before
any archive is possible with the additive columns/tables retained; after archives,
old code would ignore local archive flags, so retain the archive-aware build.
Cloud restoration/unarchive needs its own reviewed, authorized, versioned design.

See `STUDENT_ARCHIVE_CHECKPOINT.md` for exact test evidence, the current production
state and unfinished release work.
