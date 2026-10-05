# Suspensions overview and analytics

The Suspensions workspace now opens to **Overview**, with **Analytics** and
**Student Records** subnavigation. The existing directory, student details,
violation history, suspension history, and authorized lift/cancel action remain
available. Existing student shortcuts open that student's records. Returning
from a student to matching records preserves the selected row and report scope.

## Metric definitions

| Display | What is counted | Time basis |
| --- | --- | --- |
| Currently suspended | Distinct student IDs with an unlifted suspension whose start is at or before refresh and whose end is absent or after refresh | Current snapshot; ignores period and suspension-status selectors |
| Suspensions started | Suspension records in the selected scope, including historical escalation replacements | Stored suspension start date |
| Students suspended | Distinct student IDs among those suspension records | Stored suspension start date |
| Recorded violations | Student disciplinary violation records, with current pending, appeal-pending, resolved/dismissed, finalized, and legacy outcomes distinguished | Detection timestamp |
| Finalized violations | Violations with a currently active strike, excluding resolved/dismissed violations and approved or pending appeals | Stored strike award timestamp |

An escalation replacement can increase suspension-record counts without adding
another distinct student. Finalized counts reflect currently upheld outcomes;
they are not an immutable count of every strike ever awarded. Unknown-person
security events and campus sightings are excluded.

Day, week, and month charts show recorded and finalized violations separately,
plus suspension starts. Weeks start on Monday. The charts display the latest
24 buckets, with exact values available on hover; matching-record tables include
the full selected scope. Missing buckets between dated observations are zero.
Invalid dates remain visible in applicable All Data/term tables and are excluded
from trends, with the exclusion count disclosed.

College, course, and year rankings can count recorded violations, finalized
violations, suspension records, or distinct suspended students. They show counts,
not rates. Outcome and suspension-status bars use their corresponding record
counts and date bases.

## Filters and data interpretation

- The default is the current academic term. All Data, historical terms,
  Unassigned term, and an inclusive custom date range are available.
- Custom dates and chart buckets use Asia/Manila. New matching-record tables
  also display Manila dates. The retained individual-history tables keep their
  existing explicitly labeled UTC timestamps.
- Term filtering uses recorded associations, not an inferred semester calendar.
  Suspension terms come from automatic suspension awards and/or a related
  violation belonging to that student. Missing or conflicting associations are
  unassigned; a student's present term is never substituted.
- College, dependent course, and year use the shared academic catalog and
  **current student classifications**. Discipline records do not contain
  historical academic snapshots. Unspecified/Needs review and missing-student
  classifications remain explicit groups.
- Category refers to the violation category, or the linked violation category
  for suspensions. Suspensions without a usable linked violation are Unassigned.
- Violation outcome applies only to violation measures. Suspension status
  applies only to suspension-start measures. The current active snapshot still
  honors college, course, year, and category filters.
- Cards and ranking/status bars open the matching subset from the exact displayed
  snapshot, with 40 rows per page. Opening a student then shows their full
  individual history. Missing students remain visible in reports without an
  enabled Open student action.

## Implementation and preservation

| File | Responsibility |
| --- | --- |
| `core/suspension_analytics.py` | Read-only snapshot, permission check, filters, counts, trends, rankings, and matching records |
| `core/report_worker.py` | One worker with one pending request and one pending result |
| `ui/suspension_overview.py` | Overview/Analytics/Student Records navigation, responsive cards, reusable Canvas charts, filters, and drill-down |
| `ui/suspensions_panel.py` | Retained individual records, bounded background reads, asynchronous lift/cancel, and embedded scrolling |
| `ui/dashboard.py` | Mount the workspace while retaining student shortcuts and dashboard lifecycle |
| `tests/test_suspension_analytics.py` | Exact fixture totals, date boundaries, filters, consistency, permissions, locks, and worker behavior |
| `tests/test_suspension_overview_ui.py` | Real Tk navigation, controls, resizing, stale reads, errors, cleanup, and slow lift behavior |
| `scripts/capture_suspension_workspace.py` | Native screenshots using a disposable fixture database |

Analytics use a separate SQLite connection opened with `mode=ro`, `query_only`,
and one read transaction. They do not process appeal deadlines or reconcile
suspensions. Existing lifecycle processing remains in the individual-record
workflow and elsewhere in the application. No migration, second statistics
database, or historical-data rewrite is introduced.

Filters debounce for 250 ms and reject stale results before rendering. SQLite
lock waits are bounded to 250 ms for reporting; errors expose Refresh as a retry.
Charts redraw in place. Hide/destroy cancels callbacks and invalidates results;
worker shutdown discards queued reads. Report access requires a stored admin or
superadmin account. Lift/cancel continues through the existing database boundary.

## Verification completed on 5 October 2026

The resumed session reused the implementation and saved regression results,
then reran the affected tests after the last UI adjustments and regenerated and
visually inspected all six native screenshots.

| Verification | Result |
| --- | --- |
| Saved complete regression run, each test module in a separate process | **582 tests across 53 modules passed**, 190.577 seconds |
| Final aggregation/worker rerun | **15 passed**, 1.964 seconds |
| Final native overview/UI rerun | **7 passed**, 15.618 seconds |
| Existing suspension UI tests in the complete run | **11 passed**, 4.088 seconds |
| Existing suspension rules tests in the complete run | **5 passed**, 0.559 seconds |
| Compile checks and `git diff --check` | Passed |

The complete run includes appeal, reporting, evidence, enrollment, recognition,
attendance, portal, live-monitor, unknown-sighting, and sound regressions. The
22 final focused tests are reruns of tests included in the 582, not additional
unique tests.

Fixtures include multiple academic groups, approved/rejected/pending appeals,
inactive strikes, active/lifted/expired/future/indefinite suspensions, escalation
replacements, deleted students, invalid dates, and Manila midnight boundaries.
Exact custom-day totals are 7 recorded violations, 3 finalized violations,
5 suspension records, 3 distinct suspended students, and 3 currently suspended
students. All Data totals are respectively 10, 4, 7, 4, and 3. Tests verify that
concurrent commits cannot mix report snapshots and that report reads leave the
database dump unchanged.

Native tests cover 560–1080-pixel windows, 100% and 125% widget scaling, repeated
refresh, dependent course choices, empty/error/retry states, student shortcuts,
drill-down/back selection, denied student access, chart reuse, and worker
cleanup. During a deliberately blocked lift operation, the Tk event loop still
handles a scheduled callback, and the lift executes once on a worker thread.

Reproduce the focused checks:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_suspension_analytics.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_suspension_overview_ui.py' -v
.venv/bin/python scripts/capture_suspension_workspace.py
```

The screenshot script requires a macOS desktop and screen-capture access. It
opens only disposable data, disables camera/model startup, and captures the app
window's region. No real student records were modified for verification.

## Screenshots and remaining checks

- [Overview](overview.png)
- [Violation and suspension trends](overview-trends.png)
- [Analytics](analytics.png)
- [Matching records](matching-records.png)
- [Student records](student-records.png)
- [Suspension history and lift controls](student-history.png)

These are actual macOS UI captures populated by test fixtures. Windows/Linux
visual checks, additional display scaling configurations, physical-camera
operation, and production-sized database performance were not measured in this
task. Aggregation reads the relevant discipline tables into memory; the bounded
worker prevents an accumulating request queue but does not impose a row limit
on the reporting snapshot.

On the deployment computer, open Suspensions, compare a known term and custom
Manila date range, click a metric and an academic-group bar, open a student and
go Back, and confirm filters and selection persist. Resize the window and check
both scrollbars, history tabs, and authorized actions using disposable records.
Check that a student account cannot access the workspace and that other
dashboard views remain usable.
