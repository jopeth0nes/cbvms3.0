# Student management additions

## Where to use them

The existing Student Enrollment navigation item is now **Student Management**.
Its existing table, photo preview, enrollment flow, colors and controls are retained.

- Use the status filter with the existing name/ID search.
- Select a student and choose **Edit Details / Suspension**.
- **Details** contains academic status and optional student email and mobile number.
  A status change requires a reason and records the logged-in administrator.
- **Suspension** assigns a timed or indefinite suspension, optionally linked to a
  violation belonging to the student. Dates are entered in the computer's local
  time and stored in UTC. The default duration is 24 hours; it can be edited.
  OSA must explicitly assign the suspension. A strike threshold or AI recommendation
  does not assign one automatically. Overlapping suspensions are rejected.
- **History** preserves status changes and suspension assignment/lifting details.
- **Premises Entry Log** shows the latest 1,000 matching Graduate/Unenrolled entries,
  including status and suspension at entry. Continuous sightings less than five
  minutes apart are grouped into one entry, with a last-seen time. These are camera
  observations, not independently verified gate crossings or checkout records.

## Monitoring behavior

- Enrolled students keep the existing attendance and violation processing.
- Graduate/Unenrolled people stay in the recognition gallery. They create premises
  entries instead of new attendance, uniform/grooming violations, or entry-related strikes.
- Unmatched faces display **Unrecognized / Possible Visitor**. Existing unknown-person
  security logging remains; no new visitor registration system was introduced.
- New self-registrations await OSA verification. This is a registration flag, not a
  fourth academic status and not Unenrolled. They can log in but cannot create new
  attendance or disciplinary records until OSA verifies enrollment in Details.
- Suspension is independent of academic status. It appears in the management list,
  student dashboard/profile, camera overlay, and live alerts. Recognizing an actively
  suspended person raises the existing sound/toast notification. It does not operate
  a physical gate or automatically impose another violation.
- Timed suspension validity is calculated from its dates on every read. No running
  timer or open application is required to persist an expiry. Visible management-list
  tags refresh every 15 seconds, profile/dashboard tags every 5 seconds, and live
  camera tags on the next recognition cycle. Live-alert cards retain the event-time tag.

## Database changes

The next normal database initialization applies an additive, repeatable migration:

- `students`: adds `student_status`, `registration_pending`, and `mobile_number`.
  The existing `email` column is reused.
- Existing students default to Enrolled and verified; new contact fields are blank.
  Names, IDs, photos, face encodings, accounts, attendance, violations, strikes and
  appeals are preserved.
- `student_status_history`: previous/new status, reason, actor and timestamp.
- `student_suspensions`: reason, start/end, assigning actor/time, optional related
  violation, and lifting actor/time/reason. Expired and lifted records are retained.
- `premises_entries`: identity and name, status at entry, first/last observation,
  and suspension reference at entry, plus lookup indexes.

Implementation work and automated checks used temporary databases; the actual
student database was not opened or migrated during development.

## Functions and code changed

- `database/db_manager.py`: `initialize()` invokes the migration; student getters
  return the added fields; `insert_student()` accepts optional contact details and
  registration verification state. Existing callers remain compatible.
  `record_attendance()` and `log_violation()` check current academic/registration
  status within their write transactions, protecting against stale camera results.
- `database/student_management.py`: new operations for validating/saving details,
  auditing status changes, assigning/lifting/querying suspensions, and recording/
  retrieving premises entries.
- `core/student_status.py`: shared contact validation and status/suspension labels.
- `ui/enrollment.py`: filters, added table columns, contact inputs, management-dialog
  access, premises-log access, and periodic status refresh.
- `ui/student_management.py`: Details, Suspension, History, and premises-log dialogs.
- `auth/register.py`: optional contact inputs and pending-verification registration.
- `ui/dashboard.py`: reads fresh status/suspension on recognition, routes presence
  to attendance or premises logging, excludes ineligible students from classifiers,
  raises suspension alerts, and counts only verified Enrolled students in the statistic.
- `core/tracker.py`: carries status/suspension tags and clears obsolete violation
  overlays when a student is no longer eligible for student checks.
- `ui/student_portal.py`: displays contacts, academic status and suspension tags;
  refreshes visible tags without disrupting forms.

Existing five-day confirmation, appeal deadlines, per-category/per-semester strike
counting, and appeal decisions are unchanged. Historical violations can still be
reviewed or appealed after academic status changes. Approving an appeal does not
automatically lift an independently assigned suspension; OSA can lift it explicitly.

## Verification

The existing regression suite plus new status/suspension tests cover additive
migration, contact validation, enrollment verification, entry-only monitoring,
camera status refresh, exact suspension boundaries, overlap/lifting, retained
history, preserved appeals/strikes, and obsolete-overlay clearing.

A separate hidden desktop-widget test constructs the forms, saves a status change,
assigns and lifts a suspension through their buttons, and checks the student profile.
It uses a temporary database and no camera or model loading. The real camera hardware
and full on-screen appearance still need an interactive check on the target computer.
