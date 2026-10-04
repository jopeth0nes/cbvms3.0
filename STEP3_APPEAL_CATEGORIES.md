# Step 3 — Structured appeal decision categories

Implemented against the existing `main` working tree, preserving the earlier academic, attendance, account/password, and portal changes.

## Behavior

The admin Appeals case view has a required **Decision category** dropdown directly above **Administrator decision reason**. Its 12 options use the supplied labels, prefixed with Approval or Rejection. A new pending case starts at **Select decision category**. Selection alone neither fills the written explanation nor submits a decision. The existing Approve Appeal and Reject Appeal buttons and confirmation remain; an incompatible action is disabled and is independently rejected by the UI submit handler and backend.

Both fields remain mandatory, including either “Other” category. Unsaved category and explanation are retained per pending case during Refresh, Back to Appeals, and leaving/returning to the workspace. They are in-memory drafts for the current workspace, not persisted across application exits. A competing review reloads the actual winning result. Completed cases disable the category, reason, and both decision controls; the backend rejects repeat or changed decisions.

Category and explanation appear in Appeal History, Records → Decision History and its CSV export, Records violation details/reports, Reports discipline tables/CSV, and student appeal outcomes in desktop and web portals. Student outcome notifications also include the category. Historical completed cases show **Legacy — category not recorded**. No historical reason is parsed or guessed into a category. Original and supporting evidence, zoom, case identity, timestamps, and existing navigation are retained. The fixed decision footer keeps the controls visible while evidence/details scroll.

## Stable category catalog

The shared version-1 source is `core/appeal_categories.py`:

| Code | Action | Label |
| --- | --- | --- |
| `approval.incorrect_identity` | Approve | Incorrect student identification |
| `approval.detection_error` | Approve | Uniform compliant / detection error |
| `approval.valid_exemption` | Approve | Valid exemption or approved activity |
| `approval.exceptional_circumstance` | Approve | Documented exceptional circumstance |
| `approval.duplicate_record` | Approve | Duplicate violation record |
| `approval.invalid_original_evidence` | Approve | Incorrect or unusable original evidence |
| `approval.other` | Approve | Other approval reason |
| `rejection.violation_confirmed` | Reject | Uniform violation confirmed |
| `rejection.insufficient_evidence` | Reject | Supporting evidence insufficient |
| `rejection.unrelated_evidence` | Reject | Supporting evidence unrelated to the recorded incident |
| `rejection.unverified_exemption` | Reject | Claimed exemption not verified |
| `rejection.other` | Reject | Other rejection reason |

## Migration and transaction

`database/appeal_category_migration.py` adds three nullable columns to **both** `appeals` and `decision_history`: `decision_category_code`, `decision_category_label`, and `decision_category_version`. It runs during the existing database initialization and is repeat-safe. Existing rows retain NULL category values; all their original columns remain unchanged. There are no new databases, historical backfills, resets, evidence deletions, or face embedding changes.

New decisions pass a code via `decision_category_code=` to the existing `update_appeal_decision` transaction. The backend derives the label and catalog version itself and stores them atomically with administrator identity, decision timestamp, written explanation, decision history, notifications, and the existing violation/strike transition. Compatibility entry points `decide_appeal` and `log_decision` require the same valid category; they cannot silently choose a default. Student-session database handles cannot make staff decisions.

The existing `BEGIN IMMEDIATE`, pending-status check, evidence identity check, integrity restriction, and exactly-once strike handling remain in force. Approval follows the current resolution/no-active-strike path. Rejection follows the current strike path. “Duplicate violation record” does not delete or edit another record. Selecting any rejection category cannot bypass an invalid-evidence restriction. AI recommendations remain advisory and never select a category or submit a decision. Suspension and appeal duration policies are unchanged.

Reports use the stored label snapshot rather than relabeling history through the current catalog. Discipline CSVs add optional decision columns while older CSVs remain readable as reports; importing such report data does not make decisions. Decision History CSV exports include full explanations and formula protection and run off Tk. History reads also run off Tk. Appeals reads keep one active request and at most one replacement request, with stale-result protection; repeated Refresh does not accumulate an executor backlog.

## Changed files

New:

- `core/appeal_categories.py`
- `database/appeal_category_migration.py`
- `scripts/preview_appeal_category_migration.py`
- `tests/test_appeal_categories.py`
- `tests/test_appeal_category_web_render.py`
- `STEP3_APPEAL_CATEGORIES.md`

Application integration:

- `database/db_manager.py`
- `ui/appeals_panel.py`
- `ui/records_panel.py`
- `ui/student_portal.py`
- `core/reports.py`
- `core/report_csv.py`
- `web/app.js`

Updated existing regression fixtures/assertions:

- `tests/test_appeals_workspace_ui.py`
- `tests/test_appeal_flow_completion.py`
- `tests/test_evidence_association.py`
- `tests/test_violation_workflow.py`
- `tests/test_discipline_email.py`
- `tests/test_suspensions.py`
- `tests/test_student_management.py`
- `tests/test_portal_requests.py`
- `tests/test_student_portal_native_ui.py`
- `tests/test_web_portal.py`

Existing decision fixtures now explicitly supply compatible categories. Their discipline, evidence, concurrency, and permissions assertions remain. New tests cover omitted/invalid categories rather than supplying production defaults.

## Verification

Safe-copy migration previews passed twice on copies of the current database and `data/backups/cbvms-before-restart-20261004-102414.db`. Both contained **8 appeals and 7 historical decisions**. Original data across **33 current / 26 backup tables** remained identical; integrity and foreign-key checks passed. Neither source was migrated or reset by the preview.

Tests cover all 24 category/action pairings, all required fields, invalid values, duplicate clicks/retries, competing administrators, transactional rollback, restart persistence, historical cases, all rejection categories under invalid original evidence, duplicate-record approval without deletion, unchanged suspension/strike behavior, drafts, stale requests, both staff roles, completed read-only controls, original/supporting image zoom, narrow/scaled layouts, full CSV explanations, formula escaping, and consistent staff/student results. HTTP tests verify another student's result is not exposed. The actual JavaScript renderer is executed with fixture outcomes to verify category/reason display and HTML escaping.

Native testing reproduced a CustomTkinter 5.2.2 bug where destroyed dropdown menus retained scaling callbacks. The case category menu now unregisters that callback on destruction; navigation, refresh, and scaling tests exercise the fix. Invalid test appeal explanations were corrected to meet the existing minimum length, and the read-only textbox assertion now reads its native Tk state; policy and correctness assertions were not relaxed.

The first full run identified older end-to-end UI fixtures that omitted the newly required category, a trailing-space mismatch in the report fixture (the backend already trims explanations), and an intermittent evidence-navigation callback error. The fixtures now select categories explicitly and verify the student's stored code/label. The report fixture uses a trimmed explanation and follows the dashboard's root-owned workspace cleanup order before resetting Tk scaling. All **38 affected appeal/evidence tests passed** on rerun (`/tmp/cbvms-step3-affected-rerun.log`); the evidence-navigation callback error did not recur in that run. Required-field, full-explanation, callback-error, evidence, and strike assertions remain enabled.

Final full-suite rerun: **517 tests passed in 240.564 seconds** (`/tmp/cbvms-step3-full-rerun.log`). This includes the affected appeal/evidence checks, enrollment/capture fixtures, attendance, recognition, portals, permissions, and discipline regressions. The earlier evidence-navigation callback error did not recur. Python compilation, `node --check web/app.js`, and `git diff --check` also passed. Expected error messages from deliberate database/evidence failure fixtures appear in the log; the suite completed with `OK`.

Unverified manually: review with real staff/student accounts on the deployed display, real browser visual layout, other operating systems and DPI settings, and physical camera operation during an appeal review. Native Tk layouts and web rendering/HTTP behavior were tested with disposable fixtures. No real student's decision was submitted during verification.

## Run / verify

Close older application instances before starting the updated desktop. Normal startup applies the additive migration:

```sh
cd /Users/jopethones/Documents/GitHub/cbvms3.0
.venv/bin/python main.py
```

For the connected student website, restart its existing process or run in a separate terminal if port 8080 is free:

```sh
.venv/bin/python web_portal.py --host 127.0.0.1 --port 8080
```

Preview the migration without changing the source database:

```sh
.venv/bin/python scripts/preview_appeal_category_migration.py data/cbvms.db
```

Run the focused checks or full suite (native Tk and localhost access required; Node runs the JavaScript renderer test):

```sh
.venv/bin/python -m unittest tests.test_appeal_categories tests.test_appeal_category_web_render tests.test_appeals_workspace_ui tests.test_web_portal -v
.venv/bin/python -m unittest discover -s tests -v
```
