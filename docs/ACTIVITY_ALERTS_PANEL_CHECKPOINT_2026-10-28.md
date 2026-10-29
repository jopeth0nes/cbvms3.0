# Activity & alerts checkpoint — 28 October 2026

## Completion verification — 29 October 2026 (authoritative)

Recovered the existing uncommitted work on `main@32edf0d`; no checkout/reset,
commit, push, production database access, physical camera, or model benchmark.
Process inspection found no surviving task-owned test. No prior final Activity
test log was found; the earlier 96-test claim was not treated as verification.

Outstanding native checks completed with desktop access and a 90-second process
timeout. Results against the final application code:

- 121 focused tests passed in 4.208 seconds (exit 0):
  `.venv/bin/python -m unittest tests.test_monitor_session_activity tests.test_activity_session_dismissals tests.test_unknown_evidence_identity tests.test_live_monitor_ui tests.test_live_dashboard tests.test_attendance tests.test_live_persistence tests.test_live_pipeline -v`
- 4 native tests passed in 3.353 seconds (exit 0):
  `.venv/bin/python -m unittest tests.test_live_monitor_native_ui.LiveMonitorNativeUITests.test_session_activity_fits_280px_sidebar_and_routes_exact_id tests.test_activity_log_navigation_native tests.test_dashboard_sidebar -v`
  This run preceded the added real violation-widget test below. It verifies the
  280 px sidebar with 16 px padding, wrapped title, thumbnail, readable button,
  exact action ID, and Live-only layout across both staff roles.
- 6 native navigation/filter tests passed in 6.181 seconds (exit 0):
  `.venv/bin/python -m unittest tests.test_activity_log_navigation_native tests.test_attendance_ui -v`
  Includes real attendance/unknown selection, real violation selection and
  detail loading outside current filters, and restored ordinary filtering.
- `git diff --check` passed.

Logs are in `docs/validation/activity_alerts_2026-10-29/`. `source-sha256.json`
records the tested application/test source contents. The native runs overlap;
their counts are per run, not distinct-test totals.

This completion pass changed tests only: added actual violation widget/detail
selection; updated two stale exact-attendance calls to use authorized keyword
arguments; gave the transaction-guard fixture a synthetic verified enrollment
so it tests the violation write instead of the enrollment-review fallback.
The first combined run exposed those two fixture errors; the final combined
run above passes. Existing application edits were preserved.

Acceptance evidence includes exact UUID/legacy/historical-update exclusion,
repeatable additive migrations, duplicate acquisition protection, full passage
reconciliation, delayed-write dismissal, unseen/new passages, decoded image
preservation, stale navigation/logout/source callback rejection, canceled
notifications, observation-only suppression, and staff/student access gates.
Clear remains display-only. Session UUID and dismissals survive navigation,
camera changes and reconnects; logout/dashboard destruction or process restart
starts a fresh session. Camera/monitor generation checks still reject stale work.

Ready for manual testing; no known failing check. Limits: synthetic native
verification is not a physical-camera recognition/audio test. Tk emits its
existing PhotoImage HighDPI-scaling warning; sizing passed at the tested desktop
scale, but other OS scaling settings have not been visually verified.

Manual check on a test installation with synthetic students:

1. From the repository run `.venv/bin/python main.py`, sign in as staff, and
   open Live Monitor. The normal launcher uses `data/cbvms.db`; use the native
   unittest commands above for fully disposable checks instead of live writes.
2. Confirm the panel appears only on Live Monitor and other pages reclaim it.
3. Observe an identified test passage and a confirmed unknown passage. Saved
   cards must show their own saved photo or Photo unavailable; an unsaved live
   assessment must say Log not saved and have no enabled record action.
4. Set restrictive log filters, return to Live, and open each saved card. The
   exact attendance, violation or unknown record must be selected. Apply an
   ordinary filter again to confirm normal filtering resumes.
5. Clear Alerts, wait beyond the five-second poll, navigate away/back, then
   switch/reconnect the camera. Dismissed cards must stay absent; a new passage
   must remain eligible. The saved records must still exist in their log pages.
6. Log out/in or restart: previous-session activity must not return. Delayed
   write races and unseen-event behavior are covered deterministically by
   `tests.test_activity_session_dismissals`.

Earlier sections below are retained as recovery history, not pending work.

Branch `main`, base `32edf0d`; working tree remains uncommitted. This follow-up
extends the earlier live-only sidebar implementation. It preserves all existing
edits, including the lead's exact acquisition evidence key and append-only
history protections.

## Implementation

The dashboard uses the existing `LiveProcessor.security_session` UUID as the
explicit monitor-session identity. It is stable across navigation, camera
changes and idle/reconnect behavior, and ends when that dashboard closes or
logs out. New attendance facts, unknown events and violations record this UUID
in a separate nullable `monitor_session_id`. Additive SQLite migrations add
the columns and indexes; legacy records remain NULL and are never assigned to
a session based on their date. A bounded staff-authorized query merges at most
100 records from the exact UUID across those three stores. Oversized saved
snapshots (over 1 MiB) are omitted; accepted snapshots are decoded and resized
on the one in-flight background reader before delivery to Tk.

The Live-only panel now displays persisted attendance, violation and unknown
events with exact event IDs, source and timestamps, saved thumbnails where
available, and honest photo-unavailable or unsaved-live placeholders. The
current reliable-identity passage may appear with “Log not saved”; transient
identifications do not create history cards. Full passage reconciliation
requires the session, source, camera generation, monitor generation, presence
ID and student ID; tracker identity alone never joins records. No reference
photo or later-frame crop is presented as event evidence.

Persisted Clear dismissal uses a per-event-type database-ID high-water mark.
Live dismissal uses the processor's monotonic `person:<generation>:<track>`
sequence within source/camera/monitor-generation scope. Scope state is reduced
to the active monitor generation; a changed assessment state cannot undo a
dismissal, while a later passage can appear. The row/card count and DB read
remain bounded. Live layout stays Live-only, observation-only mode remains
explanatory, and refresh creates no events or notifications.

Card actions target immutable IDs. Unknown opens the existing exact reference
search. Attendance opens Sighting Events with a direct authorized immutable-ID
lookup independent of date/search/page filters. Violations navigate to the
page containing the exact ID and bypass default date filters. Existing
recognized record/log semantics are retained.

## Changed files

- `ui/dashboard.py`, `ui/live_alerts.py`, `ui/attendance_panel.py`,
  `ui/violation_log.py`
- `core/attendance.py`, `core/live_pipeline.py`, `core/security_writer.py`
- `database/attendance.py`, `database/db_manager.py`,
  `database/security_events.py`
- `tests/test_live_monitor_ui.py`, `tests/test_live_dashboard.py`,
  `tests/test_live_monitor_native_ui.py`, `tests/test_dashboard_sidebar.py`
- New `tests/test_monitor_session_activity.py`,
  `tests/test_unknown_evidence_identity.py`, and this checkpoint.

## Verification so far

- `.venv/bin/python -m unittest tests.test_monitor_session_activity -v` —
  2 tests passed against disposable SQLite, including exact UUID filtering
  across attendance/unknown/violations, NULL and other-session exclusion,
  exact attendance lookup, and staff authorization.
- Focused new UI regressions for stale session delivery, full-provenance
  identity, per-type Clear high-water, later-passage behavior, and exact log
  actions — 3 tests passed.
- `.venv/bin/python -m unittest tests.test_attendance tests.test_unknown_evidence_identity -q`
  — 12 tests passed in a prior run, covering existing attendance behavior and
  exact acquisition replay protection.
- `py_compile` for changed Python modules and `git diff --check` passed.
- An attempted combined headless run including native Tk/sidebar tests aborted
  with exit 134 when Tk initialized without the desktop; no test assertion
  failed before the process abort. Native verification remains pending an
  approved desktop run.

## Review notes and remaining verification

No evidence revision or better-photo ingestion policy was added. Persisted
event snapshots remain immutable. Attendance facts do not contain an event
photo, so those cards show Photo unavailable. There is no existing
unknown-to-student resolution workflow; the UI does not infer one.

Next run headless focused tests without native Tk, then request/run the native
UI tests through the approved desktop execution path at a 280 px activity
sidebar. Review the additive migrations, exact-ID query permissions, and the
bounded dismissal scopes before accepting. No production DB, live camera,
commit, push, or deployment was used.

## Resume correction in progress (authoritative over earlier high-water notes)

Root recovered main@32edf0d and shared edits. Process inventory found no surviving
unittest/task command. Builder is active on exact-record UI/navigation tests and
native layout. No production migration was run.

Root replaced broad dismissal high-water ranges with exact display identities
in `ui/activity_dismissals.py`: a private SQLite temporary file with 64 KiB page
cache, no mmap, storing only SHA-256 identities. It closes/deletes on widget
session change/destruction. This is ephemeral UI state, not another event log.
It avoids both unbounded RAM and false suppression of unseen passage gaps.
`tests/test_activity_session_dismissals.py`: 6 tests passed, including 10,000
synthetic keys, delayed old-generation completion, unseen lower passage IDs,
strict provenance, later visits, distinct types and decoded-image preservation.
Saved events retire their live representation even after leaving the bounded
query window. Clearing saved attendance does not suppress a later violation.

Attendance facts now use a monitor-UUID/source/camera-generation acquisition
session to prevent the pre-existing UNIQUE(student_id,session_id,frame_id)
constraint from colliding when a later monitor restarts frame numbering. Legacy
Sighting.key checksums are unchanged. Exact monitor_session_id remains the panel
membership filter; source_id remains the camera source. A producer regression
passes. Session/filter/attendance/dismissal run: 19 passed before adding the
single passing producer regression. Final combined run still pending.

Synthetic identity-index lookup benchmark with 10,000 keys: 50 card lookups
averaged .083 ms, max .107 ms over 100 iterations; not camera FPS evidence.

Next: builder completes actual native exact-record selection and realistic
padded-sidebar width checks, then root reviews final diff and final focused test
results. Do not rerun a pending desktop command without checking its outcome.
