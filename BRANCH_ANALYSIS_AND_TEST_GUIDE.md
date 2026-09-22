# mateo-branch: system analysis and guided acceptance tests

Reviewed September 22, 2026. Branch commit: `6e93de1` (student status updates).
Comparison baseline: `3855cfd` on `main`. GitHub branch tips were verified in this session.
The branch adds one commit, changing 13 files, with 947 additions and 36 deletions.

## Review result and evidence

The branch adds an academic-standing and suspension workflow across the admin portal,
student portal, registration, database, and camera processing. Its existing automated
checks pass. Physical-camera behavior, visible layout, audio, and user-operated
login/logout still need acceptance testing on the target computer.

- **77 automated regression tests passed**: student management, discipline/appeals,
  camera integration, attendance/reports, and camera switching.
- **1 desktop widget integration test passed**: admin enrollment form, student details,
  status save, suspension assignment/lifting, premises dialog, registration contact
  fields, and student profile labels.
- **15 additional smoke checks passed** using synthetic data: all seven student pages,
  profile status refresh, all five admin Records tabs, Account Manager, and Violation Log.
  These checks constructed widgets and loaded data; they do not establish visual quality
  or complete every action exposed by those pages.
- Temporary-database probes reproduced the ID-reuse issue described below and confirmed
  the suspension, contact-history, login, and notification behaviors in this guide.
- Automated checks used temporary databases. The actual `data/cbvms.db` was not opened
  or migrated for this analysis. No application code was changed.

Commands run:

```sh
.venv/bin/python -m unittest tests.test_student_management tests.test_violation_workflow tests.test_camera_violation_integration tests.test_reports tests.test_camera_switching -v
.venv/bin/python -m unittest tests.test_student_management_ui -v
.venv/bin/python /private/tmp/cbvms_portal_audit.py
```

The temporary smoke script is session-local. The repository tests are the repeatable
regression baseline. This is a source and workflow review with selected runtime checks,
not a complete security, load, or model-accuracy certification.

## System map: admin portal

| Area | Existing responsibility | Effect of this branch |
| --- | --- | --- |
| Login | Routes administrator and student accounts to their respective portals | No authentication redesign; pending students can still log in |
| Live Monitor | Recognition, attendance, violation detection, overlays, live alerts | Reads current academic/verification/suspension state; changes monitoring eligibility and labels |
| Student Management | Formerly Student Enrollment; enrollment, photo/encoding management, search, delete | Adds status filter, contacts, Details/Suspension/History, and Premises Entry Log |
| Violation Log | Review, confirm/dismiss, evidence and exports | UI unchanged; backend rejects new violations for pending, Graduate, and Unenrolled records |
| Records → Attendance Report | Daily presence, date/search filters, CSV/HTML/print | Report UI unchanged; only verified Enrolled students generate new attendance |
| Records → Violation Reports | Violation reporting and exports | Existing historical reports remain available |
| Records → Appeals Management | Human review and appeal decisions | Existing cases remain reviewable after academic status changes |
| Records → Evidence Files / Decision History | Evidence retrieval and appeal audit | Existing behavior retained |
| Training | Classifier training and supporting data | No changes in this commit; eligible detections still depend on loaded models/settings |
| Account Manager | Student account list and password reset | No changes; account access is independent of academic status/suspension |
| Settings | Cameras, recognition, detectors, academic term, alerts, admin password, AI/email | No changes in this commit |
| Alerts | In-session notification list, sounds/toasts | Receives suspension recognition alerts |

Attendance and printable violation reports were introduced in the September 18 commit
already on `main`; they are inherited features, not new additions unique to this branch.

## System map: student portal

| Page | What the student can do | Effect of this branch |
| --- | --- | --- |
| Dashboard | View visible violations, pending appeals, recent activity and semester strikes | Adds academic status and suspension label below the summary panels |
| My Violations | View confirmed cases/evidence and submit eligible appeals | Historical cases remain accessible after status changes |
| Notifications | Read violation/appeal notices | No new suspension-assignment notice is inserted by this branch |
| My Appeals | View submissions, advisory AI analysis, and administrator decisions | Existing appeal rules remain intact |
| My Profile | View identity, change name/password, see session activity | Adds status, suspension, email and mobile display |
| User Settings | Appearance/sidebar and email preference controls | Unchanged; see existing limitations below |
| System Report | Submit a report into the database | Unchanged; no corresponding admin report inbox was found |

The student cannot set their own academic status, verify enrollment, assign/lift a
suspension, or edit the new contact fields through Update Profile. Contact edits are
available to administrators and during registration/enrollment.

## Rules that determine expected results

| Current state | Student login | New attendance | New uniform/grooming violations | Premises sightings | Suspension alert when recognized |
| --- | --- | --- | --- | --- | --- |
| Verified Enrolled | Allowed with account | Yes | Yes, if detected | No | If an active suspension exists |
| Pending verification | Allowed with account | No | No | No | If an active suspension exists |
| Graduate | Allowed with account | No | No | Yes | If an active suspension exists |
| Unenrolled | Allowed with account | No | No | Yes | If an active suspension exists |
| Unrecognized face | No identity established by camera | No | Existing unknown-person security logging only | No named student entry | No student suspension match |

Suspension is independent of these states. A suspended, verified Enrolled student
still qualifies for attendance and normal violation processing. Suspension does not
operate a physical gate, automatically create another violation, or disable login.

Pending verification is a flag, not a fourth academic status. New self-registrations
receive it; administrator-created enrollments are verified by default. Pending
students must first be verified as Enrolled before switching to Graduate/Unenrolled.

Historical attendance, violations, strikes and appeals remain after a status change.
The five-day administrative review and five-day student appeal windows remain.
Strikes stay scoped to category and semester. Existing cases may still progress
through deadlines or appeal decisions while the student is no longer Enrolled.
Approving an appeal does not lift an independently assigned suspension.

## Data and implementation changes

| Files | Changes |
| --- | --- |
| `core/student_status.py` | Shared status values, optional email/mobile validation, display labels |
| `database/models.py` | New student fields: `student_status`, `registration_pending`, `mobile_number` |
| `database/student_management.py` | Repeatable additive migration, status history, suspensions, premises observations |
| `database/db_manager.py` | Migration integration, expanded student reads/writes, transaction-time eligibility guards |
| `auth/register.py` | Scrollable form, contact fields, pending self-registration and revised success message |
| `ui/enrollment.py` | Student Management UI, filters, contacts, new columns and dialog entry points |
| `ui/student_management.py` | Details, Suspension, History and Premises Entry Log dialogs |
| `ui/dashboard.py` | Current-state lookup, attendance/premises routing, classifier eligibility, alerts and enrolled count |
| `core/tracker.py` | Status/suspension fields; removal of obsolete uniform/violation overlays |
| `ui/student_portal.py` | Status/suspension/contact display and status polling |
| `tests/test_student_management.py`, `tests/test_student_management_ui.py` | New backend/camera/widget checks |
| `STUDENT_MANAGEMENT_CHANGES.md` | Branch implementation and usage notes |

New tables store status-change history, suspension assignment/lifting history, and
premises observations. Existing students migrate to Enrolled and verified. Email is
reused; mobile defaults to blank. Suspension start/end input uses the computer's local
time and is stored in UTC. Timed suspensions become inactive exactly at the end time,
without a background expiry job. A future suspension is scheduled until its start time.

## Findings and limitations

### Branch issue: reused student IDs inherit previous suspensions

**Reproduced; high impact if student IDs can be reused.** `delete_student()` removes the
student and login account, while the new suspension/history/entry tables retain rows
keyed by the student ID text. Re-enrolling that ID makes the previous suspension appear
on the replacement record. Historical retention is useful, but identity reuse needs
an explicit rule. Prefer immutable internal identity references or prohibit reuse of
IDs with retained history. See `database/db_manager.py:530` and
`database/student_management.py:69`. Do not use delete-and-recreate to reset this test.

### Branch behavior to confirm during acceptance

- **No assignment notice:** assigning/lifting a suspension does not create a student
  Notifications item or send an email through these methods. Students see the label;
  administrators receive alerts when the camera recognizes an actively suspended person.
- **Partial audit:** status/verification transitions and suspension actions are audited.
  A contact-only change saves successfully but creates no status-history row. Reproduced
  in a temporary database; see `database/student_management.py:36`.
- **Refresh differences:** student status/suspension labels poll about every 5 seconds;
  visible admin management rows refresh about every 15 seconds; enrolled statistics
  refresh about every 10 seconds; camera tags update next recognition cycle.
  Contact fields update when My Profile is reopened. Live Alerts retain event-time tags.
- **Suspension History is a snapshot:** reopen the detail dialog to refresh its history
  after a scheduled start or expiry. The active suspension label polls independently.
- **Status placement:** Dashboard status is below its cards/recent-violation panels;
  scroll down if necessary. My Profile is the clearer first verification point.
- **Premises records are observations:** sightings less than five minutes apart are
  grouped, with camera writes throttled to roughly 30 seconds. They do not prove a gate
  crossing or checkout. Search/status filters take effect with Refresh. The view returns
  the latest 1,000 matching entries and has no export control in this branch.
- **Contacts:** blank values are allowed; email syntax is checked; mobile accepts an
  optional leading plus and 7–15 digits after removing spaces, parentheses and hyphens.
  Each field has a 200-character limit. This does not verify ownership of an address.

### Existing system gaps, not introduced by this branch

- **Registration UI blocker:** `auth/login.py` defines `_open_registration()` but its
  UI does not create a registration button/link or otherwise bind that handler.
  Source-wide references show no other production entry point. This is also present
  on `main`. The branch's pending-registration form therefore cannot be exercised
  through normal login navigation until an entry point is connected. Existing pending
  records can still be verified in Student Management.
- Authentication still includes a hardcoded legacy student login mapped to a fixed
  student ID (`auth/auth_manager.py:10`). It bypasses the normal student-account lookup.
  Use the dedicated registered test account for acceptance; remove this fallback before
  relying on normal account revocation/password management for that identity.
- Violation confirmation/dismissal uses the literal actor `admin`; the appeals UI also
  relies on that default. The new student-management history correctly passes the
  logged-in username. Audit attribution is therefore inconsistent across workflows
  (`ui/violation_log.py:1077`, `ui/records_panel.py:566`).
- Student System Report saves a database row, but no admin retrieval/review screen was
  found in the portal sources. Submission success does not establish end-to-end handling.
- The student email preference is only assigned to an in-memory dictionary by its
  toggle handler, and the displayed student activity log is session-local
  (`ui/student_portal.py:1376`, `ui/student_portal.py:1301`). Do not treat these as
  persisted account preferences or the administrative audit trail.

No fixes were applied as part of this analysis.

## Guided tests inside the application

Use the dedicated test student and both logins you confirmed are available. Record the
student ID, starting status, contact values, and any active suspension before testing.
Use a unique ID for any additional registration. Choose an address you control if
email delivery is enabled, or leave optional email blank. The automated tests above
do not require modifying real student records.

Launch the normal system, if needed, from the repository:

```sh
.venv/bin/python main.py
```

Normal startup runs database migrations and deadline processing. For camera tests,
return to Live Monitor and confirm the test face is recognized by the correct ID.
Account registration without a usable face encoding is insufficient for those checks.
One application session displays one role at a time. Log out and switch accounts for
sequential checks. To observe a five-second update live, use a second application
instance for the student portal while the admin instance remains open; avoid opening
registration/capture in that second instance while testing the admin camera.

### T01 — Find the new controls and establish a baseline

1. Admin → Student Management. Search for the exact test student ID.
2. Confirm Status and Suspension columns. If needed, use the horizontal scrollbar.
3. Select the row → Edit Details / Suspension.
4. Confirm Details, Suspension and History tabs.
5. Record status and suspension. Close the dialog; try the matching status filter.

**Pass:** the correct record is present under its matching filter, and all three tabs
open. If pending, complete T08 before status-change tests. If Graduate/Unenrolled,
set Enrolled with a test reason before T02–T03.

### T02 — Contacts and validation across both portals

1. Admin → the student's Details. Keep academic status unchanged.
2. Enter `invalid-email` and click Save Details. Expect a validation message and no save.
3. Clear it; enter `letters` as mobile and save. Expect a mobile validation message.
4. Enter a valid contact you control, or leave email blank; use `09171234567` as a
   temporary mobile test value. Save and reopen to confirm persistence.
5. Student → My Profile. Confirm Email Address and Mobile Number match.
6. Open Update Profile. Confirm it does not offer academic status or suspension editing.

**Pass:** invalid contacts are blocked, valid contacts persist and appear to the student.
Contact-only edits do not add a status-history entry. Reopen My Profile after admin edits.

### T03 — Academic status and audit history

1. Admin → Details. Select Graduate; leave the reason empty; Save Details.
2. Expect a reason-required message. Enter `QA graduation status check`; save.
3. Reopen History. Confirm Enrolled → Graduate, reason, administrator username and time.
4. Filter Student Management to Graduate, then Enrolled. The record should appear only
   in Graduate (or All).
5. Student → My Profile and Dashboard. Confirm Graduate and continued login access.
6. Repeat with Unenrolled using a new reason, and confirm both transitions remain in History.

**Pass:** transitions validate and persist, both portals agree, and previous records
remain. The Students Enrolled statistic excludes Graduate/Unenrolled after refresh.

### T04 — Camera monitoring and premises observations

1. Leave the test student Graduate or Unenrolled. Note their existing Attendance Report
   first/last-seen values and Violation Log entries before presenting the face.
2. Admin → Live Monitor. Present the enrolled test face; verify correct identity and status.
3. Remain recognized for about 45–60 seconds. Confirm no student uniform/grooming overlay.
4. Student Management → Premises Entry Log → search the ID → Refresh.
5. Expect one grouped sighting with status, entered/last-seen times, and suspension-at-entry.
6. Records → Attendance Report and Violation Log: confirm no new attendance/update or
   new student violation attributable to this observation. Existing rows are retained.
7. Leave the camera for more than five minutes, return, and refresh the premises log.
   Expect a second entry. Test the Graduate/Unenrolled filter with Refresh.
8. Restore Enrolled with a reason, return to Live Monitor, and present the face.
   Attendance should now record/update; no additional premises entry should be created.

**Pass:** monitoring follows the current state without restarting the admin application.
New violations for Enrolled students depend on enabled/trained detectors; a compliant
appearance should not be expected to produce a violation.

### T05 — Timed suspension across both portals

1. Start with the test student verified Enrolled and no active/scheduled overlapping suspension.
2. Admin → Edit Details / Suspension → Suspension.
3. Set Starts to the current local minute and Ends to 3–5 minutes later. Use the displayed
   `YYYY-MM-DD HH:MM` format. Enter `QA short suspension`; leave related violation blank.
4. Assign Suspension. Confirm Enrolled remains the academic status and the suspension tag appears.
5. Student → My Profile/Dashboard. Confirm the active suspension label. Displayed UTC is
   eight hours behind Manila time, assuming the computer is configured for Manila.
6. Admin → Live Monitor. Recognize the test student. Expect SUSPENDED, a red face overlay,
   and a Live Alert. Sound/toast depends on the enabled settings and audio device.
7. Keep the student portal open through the end time, or reopen it afterward. Expect
   No active suspension after its refresh. Reopen admin History; expect Expired retained.

**Pass:** the suspension starts/ends correctly, remains separate from academic status,
and never requires deleting its history. Login and attendance still work while suspended.
Do not expect a new student Notifications item merely from assignment.

### T06 — Indefinite, scheduled, invalid and overlapping suspensions

1. Assign an indefinite suspension with a reason; confirm the Ends field is disabled.
2. Try another overlapping suspension; expect rejection.
3. Try lifting with no lift reason; expect rejection. Enter `QA cleared`; Lift / Cancel.
4. Confirm No active suspension, then reopen History to verify who lifted it and why.
5. Try a timed suspension ending before or at its start; expect rejection.
6. Try a nonexistent related violation ID; expect rejection. Use another student's
   violation ID only if available as dedicated test data; expect rejection as well.
7. Assign a future suspension a few minutes ahead, with a valid end and reason.
   History should show Scheduled; current status should show No active suspension.
8. Cancel the scheduled test suspension with a reason, or observe its start if desired.

**Pass:** validation prevents invalid assignments; indefinite and scheduled cases
remain editable through explicit, audited lifting/cancellation.

### T07 — Existing violations and appeals survive status changes

Use a dedicated test violation; confirmations/appeal decisions create durable history.
If none exists, mark this manual test Blocked rather than altering a real case.

1. While the test student is Enrolled, obtain a pending test violation through the
   configured detector. Admin → Violation Log → select it → confirm.
2. Student → My Violations. Confirm the case is visible, a strike is counted, and an
   appeal is available within five days of confirmation. Record the violation ID.
3. Submit an appeal with at least 20 characters of reasoning.
4. Admin → Student Management: change the student to Graduate with a reason. Optionally
   assign a separate suspension explicitly linked to this test violation.
5. Confirm the student still sees the historical case and pending appeal.
6. Admin → Records → Appeals Management: approve the test appeal with a review note.
7. Student → reopen My Appeals/My Violations/Dashboard. Confirm the decision and the
   affected strike removal in the applicable semester/category.
8. If a suspension was assigned, confirm it remains active until explicitly lifted.

**Pass:** status changes preserve historical review rights and records; appeal approval
removes the relevant strike without implicitly lifting a suspension.

### T08 — New self-registration and OSA verification

**Currently blocked for a fresh registration:** the login screen has no registration
button/link. Its registration handler exists but is not connected to the UI. Do not
spend time looking for a missing control. If an existing pending test record is
available, run steps 3–8 using that account. Otherwise record this test as Blocked.
The following full sequence is the acceptance test after that entry point is repaired;
no repair was applied in this analysis.

The UI also has no action to return a verified record to pending verification.

1. Log out → open student registration. Use a unique test ID and username, fill required
   identity fields, and capture a usable face if camera tests will follow.
2. Try invalid optional email/mobile; expect rejection. Correct/clear them and register.
3. Log in as that new student. My Profile/Dashboard should show Pending verification.
4. Admin → Student Management → Pending verification filter → select the new ID.
5. In Details, leave verification unchecked, enter a reason, save. Expect still pending.
6. Try Graduate while pending; expect rejection requiring verification as Enrolled first.
7. Set Enrolled, check OSA verified current enrollment, enter `QA enrollment verified`,
   and Save Details. Check History for Pending verification → Enrolled.
8. Reopen the student portal and confirm Enrolled. For a face-enrolled account, compare
   Live Monitor before and after verification: pending creates neither attendance,
   premises entries nor new student violations; verified Enrolled enables attendance.

**Pass:** account access works while pending; monitoring eligibility requires explicit
verification. Administrator-created enrollment is a different, already-verified path.

### T09 — Regression tour of the remaining pages

| Portal/path | Action | Expected result |
| --- | --- | --- |
| Admin → Records → Attendance Report | Search the test student, apply dates, download CSV/Report | Rows match filters; existing historical attendance remains |
| Admin → Records → Violation Reports | Search/filter and download a report | Existing cases remain reportable after status changes |
| Admin → Records → Evidence Files / Decision History | Inspect the dedicated test appeal | Evidence/decision remains linked to the case |
| Admin → Account Manager | Find test account; optionally reset its password | Next login accepts the replacement password; status is unchanged |
| Student → Notifications | Read a confirmed-case/appeal notice; mark read | Read state changes; suspension assignment alone creates no item |
| Student → My Profile | Update only the test name; restore it afterward | Name saves; contact/status controls remain admin-managed |
| Student → User Settings | Toggle compact sidebar | Sidebar changes; do not assume preference persistence |
| Student → System Report | Submit an explicitly labeled QA report, if desired | Success confirms storage; admin inbox is currently absent |
| Admin → Settings / Training | Open the screens and inspect configured values | Existing controls remain available; no retraining or semester switch is needed |
| Both portals | Log out and log in using the other role | Correct portal opens; record any error or unexpected role |

### T10 — Restore the test state and record results

Restore the original contact values and intended academic status, using a clear reason.
Lift/cancel only suspensions created during this walkthrough. Keep history as evidence;
do not delete/recreate the same student ID to clear it. Pending verification cannot be
restored through these controls after verification. Confirm the final state in both portals.

| Test | Result: Not run / Pass / Fail / Blocked | Observation or evidence |
| --- | --- | --- |
| T01 Controls/baseline | Not run | |
| T02 Contacts/validation | Not run | |
| T03 Status/history | Not run | |
| T04 Camera/premises | Not run | |
| T05 Timed suspension | Not run | |
| T06 Suspension edge cases | Not run | |
| T07 Historical appeals/strikes | Not run | |
| T08 Registration/verification | Blocked for fresh registration | Login registration entry point is absent; existing pending test records can exercise verification |
| T09 Existing-page regression | Not run | |
| T10 Restore test state | Not run | |

When reporting a failure, include the test ID, portal/page, action, expected result,
actual result, and local time. A screenshot or exact error text helps distinguish
refresh delays, camera recognition issues, and workflow defects.
