Student portal loading fix — verified 2026-09-30

The unfinished Notifications/My Violations fix is implemented. Verification used temporary databases and a read-only backup of the actual database for student `2023-00883`. No live student records were edited for testing. The existing camera changes remain intact.

**Confirmed causes and fixes**

- The September 29 baseline trace repeatedly stopped in `CTkScrollbar.set → _draw → update_idletasks`, including nested idle processing. After clicking My Violations, the next scheduled navigation did not run for more than 45 seconds. That was a native rendering/event-loop stall, not proof of a slow database query. `ui/portal_scroll.py` now uses a standard Tk canvas/ttk scrollbar, one deferred scroll-region update, and a removable window-scoped wheel binding. It avoids the redraw path captured in that trace. The temporary `_root` attribute collision from the earlier attempted fix is also absent.
- The unfinished asynchronous rewrite still contained main-thread database actions and a missing lazy evidence-image load. All portal database actions and image preparation now run on the portal worker. Only native image creation and widget updates occur on Tk's thread. Snapshot retrieval checks both record ID and student ID.
- Navigating during an action previously left the new page without a request; queued actions could also be replaced before execution. A condition-protected worker now keeps one running operation and at most one pending request, preserves authorized actions, cancels obsolete reads, and starts the new page after the action completes. It never starts another worker to hide a timeout.
- A render failure could advance the snapshot key despite leaving old content visible. Snapshot keys now advance only after successful delivery. Failed reads and renders retain existing content and expose Retry. Loading, loaded, empty, failed, and cancelled states are exercised in tests.
- Badge updates could rebuild report/settings forms and discard a draft. Those forms now persist through background refresh, and unchanged record pages preserve their widgets and scroll position. Ten-record pagination bounds native layout cost; evidence images remain on demand.
- Database reads use page-specific metadata, worker-owned connections, a 750 ms SQLite busy wait, and a four-second request deadline with a SQL progress handler. Deadline processing stays enabled, scoped to the student, and idempotent. With no due transitions it avoids taking a writer lock. Pending results are never interpreted as empty records after an exception. No speculative indexes were added.
- A combined native test run failed after switching from portal tests to other windows. Explicit garbage collection on Tk's thread between destroyed portal roots resolved that sequence in the next run, consistent with the old-root cleanup issue previously documented in this project. Login/logout now performs the same cleanup. Portal-owned modal/toast callbacks, polling, scroll restoration, pending work, and wheel bindings are cancelled or removed at shutdown. Late UI delivery is rejected; a save timeout still waits for its actual outcome.

**Measured before and after**

The archived baseline recorded callback return times of 84.8 ms for Notifications and 225.1 ms for My Violations; it did not record a reliable first-content time. Repeated stack samples showed the native redraw stall for over 45 seconds. Those callback-return values are not equivalent to the new shell/content measurements.

An intermediate fixed version with 20 records per page still took a median 1,650 ms for the My Violations layout to become ready. Reducing the page to ten records brought that median to 168 ms and the worst observed sample to 433 ms.

The final audit invoked every actual sidebar button three times against a disposable backup of the real student database, then invoked Logout. Below are medians in milliseconds across those three cycles, plus the maximum visible-ready latency. Shell measures title/status construction; database and preparation measure the worker stages; render measures widget construction. Visible-ready measures the event-loop observation of completed loading and a mapped content viewport, not a pixel-level presentation timestamp. Settings and Report build their forms immediately while the badge query completes separately.

| Route | Result | Shell ms | DB ms | Prep ms | Render ms | Visible-ready ms | Max ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dashboard | PASS | 11 | 6.3 | 0.0 | 17 | 114 | 198 |
| My Violations | PASS | 25 | 6.3 | 0.0 | 46 | 168 | 433 |
| Notifications | PASS | 43 | 3.2 | 0.0 | 14 | 147 | 153 |
| My Appeals | PASS | 17 | 3.1 | 0.0 | 12 | 123 | 199 |
| My Profile | PASS | 16 | 4.5 | 3.5 | 23 | 134 | 165 |
| User Settings | PASS | 27 | 2.6 | 0.0 | 6 | 168 | 168 |
| System Report | PASS | 13 | 3.0 | 0.0 | 332 | 593 | 601 |
| Logout | PASS | — | — | — | — | — | — |

All final observed route loads were under one second; the slowest was System Report at 601 ms. This is a local three-cycle measurement, not a guarantee for other machines or record contents. Timers remained between three and five in the audit, and the worker exited after logout.

**Workflow and isolation results**

- PASS: stored account mapping for string student ID `2023-00883`; admin and portal receive the same database instance/path through authentication. The source contained 41 records for this student (6 pending_review, 32 legacy unreviewed, 2 auto_confirmed, 1 reviewed). Normal deadline processing ran only in the disposable copy during the audit.
- PASS: owner-scoped notification list and unread badge; marking one/all read persists through relogin. Another student's unread notification remains unchanged. Related-record actions open the correct owner-scoped violation.
- PASS: pending records are visible without strikes or an appeal action. Confirmation moves the same record into Confirmed, creates one active strike, and opens the existing appeal window. SQL filtering removes that record from Pending without resetting the selected filter.
- PASS: an appeal submitted through the native dialog appears in My Appeals. Approval removes the corresponding strike; dismissal and approved appeals appear in Resolved. Other-student records and appeals remain excluded. Existing deadline/rejection/idempotency regressions also pass.
- PASS: repeated refresh/navigation and relogin create no duplicate notifications or strikes. The tests exercise 45-record lists, pagination, scroll retention, empty pages, rapid switching, navigation during a read/save, query failure, SQLite lock contention, render failure, Retry, read and save timeouts, and logout while work is active.
- PASS: profile name, password, profile photo, and system-report submission through native controls in an isolated database. Profile-photo checks preserve enrollment photos, encodings, and the other student's data. Report drafts survive badge refresh.

**Validation and repeatability**

The broad headless suite passed 327 tests. The combined native group passed 25 tests (8 portal and 17 monitor/student-management/records/suspension tests). The final ten-record pagination change was additionally checked with the nine worker/database tests and the eight native portal tests. No callback errors, accumulated portal workers, growing wheel bindings, or growing database file-descriptor counts were observed in the repeatable navigation checks. Python compilation and `git diff --check` passed.

Run the native audit from a desktop-capable session:

```sh
.venv/bin/python scripts/verify_portal_navigation.py --output /tmp/cbvms-portal-audit.json
```

The source is opened with SQLite `mode=ro` and backed up into a temporary directory. A parent process terminates a stalled child after 80 seconds. The JSON includes timings, mapping, source status counts, callback counts, and pass/fail details. A missing desktop, failed route, callback exception, or timeout is a failure rather than a skipped success.

Repeat the native regressions:

```sh
.venv/bin/python -m unittest tests.test_student_portal_native_ui tests.test_live_monitor_native_ui tests.test_student_management_ui tests.test_records_ui tests.test_suspensions_ui -v
```

Relevant headless regressions:

```sh
.venv/bin/python -m unittest tests.test_portal_requests tests.test_startup_portal tests.test_violation_workflow tests.test_profile_photo tests.test_suspensions -v
```

**Files completing this request**

- `ui/student_portal.py`: page shells, delivery/error states, async actions, safe refresh, pagination, lazy evidence, owner-scoped navigation, and session callback cleanup.
- `ui/portal_scroll.py`: native scroll container that avoids the observed nested redraw path.
- `core/portal_state.py`: page-specific snapshots, image preparation, bounded requests, cancellation, and worker-owned database access.
- `database/db_manager.py`: pagination/metadata queries, owner-scoped notifications, scoped deadline processing, and bounded connection options from the resumed work.
- `auth/login.py`: main-thread cleanup between login/portal roots while retaining the authenticated student/database mapping.
- `tests/test_portal_requests.py`, `tests/test_student_portal_native_ui.py`: worker, database, navigation, workflow, contention, and lifecycle regressions.
- `tests/test_profile_photo.py`, `tests/test_student_management_ui.py`: fixtures updated for background operations and prepared profile images.
- `scripts/verify_portal_navigation.py`: bounded, repeatable native audit using a disposable database copy.

**Limits**

Native checks ran on this macOS desktop. Windows/Linux rendering and physical camera accuracy were not retested for this portal change; existing camera regression tests passed. The external AI appeal-analysis service was mocked in submission tests, so network availability and advisory quality are not established. Separate computers with separate SQLite files still do not share records. No disciplinary rules were changed, and no real records were forced into a different status for verification.
