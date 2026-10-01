# Appeal workflow cleanup

The former workflow required administrative confirmation and an active strike before accepting an appeal. The student cards and notifications also gated their buttons on confirmation. Confirmation created the strike and started a second five-day window. A temporary database running the original checkout reproduced `pending_review`, `can_appeal=False`, `reason=not_confirmed`, and `appeal_deadline=None` immediately after detection.

## Implemented lifecycle

| Event | Violation | Appeal | Active strike |
| --- | --- | --- | --- |
| Qualifying detection published | Pending | Available for 120 hours | None |
| Administrative classification | Confirmed | Original deadline unchanged | None |
| Timely submission | Pending/confirmed | Pending admin decision | None, including after deadline |
| Approval | Resolved | Approved | None |
| Rejection | Confirmed | Rejected | Exactly one |
| Deadline passed without appeal | Auto confirmed | Expired | Exactly one |
| Dismissal | Dismissed | Unavailable | None |

`log_violation` captures publication time, deadline, original evidence, owner ID, academic term, and a persistent notification in one write transaction. Detection time remains separate from publication time. UTC timestamps use the existing SQLite representation; publication/deadline precision is seconds and the interval is exactly 120 hours. Student and admin appeal displays share the host's configured local timezone formatter. Login time and administrative confirmation never restart the clock.

Submission obtains `BEGIN IMMEDIATE` before checking the authoritative clock, ownership, existing appeal, final/resolved state, and deadline. Equality is accepted; any later instant is rejected. The shared eligibility rule also drives portal snapshots. Explanation length remains 20–1,000 characters. Backend image decoding verifies actual JPG/JPEG, PNG, or BMP contents, with the existing 10 MB limit. Appeal and supporting image commit together. The unique violation-to-appeal and violation-to-strike constraints protect concurrent retries.

Expiry uses a read-only due check followed by a locked recheck and transition; it runs at startup, during normal dashboard operation, and in portal background refreshes. A pending appeal excludes expiry. Administrator decisions require a known administrator and a nonempty reason, then atomically save outcome, actual actor/time, strike effect, decision history, and persistent notices. A competing request reads the recorded outcome. The legacy `log_decision` API delegates to this same boundary instead of appending independent decisions.

Three finalized strikes in one category and semester retain the existing action-required threshold. Office-imposed suspensions, office clearance, unrelated causes, and live-monitor warnings remain separate. Threshold recomputation does not lift a suspension.

## Student and administrator experience

Student violations expose Appeal immediately, with a deadline/countdown and no-strike explanation. Forms retain drafts on validation/storage errors, display the original detection image, and require both explanation and supporting image. Duplicate submissions are disabled during save. View Appeal/View Decision targets the submitted case; My Appeals includes the explanation, supporting image viewer, timestamps, status, administrator, and reason. Owner-scoped lazy image reads and generation checks keep database work off Tk and prevent stale case/student responses. AI remains advisory and cannot change a human decision or create a strike.

The admin sidebar has a dedicated Appeals item and pending count, independent of unread alerts. The new asynchronous inbox defaults to Pending, supports status/name/ID filtering and pagination, and opens a full-width case page. Original and supporting evidence appear side by side with enlargement. The page includes identity, clearly labeled dates, a readable explanation, a required reason, and fixed decision controls showing each consequence. Back preserves search, filter, page, and drafts. Camera controls, the oversized welcome banner, and Activity & Alerts are removed from this workspace. Small windows scroll the details area while retaining decision controls. Records and alert links route to this workspace; the embedded Records decision editor was removed.

## Migration preview and historical reconciliation

The production database was inspected read-only. No production violations, appeals, strikes, suspensions, or evidence were modified during development/testing.

| Before migration | Count |
| --- | ---: |
| Pending review | 8 |
| Legacy unreviewed | 53 |
| Auto confirmed | 11 |
| Historically reviewed | 1 |
| Appeals: pending / approved / rejected | 1 / 1 / 1 |
| Active strikes | 4 |
| Suspensions | 0 |

`appeals_publication_v1` records its execution time and pre-migration preview. On a SQLite backup, 42 identified disciplinary records received a fresh 120-hour window and explicit migration origin. The other 19 pending historical records are unmapped `unknown_person` records and remain outside student discipline. Already decided appeals, evidence, and finalized history were preserved. Reinitialization did not reset deadlines or duplicate notifications. No automatic historical strike reconciliation occurs.

One conflict requires a historical decision: violation **65**, student **2023-00883**, pending appeal, active strike **3**. Proposed reconciliation: deactivate that strike with reason `pending_appeal_reconciliation`, append administrator/reason/time to `discipline_reconciliations`, recompute threshold eligibility, and preserve all office-clearance suspensions. Approval leaves the strike inactive; rejection reactivates that same ledger row exactly once. `reconcile_pending_appeal_strike` is explicit and is never invoked by startup/refresh. The disposable-copy simulation reduced active strikes from **4 to 3**, left all three appeals and historical decisions intact, and removed the conflict. This has **not** been applied to the production database.

Reproduce the read-only source inspection and disposable migration/reconciliation preview:

```sh
.venv/bin/python scripts/preview_appeal_migration.py data/cbvms.db
```

The full machine-readable result is in `APPEAL_MIGRATION_PREVIEW.json`. Normal application initialization applies the safe versioned publication migration once; the historical conflict stays explicitly flagged until reconciliation is authorized.

## Validation

Final verification results:

- `.venv/bin/python -m unittest discover -s tests -q`: **360 passed in 59.778 seconds**, with native macOS window-server access.
- `.venv/bin/python -m unittest tests.test_violation_workflow tests.test_camera_violation_integration -q`: **29 passed in 0.799 seconds**, including the final resolved-case guard added after the full run. This focused run overlaps the full suite; the counts are not additive.
- Native workspace tests covered 1050×720 at 100%, 1200×900 and 900×700 at 125%, and a 590×480 fallback. Decision controls remained within the viewport. Tests also verified evidence enlargement, exact-case selection, competing decisions, required reasons, error retry, stale-result rejection, and inbox search/page/scroll restoration.
- Native portal tests covered immediate Appeal visibility, invalid-image draft preservation, asynchronous submission, notification navigation, admin outcomes, lazy evidence, and existing navigation/responsiveness regressions.
- The full run retained the existing face recognition, face capture, torso association, model concurrency, camera switching, live persistence, suspended-student warning, and portal responsiveness tests. The exact `2023-00883` synthetic LiveProcessor assessment was separately verified against real disposable SQLite persistence.
- The migration copy was repeatable, preserved historical decisions, and simulated the proposed reconciliation without modifying its read-only source. Final source inspection still showed 73 violations, 3 appeals, 5 ledger rows (4 active), no suspensions, and no `workflow_migrations` table.
- `py_compile` and `git diff --check` passed.

All workflow data created during testing is disposable. Model tests use synthetic frames/model doubles where appropriate; native Tk tests exercise actual widgets/event loops. These tests do not establish physical-camera or live multi-person accuracy. No production migration or historical disciplinary reconciliation was executed.

## Manual walkthrough

1. Start the updated application on an isolated database copy; sign in as the fixture student `2023-00883` and an administrator in separate sessions.
2. Use a qualifying recognized Live Monitor detection. Confirm the correct student's My Violations updates with original evidence, a five-day deadline, Appeal, and zero new strikes.
3. Open Appeal, inspect the camera image, enter 20–1,000 characters, and attach a valid image. Try a corrupt image first: the draft must remain, with a clear error. Submit valid evidence once; View Appeal and Pending Admin Decision replace Appeal.
4. Open admin Appeals. Verify the pending count independently from the unread notification count. Search the student ID, open the case, inspect both images, and enter a decision reason.
5. Approve to resolve with no active strike, or reject to finalize exactly one strike. Return to My Appeals/Notifications and verify the same outcome, reason, administrator, and local timestamps.
6. On disposable data with a controlled clock, advance past the deadline. A timely pending appeal remains protected; an unappealed record gains one strike. Restart and refresh repeatedly to confirm no duplicate effects. A third finalized same-category strike triggers office action; existing suspension clearance remains explicit.

## Application launch — October 1, 2026

The complete desktop application was started at 18:18 Asia/Manila using `.venv/bin/python -u main.py`. A SQLite backup was saved first to `data/backups/cbvms-before-app-start-20261001T101725Z.db`. Startup applied `appeals_publication_v1` at `2026-10-01 10:18:03 UTC`, publishing 42 existing eligible records with fresh five-day windows. The pre-launch development/preview results above remain historical; the live database now contains the migration. Violation #65 retains its pending appeal and existing active strike pending explicit historical reconciliation. Face detection and recognition models both loaded successfully.
