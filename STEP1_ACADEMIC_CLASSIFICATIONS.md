# Step 1: shared academic classifications

Implemented on `main`, preserving the existing uncommitted password-setup and portal work. The configured database was not migrated in place during development. Normal application startup applies the migration.

## Behavior and representation

- `core/academics.py` contains the supplied seven-college, 25-program catalog, stable application keys, explicit abbreviation aliases, validation, legacy parsing, and display labels. No programs were reassigned.
- Enrollment, student editing, and native self-registration share dependent College/Course controls. College and Course start with prompts; Course is disabled until a college is selected. Changing colleges clears incompatible courses. Long course names also appear in a wrapping label.
- Year Level and optional Section are separate. Year suggestions are not program durations: any positive year number and `Not applicable` are accepted.
- Existing `college_department`, `course`, `report_year_level`, and `report_section` store canonical labels. `year_and_section` is the compatibility mirror. Stable catalog keys are accepted at the persistence boundary without creating duplicate student classification fields.
- Existing callers that supply an unambiguous course/alias without a college resolve through the shared catalog. New UI selections and CSV rosters require a college. Unknown or incompatible new classifications fail before writes.
- Unresolved legacy college/course values display as `Unspecified/Needs review`. Student Details shows their raw values. An unchanged legacy classification does not block contact/status edits; changed academic fields are validated in the UI and persistence layer.
- CSV rosters validate all rows before writing, report line-specific errors, and commit together. Limits are 20 MB and 50,000 rows. IDs remain strings. CSV export prefixes spreadsheet-sensitive values with an apostrophe; the reader reverses that escape (including escaped literal apostrophes).
- Profiles, roster/discipline reports, attendance reports, and suspension filters share canonical labels. Report Course filters depend on College. Discipline CSV snapshots remain separate from the discipline ledger.
- Affected data operations run off Tk through bounded workers, with error/retry states and destroyed-view/stale-read protection. An in-flight save cannot be started again. Capture Back retains academic widgets and values.

## Migration

`database/academic_migration.py:migrate_academics` runs after existing student-column migrations, including the older `year_level` rename.

It adds `academic_migrations` and `student_academic_legacy`. Version 1 snapshots each existing student's exact college, course, structured year/section, and original combined text before normalization. Only explicit aliases/exact catalog matches resolve. Conflicting/unknown college-course values remain untouched for review. Recognized structured year/section take precedence when rebuilding the combined compatibility field. The version marker prevents subsequent startup from rewriting those records.

It does not alter IDs, names, photos, embeddings, accounts, evidence, discipline records, notifications, appeal policies, suspension policies, or role permissions. The legacy table deliberately has no cascading student foreign key.

Verification used disposable databases (including a pre-report schema), restart/repeat migration, and a read-only SQLite backup of `data/cbvms.db`:

```sh
.venv/bin/python scripts/preview_academic_migration.py
```

Copy result: 5 students, 0 unresolved college/course classifications, 24 protected tables unchanged, exact raw text retained, integrity `ok`, repeat migration unchanged. The temporary copy is deleted after verification; the source is opened read-only.

## Regression findings

Before Step 1, enrollment accepted free text and the CSV roundtrip fixture accepted `Computing` with `BSCS`. New tests exercise all 25 supported programs and all 150 incompatible pairings.

Three broader-suite failures were reproduced in a temporary copy with Step 1 removed and the pre-existing local password/portal work retained:

- The login-routing fixture lacked the session token now required by the existing local login flow. The fixture now supplies and asserts that token.
- The discipline fixture attempted to impose a manual suspension over an existing automatic suspension. It now asserts overlap rejection, explicitly lifts the automatic suspension, and verifies manual suspension/appeal behavior.
- A portal read unnecessarily requested a writer lock when suspension reconciliation had no candidates. A read preflight now returns in that case, and candidates are rechecked under the original write lock. Award rules, thresholds, durations, and appeal windows are unchanged.

## Changed files for this step

New implementation and verification files:

- `core/academics.py`
- `database/academic_migration.py`
- `ui/academic_fields.py`
- `ui/background_task.py`
- `scripts/preview_academic_migration.py`
- `tests/test_academics.py`
- `tests/test_academics_ui.py`
- `STEP1_ACADEMIC_CLASSIFICATIONS.md`

Updated application files:

- `database/db_manager.py`, `database/student_management.py`
- `ui/enrollment.py`, `auth/register.py`, `ui/student_management.py`
- `core/report_csv.py`, `core/reports.py`, `ui/reports_panel.py`, `ui/suspensions_panel.py`
- `core/portal_state.py`, `ui/student_portal.py`, `web/app.js`

Updated regression fixtures/assertions:

- `tests/test_enrollment_native_ui.py`, `tests/test_face_capture.py`
- `tests/test_report_csv.py`, `tests/test_student_management_ui.py`
- `tests/test_suspensions.py`, `tests/test_suspensions_ui.py`, `tests/test_violation_workflow.py`

Other modified/untracked files in the worktree predate this step and were preserved.

## Verification and manual checks

Final results:

- 273 regression checks passed across 22 modules: academics, face capture, enrollment recovery/preview, registration preview, student management, suspensions, CSV/reports, violation/appeal workflows, evidence association, tracking/live persistence/recognition, profile photos, password setup, web portals, and bounded portal requests.
- 44 native checks passed across academics, enrollment, student management, suspension filters, student portal, and password setup. This includes real Tk dropdowns, asynchronous report import/error/retry, frozen-face registration, three repeated three-angle enrollments, Back, failed-save retry, and owner preservation.
- The native batch initially exposed a nondeterministic synthetic-camera fixture: its producer could publish a valid original face after the click but before the test assigned the departing/replacement person. The fixture now gates producer publication across those two actions. No capture/identity assertion was relaxed. All six affected native enrollment tests passed on the final rerun; the other 38 native tests had passed in the combined batch.
- A final capture-key review retained academic values in enrollment's frozen identity key. All 38 affected face-capture/recovery checks and all six native enrollment checks passed after that adjustment.
- Disposable legacy/restart/repeat migrations and the configured database's safe-copy migration passed. `compileall` and `git diff --check` passed.

Commands used for the final broader and native checks:

```sh
.venv/bin/python -m unittest tests.test_academics tests.test_face_capture tests.test_enrollment_recovery tests.test_enrollment_preview tests.test_registration_preview tests.test_student_management tests.test_suspensions tests.test_report_csv tests.test_reports tests.test_violation_workflow tests.test_appeal_flow_completion tests.test_evidence_association tests.test_live_pipeline tests.test_live_persistence tests.test_recognizer_identity tests.test_coordinated_monitor tests.test_camera_violation_integration tests.test_profile_photo tests.test_password_setup tests.test_web_portal tests.test_password_setup_web tests.test_portal_requests -q
.venv/bin/python -m unittest tests.test_academics_ui tests.test_enrollment_native_ui tests.test_student_management_ui tests.test_suspensions_ui tests.test_student_portal_native_ui tests.test_password_setup_native -q
.venv/bin/python -m unittest tests.test_face_capture tests.test_enrollment_recovery -q
.venv/bin/python -m unittest tests.test_enrollment_native_ui -q
```

Native Tk and localhost web checks required access outside the macOS sandbox. Native tests use actual Tk widgets, synthetic camera frames, and disposable SQLite databases; no real students are enrolled.

Manual checks still needed: the physical MacBook/IP camera under real lighting; the longest college/course labels on the user's display scaling; an operator's complete enrollment/edit/import workflow against an operational backup; real email delivery. Automated evidence/face/account assertions do not substitute for real-camera recognition accuracy testing.
