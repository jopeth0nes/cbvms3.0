# Step 2 — Attendance — Campus Sightings

Implemented on the existing `main` working tree, preserving the local Step 1, account/password, portal, and other changes. The application still uses `data/cbvms.db`.

## Meaning and operation

Admin and superadmin have an **Attendance** sidebar entry. **Records → Attendance** embeds the same `AttendancePanel` and database queries. Daily Summary defaults to today's Asia/Manila date; Sighting Events shows the underlying audit records. Clear Filters removes the date restriction as well as the other filters.

A sighting means the live monitor confirmed a registered identity using its existing quality, identity consistency, ownership, and freshness checks. Uniform compliance is irrelevant. Graduates, unenrolled students, and pending registrations may have campus sightings; their existing discipline eligibility and premises-entry behavior remain separate. No absence, lateness, class attendance, time on campus, or checkout is inferred. Attendance creates no strikes, violations, suspension changes, or disciplinary notifications.

The default cooldown is **300 seconds per student across cameras**, including across midnight. Staff can change it to a whole number from 0 through 86400 seconds. Running writers refresh the setting every two seconds. Each accepted event increments the daily count once. First/last seen are the earliest/latest **accepted** observations, not every intervening recognition frame. Events exactly at the cooldown boundary are accepted. Delayed/out-of-order facts are checked against accepted events on both sides of their observation time; already committed events are never rewritten to reorder cooldown decisions.

Filters combine dates, semester, college, dependent course, year, section, exact text Student ID, name, and camera/source. Summary classification/term describes its earliest accepted new event; a source filter selects days containing that source while the daily count still covers all sources. Use Sighting Events to inspect within-day changes or individual cameras. Student IDs retain leading zeros and punctuation. Pagination uses SQL count, stable sorting, and 25/50/100/250 row pages. Exports explicitly distinguish selected rows from all matching rows, stream a consistent database snapshot, include applied filters/timezone/date metadata, and escape spreadsheet formulas. Exported times identify Asia/Manila (UTC+08:00).

## Storage and migration

`database/attendance.py:migrate_attendance` runs during normal database initialization after the existing academic migration. It is additive and repeat-safe:

- Retains the original `attendance` daily summary table and unique student/date key, adding qualified count, provenance, legacy marker, and academic/term snapshot columns.
- Adds immutable `attendance_events`, including observation UTC time, explicit Manila date, student ID, source label/ID, camera session/frame, tracking provenance, classification/term snapshot, effective cooldown, and persistence UTC time. No new face images or embeddings are stored.
- Adds `attendance_receipts` for durable accepted/suppressed retry deduplication, and `attendance_settings` with the default cooldown.
- Copies the original six legacy summary fields into `attendance_legacy` without inventing sightings, counts, classifications, or semesters. Legacy-only classifications remain Unspecified/Needs review, historical totals remain unknown, and old timestamp text is displayed with its timezone marked unverified. Original values remain archived even when a later new observation updates that day's summary.
- Uses unique event identity constraints, immutable-event triggers, indexes, and `BEGIN IMMEDIATE` transactions for deduplication plus atomic event/summary updates. Replaying the same fact cannot increment counts twice, even after a setting change or a commit-before-journal-cleanup crash.

The existing `record_attendance` is the only persistence boundary. Its compatibility Python API has explicit `legacy_api_observation` provenance when a caller lacks camera metadata. Production uses immutable live observations. Reports never join historical event classifications to current student profiles.

## Responsiveness and failure handling

A dedicated attendance writer consumes a bounded queue of 256 waiting facts plus its active fact. It is independent of the existing replaceable discipline-work queue. An advisory bounded in-memory cooldown reduces traffic; SQLite remains the authority across cameras, processes, and restarts. Live profile reads time out at 100 ms; attendance writes at 250 ms. Locks produce retries without blocking Tk or uniform inference. Unknown/ambiguous identities, invalidated frames, tracking-only detections, and enrollment previews do not qualify. Cancellation is checked again inside the transaction before commit.

The writer fsyncs **metadata-only retry journals** under `<database filename>.attendance-pending/`, including the bounded backlog during database locks. This is a transport/recovery directory, not a second attendance database. Process locks prevent recovery of another active writer's journals. Cancelled queued facts are removed; committed facts remain auditable. A restarted writer replays abandoned journals through the same transactional deduplication boundary. Malformed journals are retained and reported for review. Do not remove this directory while retry records are pending; protect it like the application database. It is git-ignored.

Live Monitor and Attendance show queued/saved counts, write errors, overflow, and journals retained for restart. Database retries are automatic; **Retry sighting writer** restarts a worker after a filesystem/startup failure. Overflow is explicitly reported as NOT QUEUED rather than silently dropping records. An abrupt process/power loss before a newly queued fact reaches its journal can still lose that in-memory fact; queue acceptance is not a durability acknowledgment. A shutdown exceeding the worker wait deadline is logged with its remaining status. The feature does not claim lossless operation when storage is unavailable or the bounded queue is exhausted.

Attendance reads, exports, and settings writes run off Tk, with bounded in-flight work, retryable errors, loading/empty states, and stale-response protection. Student-session database handles cannot query/export the staff attendance store. No student attendance endpoint was added; endpoint and page attempts are denied rather than returning another student's data.

## Changed files for this step

- New: `core/attendance.py`, `core/attendance_writer.py`, `database/attendance.py`, `scripts/preview_attendance_migration.py`.
- Integration: `core/live_pipeline.py`, `core/live_state.py`, `database/db_manager.py`, `core/reports.py`, `ui/attendance_panel.py`, `ui/dashboard.py`, `ui/records_panel.py`, `ui/enrollment.py` (Tk font cleanup before worker startup), `.gitignore`, `requirements.txt` (portable IANA timezone data).
- New coverage: `tests/test_attendance.py`, `tests/test_attendance_writer.py`, `tests/test_attendance_pipeline.py`, `tests/test_attendance_ui.py`.
- Updated regression expectations/fixtures: `tests/test_reports.py`, `tests/test_live_persistence.py`, `tests/test_live_pipeline.py`, `tests/test_camera_violation_integration.py`, `tests/test_student_management.py`, `tests/test_live_dashboard.py`, `tests/test_camera_switching.py`, `tests/test_web_portal.py`.

Other already-modified files in the working tree belong to the earlier work and were preserved. Existing attendance tests were updated for the requested campus-sighting semantics; assertions preventing discipline writes were retained.

## Verification

Disposable SQLite fixtures cover cooldown boundaries, two students, concurrent cameras/inserts, duplicate retries and restarts, delayed/out-of-order writes, Manila midnight, leading-zero/punctuated IDs, immutable academic snapshots, cancellation rollback, legacy and repeat migration, combined filters, stable pagination, all-matching versus selected exports, formula protection, and student-session denial. Worker fixtures cover sustained traffic, bounded overflow, actual SQLite locks, journal recovery, live-owner locks, corrupt journals, and failed-worker retry. Native Tk fixtures cover off-thread work, heartbeat responsiveness, stale responses, dependent college/course filters, loading/error/empty/retry behavior, both staff roles, Records reuse, and narrow/wide layouts. HTTP fixtures check that student sessions cannot request another student's attendance.

Synthetic camera fixtures exercise the existing tracker-to-recognizer handoff with multiple students and an unknown face, uniform compliance, blocked discipline persistence, immutable source/time metadata, stale/cancelled/wrong-session/ambiguous rejection, and unchanged configurable identity confirmation thresholds. The untracked identity-publication path also tests slow uniform analysis expiring after identity confirmation. Existing enrollment, three-angle capture, identity binding, evidence, portal, appeal, strike, suspension, permission, and camera regressions are included in the full suite.

Safe-copy migration checks passed twice on copies of both the current database and `data/backups/cbvms-before-restart-20261004-102414.db`: **19 legacy summaries, zero fabricated events, integrity and foreign-key checks passed**. All original columns/rows across **29 current / 26 backup tables** remained identical. Neither source database was migrated or reset for these checks.

Final verification on 2026-10-04:

- Full suite: **501 tests passed in 215.715 seconds** (`.venv/bin/python -m unittest discover -s tests -v`); log: `/tmp/cbvms-step2-complete-suite.log`.
- Focused store/writer/recognition checks: **20 passed**; native enrollment/attendance/camera rerun: **27 passed**. Both groups are also covered by the final full suite.
- Python compilation and `git diff --check` passed.
- The two safe-copy migration checks described above passed, with no production database changes.

During integration, the old camera-feed fixture was updated for the new status label. A native enrollment regression also exposed a Tk font finalizer running during background-thread startup; enrollment now collects retired widget/font cycles on the Tk thread before starting validation. The focused 27-test enrollment/UI/camera rerun passed without relaxing its timing assertions.

Unverified manually: physical multi-person camera recognition in real lighting, actual simultaneous physical/network cameras, device switching/disconnection during a prolonged lock, unattended long-duration operation, process/power loss at every journaling boundary, and the Windows-specific journal-lock implementation. Camera behavior above was tested with fixtures, not by enrolling or observing real students. Review the new view on the deployed display with actual staff accounts after restart.

## Run commands

From the repository root, close an older desktop instance before starting the updated application:

```sh
cd /Users/jopethones/Documents/GitHub/cbvms3.0
.venv/bin/python main.py
```

Normal startup applies the additive migration. The currently running old application was not restarted or migrated during Step 2 verification.

The existing connected student website remains available through its own command (run in a separate terminal if it is not already listening on port 8080):

```sh
.venv/bin/python web_portal.py --host 127.0.0.1 --port 8080
```

Read-only-source migration preview and focused/full regression commands:

```sh
.venv/bin/python scripts/preview_attendance_migration.py data/cbvms.db
.venv/bin/python -m unittest tests.test_attendance tests.test_attendance_writer tests.test_attendance_pipeline tests.test_attendance_ui -q
.venv/bin/python -m unittest discover -s tests -q
```

Native Tk and HTTP tests require a desktop-capable environment with localhost binding permitted. No database reset, evidence deletion, or face embedding regeneration is needed.
