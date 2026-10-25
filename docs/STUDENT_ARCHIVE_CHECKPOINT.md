## Dashboard access follow-up — 2026-10-25

Added a direct Recycle Bin sidebar destination for superadmins, opening the shared
student workspace in bin mode. Ordinary admins have no sidebar entry and navigation
is guarded; backend session authorization remains authoritative. Returning to Student
Management opens active students. No cloud or production data operations performed.
Changed ui/dashboard.py, ui/enrollment.py, tests/test_student_management_ui.py and
these runbook/checkpoint notes. Focused native test passed (1 test, 1.661s), including
direct bin opening, restoration, return to active students and ordinary-admin controls;
log `.runtime/archive-verification/recycle-dashboard-ui.txt`. py_compile and
`git diff --check` passed. Branch/HEAD and deployment status below remain applicable.

# Latest recovery checkpoint — 2026-10-25

This section supersedes all earlier status/authorization statements below.
Branch `main`, HEAD `d43ff56905fa50a108bfba6d85d74c916853cea3`; feature work remains
uncommitted. Existing camera, Reports, semester, import and writer behavior retained.

## Last confirmed operations

- Authorized production D1 migrations `0006_student_revocations.sql` and
  `0007_student_lifecycle.sql` applied successfully and read back in d1_migrations.
- Production domain rows exactly match the verified pre-release backup, including
  students/accounts/sessions/actions/preferences/import receipts/writer controls/
  publications. Lifecycle receipts=0, lifecycle states=0, FK errors=0.
- One verified source-bound superadmin authority provisioned and read back.
- Worker deployment/settings/domains unchanged. No DNS/secrets/bindings replaced.
- `com.cbvms.student-lifecycle` launch agent installed, interval 60 seconds,
  last exit 0. Restricted status reports `waiting_for_desktop_schema` with zero
  verified/failed/deferred operations. No student/session lifecycle test in production.
- No uncertain or in-flight release operation remains. Service remains periodic.

## Evidence

Restricted release directory: `.runtime/recycle-release-path.txt` resolves it.
It contains `backup-verification.json`, `rehearsal-verification.json`,
`migration-apply.log`, `preflight.json`, `authority-verification.json`,
`post-deployment-verification.json`, `operation-state.json`, restored D1 export,
Worker metadata/source and 65 checksum-verified private R2 object backups.
Dispatcher aggregate status/logs: `.runtime/lifecycle-dispatcher/` (private).

Tests on disposable data:
- Local archive/attendance/live pipeline/password setup/report snapshot: 78 passed.
- Local archive suite after test rename: 12 passed.
- Cloud lifecycle/delivery/legacy archive/import/transfer: 25 passed, log
  `.runtime/archive-verification/lifecycle-cloud-final.txt`.
- Latest lifecycle+actual local dispatcher integration: 8 passed, log
  `.runtime/archive-verification/lifecycle-final-integration.txt`.
- Isolated real Miniflare D1/R2/Worker test passed: recycle and revoked HTTP session,
  denied login, unrelated account login, restore/fresh login, old session denial,
  stale command and publication fences before/after restore, preserved pending
  actions/evidence/mappings. Tool session 77989 exited 0.
- Focused native lifecycle test passed for superadmin and ordinary admin roles.
- Broader native UI suite has one form-widget test failure (`_entries` 0 vs 4);
  isolated rerun aborted Tk (134). Do not claim full native-suite success.
- Four earlier student-management failures reproduced on pristine base; prior
  archived-only evidence remains below. Compileall and diff whitespace checks passed.

## Activation, limitations and recovery

Next concrete action: normally launch the updated desktop from this working tree;
its regular startup applies the additive local schema. Then inspect dispatcher
status for `ready`. Do not initialize the live database or recycle a real student
just to test. First deliberate production lifecycle use remains unverified.
Local/cloud completion is separate; use Reload to retrieve acknowledgment. A
logged-out/asleep/offline Mac cannot deliver. Expired provider refresh grants need
administrator reauthentication. New/renamed superadmins need explicit cloud
allowlist provisioning. Non-imported identities fail ownership checks and remain
pending rather than claiming cloud completion.

For interruption: inspect actual migration history or the immutable operation UUID
receipt before retrying. Retain queue/audit/import bundle and versions. The service
recovers pending/failed operations with the same UUID; timeout does not mean rollback.
Never reset generations, replace receipts, restore a stale database, or roll back
to lifecycle-unaware code after real operations. See STUDENT_RECYCLE_BIN.md.

## Modified files at this checkpoint

```
 M auth/auth_manager.py
 M auth/login.py
 M core/attendance.py
 M core/live_pipeline.py
 M core/live_state.py
 M core/portal_import.py
 M core/recognizer.py
 M database/attendance.py
 M database/db_manager.py
 M database/models.py
 M database/student_credentials.py
 M database/student_management.py
 M database/term_enrollment.py
 M docs/CAMPUS_PORTAL_IMPORT.md
 M main.py
 M scripts/prepare_cloud_portal_transfer.py
 M tests/test_attendance.py
 M tests/test_live_pipeline.py
 M tests/test_portal_import.py
 M tests/test_recognizer_identity.py
 M tests/test_student_management_ui.py
 M ui/dashboard.py
 M ui/enrollment.py
?? core/archive_cloud.py
?? core/student_lifecycle_cloud.py
?? docs/STUDENT_ARCHIVAL.md
?? docs/STUDENT_ARCHIVE_CHECKPOINT.md
?? docs/STUDENT_RECYCLE_BIN.md
?? scripts/install_lifecycle_dispatcher.py
?? scripts/lifecycle_dispatcher.py
?? scripts/revoke_imported_student.py
?? scripts/verify_campus_portal_import.py
?? sites/student-portal/drizzle/0006_student_revocations.sql
?? sites/student-portal/drizzle/0007_student_lifecycle.sql
?? sites/student-portal/tests/archive.mjs
?? tests/test_archive_cloud.py
?? tests/test_lifecycle_delivery.py
?? tests/test_student_archive.py
?? tests/test_student_lifecycle_cloud.py
```

---

## Earlier historical checkpoints (superseded where inconsistent)

# Student archival recovery checkpoint

Updated 25 October 2026 (Asia/Manila). Work is uncommitted on `main` at
`d43ff56905fa50a108bfba6d85d74c916853cea3`; remote HEAD matched at initial check.

## Confirmed operations and production boundary

No production archive, deletion, revocation, migration or deployment has been
attempted. No production write has an uncertain outcome. Read-only provider
checks used the existing Wrangler OAuth session (refreshed via `whoami`).
No background sync configuration was created. No real student was used for a
mutation test.

Fresh read-only checks: five local students; SQLite integrity OK, no FK errors;
seven active cloud students/accounts; zero active cloud sessions; one queued and
one applied password action; one immutable import receipt with 58 negative case
mappings. All five local IDs belong to the receipt and have cloud accounts.
Imported rows, credentials and case mappings match the original restricted import
bundle. Writer controls and publication ledger match the original import
checkpoint, at generation 3. Cloud migrations end at 0005. MacBook sync config
is absent. Provider deployment metadata was readable.

Sanitized audit script: ignored `.runtime/inspect_archive_state.py`. It reads
live SQLite in read-only mode and emits aggregate counts/comparison booleans.
Original restricted evidence remains under the directory referenced by
`.runtime/active-portal-import-path.txt`; never publish those files.

## Reproduction and implementation

Disposable initialized SQLite reproduced failure at `DELETE FROM students WHERE
id=?`, after account deletion, due to `student_term_enrollments.student_id` →
`students.student_id` NO ACTION. Rollback restored account and student. Actual
local schema also has restrictive references from enrollment captures, revisions,
and reviews. No live delete was attempted.

Local implementation replaces Delete Selected with Archive Selected; archive
metadata, account disable/session invalidation, audit and durable outbox are one
transaction. Backend accepts persisted authenticated staff-session proof. Active
management excludes archives; Reports retain identity/history. Recognition,
attendance, violation and premises-write guards reject archived identities.
Permanent deletion is deliberately unavailable: local dependency absence cannot
prove absence of an imported cloud identity.

Cloud implementation is a provider-admin-only reconciliation command, no new
student HTTP route or SYNC_KEY sharing. Migration 0006 adds immutable revocation
receipts, ownership/version validation and durable reactivation/history/action
fences. A single INSERT and triggers atomically revoke access/sessions. Recovery
checks the same operation ID before retry; command files are persisted before
submission. Import verification accepts only the authorized active-bit difference
and retains snapshot/credential/mapping checks. All cloud changes are PREPARED,
NOT DEPLOYED.

## Final verification evidence

All following evidence uses disposable data; no production mutation was tested.

- Builder: `.venv/bin/python -m unittest tests.test_student_archive
  tests.test_live_pipeline tests.test_attendance tests.test_attendance_pipeline
  tests.test_attendance_writer tests.test_password_setup -v`: **70 passed**.
  Includes session issuance after archive denial, authenticated actor/credential
  invalidation, transaction rollback, stale selection/retry, and historical
  capture/revision/review/appeal/evidence/strike/suspension preservation. Semester
  Enrollment Roster report rows compare equal before/after archive.
- `.venv/bin/python -m unittest tests.test_archive_cloud -v`: **6 passed**,
  including offline/restart recovery, timeout after remote commit, failure to
  persist the local acknowledgment, receipt replacement denial and queued actions.
- `tests.test_portal_import`: **9 passed**; `tests.test_cloud_portal_transfer`:
  **2 passed**. Archive-aware import retry remains strict about history/mappings.
- `.venv/bin/python -m unittest tests.test_report_snapshot tests.test_report_csv
  -v`: **18 passed**.
- `.venv/bin/python -m unittest tests.test_recognizer_identity -v`: **18 passed**,
  including removing archived anchor/capture embeddings from the actual gallery.
- `.venv/bin/python -m unittest tests.test_password_setup_web -v`: **15 passed**
  against disposable SQLite and real localhost HTTP.
- Native widget test (builder): `.venv/bin/python -m unittest
  tests.test_student_management_ui.StudentManagementUITests.test_summary_tracks_selection_filters_status_and_deleted_student
  -v`: **1 passed** with synthetic students and real CustomTkinter controls.
  Covers cancellation, confirmation, active/archived counts, summary clearing,
  cloud pending status, persistence and recognizer reload. Sandbox Tk root creation
  aborted (exit 134); authorized native rerun passed. This is automated native UI
  evidence, not physical camera/FPS or a production-user login test.
- `node tests/archive.mjs` from `sites/student-portal`: **passed** against real
  isolated Miniflare D1/R2 and Worker HTTP routes after final migration changes.
  Covers wrong imported owner, session invalidation/login denial, unrelated login,
  later publication, pending action/receipt/mapping/evidence preservation and FKs.
  Initial sandbox loopback failed EPERM; authorized isolated reruns passed.
- Python compilation, CLI help and `git diff --check`: passed.

`tests.test_student_management` has **4 existing failures among 15 tests** in both
this tree and a pristine `git archive d43ff56` under `/private/tmp`. Identical
failures: camera status/eligibility, entry-only status→new violation, existing
appeal/strike preservation, and registration/history. These older fixtures omit
published-term enrollment required by existing eligibility rules. They were not
silently changed. Raw lead logs and the final file manifest are in ignored
`.runtime/archive-verification/`; builder evidence above comes from its reported
exact commands/results. No broad full-suite or physical-camera performance claim.

## Final implementation/release state

Local session-race and queued monitoring gaps are closed. Staff proof is bound to
current credentials, and student account/session checks reject disabled/archived
identities inside the transaction. Deferred audio/notifications and enrollment
review writes also reject archived identities. Unknown-person handling and
inference rates are unchanged.

Cloud migration 0006, provider-admin CLI, durable recovery and import verifier are
implemented and fixture-tested, **not deployed**. No production archive/revocation
is pending or uncertain from this work. No test process or provider write remains
in flight. Current live state was rechecked after interruption/OAuth refresh and
still matches the confirmed state above. Private R2 bytes were not re-downloaded
in this task; the original 65-object checksum verification is historical evidence.

Permanent deletion remains disabled: absence of local dependencies does not prove
absence of cloud identity. Restoration is not implemented. Cloud revocation is a
separate explicit provider-admin reconciliation, not automatic desktop sync.
After local archive the UI must remain pending until that remote receipt is
verified; there is no end-to-end production protection claim.

## Changed files

- Local auth/session wiring: `auth/auth_manager.py`, `auth/login.py`, `main.py`,
  `ui/dashboard.py`.
- Archive/UI/storage/monitoring: `ui/enrollment.py`, `database/db_manager.py`,
  `database/models.py`, `database/student_credentials.py`, `database/attendance.py`,
  `database/student_management.py`, `database/term_enrollment.py`,
  `core/recognizer.py`, `core/live_pipeline.py`.
- Cloud: `core/archive_cloud.py`, `core/portal_import.py`,
  `scripts/revoke_imported_student.py`, `scripts/verify_campus_portal_import.py`,
  `scripts/prepare_cloud_portal_transfer.py`,
  `sites/student-portal/drizzle/0006_student_revocations.sql`.
- Tests: `tests/test_student_archive.py`, `tests/test_archive_cloud.py`,
  `tests/test_portal_import.py`, `tests/test_live_pipeline.py`,
  `tests/test_recognizer_identity.py`, `tests/test_student_management_ui.py`,
  `sites/student-portal/tests/archive.mjs`.
- Documentation: this checkpoint, `docs/STUDENT_ARCHIVAL.md`, and the archival
  follow-up in `docs/CAMPUS_PORTAL_IMPORT.md`.

All remain uncommitted on main at d43ff56. No unrelated changes were present at the
start or reverted during this task.

## Next concrete action and recovery

For a separately authorized production release, follow `STUDENT_ARCHIVAL.md`:
back up/restore-check current D1/R2 and settings, verify exactly migration 0006 is
pending, apply it, and read back migration history/schema/controls. The user only
authorized implementation/testing here; no production migration was inferred.
Do not revoke a real student to test deployment. Actual authorized future archives
can then be reconciled with the provider-admin CLI and original import bundle.

Preserve outbox IDs and restricted command files. Read the remote revocation
receipt before any retry; never assume timeout means rollback. Never remove the
revocation ledger, import receipt or case mappings, reset generations, or restore
an old production database. A verified receipt can repair a missing local
acknowledgment. Restoration requires a separate authorized version-checked design.
Do not roll back to archive-unaware desktop code after real archival operations.

## Recycle Bin requirement supersedes archival-only release — recovery update

User explicitly authorizes deployment of the tested Recycle Bin lifecycle feature.
Current branch remains main/d43ff56, all prior and new work uncommitted. No cloud
migration or lifecycle write has been started yet; there is no uncertain production
write to replay. Last live read still had migrations through 0005, seven active
accounts, unchanged generation 3/writer/receipt state.

New prepared files: core/student_lifecycle_cloud.py,
scripts/lifecycle_dispatcher.py, scripts/install_lifecycle_dispatcher.py,
sites/student-portal/drizzle/0007_student_lifecycle.sql,
tests/test_student_lifecycle_cloud.py, tests/test_lifecycle_delivery.py.
Local builder is extending archive into superadmin-only recycle/restore, preserving
independent account restrictions and adding lifecycle versions to stale monitor
guards (including core/live_state.py). UI/backend tests remain in progress.

Restricted recovery directory is referenced by `.runtime/recycle-release-path.txt`.
D1 export restored with integrity/FKs passing. Worker source/settings/deployments
and domains saved; all 65 private R2 objects downloaded, locally read back and
checksum-verified. `backup-verification.json` and `rehearsal-verification.json`
record evidence. Migration 0006+0007 rehearsal on the restored real data preserved
all student/account/session/action/preference/receipt/writer/publication rows and
created zero lifecycle states. No production data was changed.

Six new SQLite cloud lifecycle tests passed (ordering, identity/authority denial,
independent blocks before/during recycling, timeout/idempotence, stale publication
and receipt protection). Updated real Miniflare D1/R2 integration passed recycle,
restore with fresh login, old-session denial, delayed recycle denial, unrelated
account/action/evidence/mapping preservation. Latest local→cloud dispatcher test
had one test-call signature error after the builder secured get_archive_operation;
that call was corrected to pass the superadmin token and is being rerun. This was
a fixture failure, not a production write.

Next: finish local/native and dispatcher tests; verify current remote migrations
again, then apply authorized 0006+0007 in order and read back schema/domain state.
Register the trusted source's superadmin authority metadata; install/verify the
separate provider dispatcher (no OAuth in UI), with periodic durable outbox
recovery. Never initialize/mutate live students merely to run a test. Update this
checkpoint immediately before any production migration begins.

## Authorized deployment starting — 2026-10-25

Preflight reconfirmed migrations through 0005 only and all backed-up domain rows unchanged. Local scoped tests 78 passed; focused native lifecycle test passed for both roles; broader UI suite has an unresolved form-widget failure and isolated Tk abort. Cloud D1/R2 integration passed including publication after restore. Applying 0006 then 0007 next. If interrupted, inspect d1_migrations before retrying. No lifecycle operations submitted.
