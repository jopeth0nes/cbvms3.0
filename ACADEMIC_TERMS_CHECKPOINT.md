# Academic terms implementation checkpoint

Sanitized local implementation checkpoint; no credentials or student data.

## Worktree and safety

- Worktree: `/Users/jopethones/Documents/GitHub/cbvms3.0`
- Branch: `feature/academic-term-enrollment`, based on `4292b11` (preserves newer sync fixes).
- Previously fetched origin/main: `13fcdc7`. No commits or pushes made.
- No applicable AGENTS.md found in repo or ancestors. No subagents used.
- All migrations/tests use disposable databases. No production DB initialized,
  production sync run, or deployment performed.
- Original requirements: attachment `9172632e-34b2-4ecd-b749-d7c0f7d9d25e/Pasted text.txt`.
- Resume requirements: attachment `2ea2bdec-54a8-4bc0-b3b0-ee93b9b812e0/Pasted text.txt` and latest user message.

## Implemented

Explicit Manila calendar and overlap protection; additive repeatable migration;
readonly preview/WAL-aware isolated-copy rehearsal; audited publication/date impact;
unique per-term enrollment with effective-time revisions; transactional fresh
three-angle capture plus permanent identity/account creation; upcoming enrollment;
local anchor/latest recognition; enrollment-review status/alarm/cooldown/receipts,
individual incident evidence; occurrence-term attendance/unknowns/violations;
parent-owned appeals; term/lifetime strikes; audited idempotent suspension rollover;
portal schema 3 floor with original protocol 2 retry/evidence protections; safe
student histories without biometric payloads; calendar roster/filter/export UI,
student history, incident evidence and historical report snapshots. Operating,
deployment and recovery guide: `docs/ACADEMIC_TERMS.md`.

Latest additional fixes: boundary reconciliation skips its writer transaction if
nothing changed (portal read-lock regression); identity/account/capture enrollment
rolls back together; review events retain each accepted incident image; calendar
publication queues conflicts in both directions; reporting academic year/section
filters and calendar unresolved case counts added.

## Verified results

- 319 consolidated Python regressions passed, 41.733s: `/tmp/cbvms-terms-final.log`.
  This predates latest review-event/reconciliation/reporting additions.
- 19 expanded term tests passed, 4.790s: `/tmp/cbvms-terms-new.log`.
- 61 focused tests passed, 14.959s: `/tmp/cbvms-terms-extra3.log` (term enrollment,
  suspensions, discipline mail, portal requests, evidence association/provenance).
- 44 latest term/analytics/portal request tests passed, 12.008s:
  `/tmp/cbvms-terms-last.log`.
- 16 native UI/local HTTP tests passed, 27.023s: `/tmp/cbvms-terms-ui-http.log`.
- Portal npm run typecheck, npm run build, npm test all passed after latest portal
  changes. Integration uses disposable local D1/R2 fixtures and localhost.
- compileall and git diff --check passed.
- An earlier combined test run aborted at native Tk without GUI permission;
  selected tests were rerun successfully with GUI/local HTTP permission. No
  successful whole-repository discovery is claimed.

## Final verification and handoff

- Consolidated final suite: **367 tests passed in 53.036s**,
  `/tmp/cbvms-terms-complete.log`. Includes all earlier consolidated modules plus
  suspension/email/portal-request/evidence regressions and new filter/CLI tests.
- Final preview/date-conflict changes: **20 term tests passed in 6.980s**,
  `/tmp/cbvms-terms-preview-final.log`.
- Native reporting/calendar UI: **9 tests passed in 28.893s**,
  `/tmp/cbvms-terms-report-ui.log`.
- Native evidence/calendar and local HTTP portal: **16 tests passed in 27.023s**,
  `/tmp/cbvms-terms-ui-http.log`. Some calendar tests overlap the 9-test run.
- Final compileall and git diff --check passed.
- Portal typecheck/build/integration results above remain current; no portal code
  changed after those checks.
- All commands launched for this work have completed; no test/sync/migration
  process is intentionally left running. There was no interrupted production
  operation to resume.

Latest completed checks cover historical academic year/section across table,
chart and totals; legacy term IDs in Unassigned; WAL-preserving read-only preview;
CLI isolated-copy migration and existing destination rejection. Preview now also
reports invalid timestamps and conflicts against published dates. Original term
assignments are preserved and publication queues conflicts for explicit review.

Remaining operator checks: actual physical camera quality/identity continuity,
speaker audibility, production backup restoration and approval of official campus
dates. These were not claimed as fixture-verified. Production actions are still
unapproved and untouched. Follow `docs/ACADEMIC_TERMS.md` for preview, deployment,
rollout and recovery. Do not run normal startup against real data, deploy, sync,
commit or push without the user's authorization.

Exact reusable commands:

```sh
PYTHONPATH=.:tests .venv/bin/python -m unittest tests.test_term_enrollment tests.test_suspension_analytics tests.test_portal_requests
.venv/bin/python scripts/preview_term_migration.py --db /absolute/path/campus.db
.venv/bin/python scripts/preview_term_migration.py --db /absolute/path/campus.db --apply-to-new-copy /secure/path/new-isolated-copy.db
```

## Modified files at checkpoint

See list below (generated from git status; includes new files):
 M core/cloud_sync.py
 M core/detection_audio.py
 M core/live_pipeline.py
 M core/live_state.py
 M core/portal_state.py
 M core/recognizer.py
 M core/report_csv.py
 M core/suspension_analytics.py
 M database/attendance.py
 M database/db_manager.py
 M database/security_events.py
 M database/student_management.py
 M scripts/generate_detection_sounds.py
 M sites/student-portal/app/api/[...path]/route.ts
 M sites/student-portal/db/schema.ts
 M sites/student-portal/drizzle/meta/_journal.json
 M sites/student-portal/lib/cloud-sync.ts
 M sites/student-portal/public/app.js
 M sites/student-portal/tests/sync.mjs
 M tests/test_appeal_flow_completion.py
 M tests/test_attendance.py
 M tests/test_attendance_pipeline.py
 M tests/test_camera_violation_integration.py
 M tests/test_cloud_sync.py
 M tests/test_discipline_email.py
 M tests/test_face_capture.py
 M tests/test_live_pipeline.py
 M tests/test_portal_requests.py
 M tests/test_report_csv.py
 M tests/test_reports.py
 M tests/test_suspension_analytics.py
 M tests/test_suspensions.py
 M tests/test_violation_workflow.py
 M tests/test_web_portal.py
 M ui/attendance_panel.py
 M ui/dashboard.py
 M ui/enrollment.py
 M ui/live_alerts.py
 M ui/reports_panel.py
 M ui/settings.py
 M ui/student_management.py
 M ui/suspension_overview.py
 M ui/suspensions_panel.py
?? ACADEMIC_TERMS_CHECKPOINT.md
?? assets/sounds/enrollment.wav
?? core/academic_calendar.py
?? database/term_enrollment.py
?? database/term_migration.py
?? docs/ACADEMIC_TERMS.md
?? scripts/preview_term_migration.py
?? sites/student-portal/drizzle/0003_academic_snapshots.sql
?? sites/student-portal/drizzle/meta/0003_snapshot.json
?? tests/term_fixture.py
?? tests/test_academic_terms_ui.py
?? tests/test_term_enrollment.py
?? ui/academic_terms.py
