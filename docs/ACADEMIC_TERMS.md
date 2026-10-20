# Academic terms and semester enrollment

## Ownership and operation

The local SQLite database is authoritative. The portal is a versioned projection.
A single published campus calendar uses Asia/Manila dates. An inclusive end date
becomes an exclusive midnight boundary on the following day. There are no month
heuristics and changing a reporting filter never changes the operational term.
Existing term IDs and case assignments survive migration. Existing undated terms
remain drafts; historical enrollment is unverified until independently established.

`academic_terms` owns the calendar. `student_term_enrollments` has one record per
permanent student and term. Confirmed records reference a local-only, versioned
`enrollment_captures` record. `enrollment_revisions` preserves effective-time
academic/status snapshots; `academic_audit` records operator changes and rollover.
Capture confirmation and new identity/account creation are one transaction.
Repeated students use their existing identity and credentials. Upcoming enrollment
does not replace the current term's academic details. The recognizer keeps the
permanent anchor and latest valid capture without changing matching thresholds.
Capture provenance is not a claim of biometric liveness detection.

Violations, attendance events and unknown sightings resolve their occurrence time,
including delayed writes. Appeals, evidence and decisions retain their parent case.
Enrollment-review events retain the observed status and actual incident images;
resolution does not enroll a student or issue a strike. Cooldown and source receipts
suppress repeated observations. Capture images/encodings/provenance are never
exported to the portal. Accounts are not deactivated for missing term enrollment.

Strikes remain in their original term, with separate lifetime issued and valid
counts. A suspension retains its original dates/reason and receives an audited
effective termination at the next published term start. During a calendar gap,
automatic academic penalties are deferred; an existing suspension may end on its
scheduled date, or at the next term start if that occurs first. Old case decisions
remain available but cannot activate an old-term suspension in a new term.
Offline boundary reconciliation is idempotent and does not generate retrospective
threshold penalties. Date corrections preserve assignments and queue conflicts.

## Superadmin workflow

1. Open Settings → Academic Terms. Create the academic year and first, second and,
   if needed, summer terms with explicit start/end dates. Review and publish.
2. Use the enrollment wizard, select a current/upcoming published term, enter the
   existing permanent student ID (or create a new identity) and term academics,
   and complete all three fresh face captures. A demographic draft is not enrolled.
3. Verify the Academic Terms roster. Filter academic year, term, department,
   course, year and section; search, paginate and export the filtered roster.
4. Activation follows Manila dates automatically. No manual semester switch is
   needed. Review counts and the audit history after an offline interval.
5. Inspect enrollment warnings and original incident evidence. Resolve with an
   audit reason, and separately perform fresh enrollment where needed. Use audited
   enrollment correction for academic corrections/withdrawal.
6. Preview the impact before changing published dates; enter a reason and review
   calendar exceptions without silently moving records. Student history and portal
   term filters retain old cases, appeals and suspension termination reasons.

## Read-only preview and isolated rehearsal

Do not use ordinary application startup to inspect production: initialization can
migrate, process deadlines and start synchronization.

```sh
.venv/bin/python scripts/preview_term_migration.py --db /absolute/path/campus.db > /secure/path/term-preview.json
.venv/bin/python scripts/preview_term_migration.py --db /absolute/path/campus.db --apply-to-new-copy /secure/path/new-isolated-copy.db > /secure/path/copy-preview.json
```

The first command opens SQLite with `mode=ro` and `query_only`; it does not run app
initialization. The second uses SQLite backup (including committed WAL pages) and
migrates only a new destination, refusing an existing destination. Reports include
term IDs, counts, preserved/missing/invalid assignments, foreign keys, evidence
hashes and mismatches, and unresolved legacy students. Reports can contain record
identifiers: keep them in controlled storage, not source control. Never copy only
the main SQLite file while a writer may have uncheckpointed WAL.

Review the preview, compare evidence hashes/counts/IDs, check foreign keys, run the
rehearsal twice, restore a separate copy and verify it before approving production.
No production preview or migration has been run by this implementation session.

## Production deployment order — requires explicit approval

1. Pause desktop writers and outbound/inbound sync; drain or account for queued
   cloud actions and preserve receipts, watermarks and publication generations.
2. Take consistent SQLite, cloud D1 and relevant R2/object-store backups. Record
   the app/server versions. Verify restoration in an isolated environment.
3. Apply cloud migration `0003_academic_snapshots.sql`, then deploy the portal
   server supporting snapshot schema 3 and minimum writer version 3. Protocol 2
   action/retry/generation protections remain in use. Older snapshots are rejected.
4. Apply/rehearse the additive local migration using the approved backup; install
   the desktop release while sync is disabled. Configure/publish the approved
   calendar. Do not invent historical enrollments or mass-assign legacy cases.
5. Verify fresh capture on the actual camera, permanent identity matching,
   upcoming enrollment, alarms, roster/history and original evidence. Compare
   production counts/hashes with the approved preview and inspect exceptions.
6. Publish a controlled schema-3 snapshot, verify authorized student histories,
   queued action parent terms and evidence, then resume normal writers/sync.

## Rollback and recovery

Keep writers and sync paused on any inconsistency. Preserve the failed copies and
logs. Prefer a forward repair of additive schema. If restoration is required,
restore a coordinated local/cloud/object-store backup set and its matching server
and desktop versions, receipts and generations; verify before restarting. Restoring
only SQLite while the portal has advanced can replay or lose actions. Do not lower
the minimum writer version simply to let an old desktop overwrite new histories.
Never delete or renumber records, reset strikes, or remove incident evidence as a
recovery shortcut. Reconcile offline boundaries only after the calendar is checked.

Pending production approval is specifically for backup/access, production preview,
cloud migration/deployment, desktop migration/install, calendar publication and
resumption of production sync. Local fixtures do not establish camera hardware
quality or production restoration readiness.

## Local verification record

The consolidated Python regression run passed 367 tests (53.036 seconds).
The final term/migration suite passed 20 tests (6.980 seconds). Native reporting
and calendar checks passed 9 tests (28.893 seconds); a separate native evidence
and local HTTP portal run passed 16 tests (27.023 seconds), including overlapping
calendar checks. Portal typecheck, production build and isolated D1/R2 HTTP
integration tests passed. Python compilation and diff whitespace checks passed.

These checks use synthetic identities, controlled clocks and disposable SQLite
files. They cover camera preview/switching logic, not physical camera quality or
speaker output. No whole-repository discovery result or production restore is
claimed. See `ACADEMIC_TERMS_CHECKPOINT.md` for test logs and recovery context.
