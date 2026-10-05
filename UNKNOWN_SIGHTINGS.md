Implemented against clean `main` at `9f68e6f`. No applicable local `AGENTS.md` was found. Production student data was not reset or used as a test fixture.

The shared **Attendance — Campus Sightings** panel now includes **Unknown / Unrecognized** alongside Daily Summary and Sighting Events. Records uses this same panel. The view shows Manila observation time, camera/source, encounter reference, temporary presence ID, snapshot availability and status. Date/camera/reference filters, paging, refresh, filtered encounter counts and selected/all-matching CSV exports use background workers. Snapshots are fetched and decoded only on request. Stale responses cannot replace the current report or open a snapshot after changing views.

“Unknown” means no registered identity match was established. It is not proof that someone is unregistered or unauthorized. Counts are recorded encounters, not unique people. Historical repeated records remain visible; they cannot reliably be consolidated into people or visits.

Persistence and safeguards:

- `security_events` remains the only authoritative store, through `log_security_event()`. The former unknown branch in discipline persistence was removed; the live analysis worker submits qualified unknowns to one bounded writer independently of the replaceable discipline queue.
- Existing ambiguity, recognition failure, ownership, motion, frame freshness and cancellation checks remain. Unknown persistence additionally requires the existing configured identity confirmation count (default two) of consecutive fresh, unambiguous unmatched observations with usable embeddings. Missing observations break confirmation. Recognition thresholds are unchanged.
- A hash of the live processor session, source, camera session/generation, monitor generation and temporary tracked presence identifies an encounter. Retries reuse that key. The existing 300-second unknown cooldown limits submissions, and database uniqueness prevents a long-running track or retry from producing a second record even after the cooldown. Multiple simultaneous tracks have separate keys. No persistent biometric identity or embedding is stored in these records.
- Qualified facts retain their observation time and original snapshot while waiting for SQLite. Like attendance, delayed persistence uses the live cancellation guard rather than treating a later frame as evidence for the original observation. Cancellation is checked inside the write transaction and again before commit.
- The writer holds at most 64 queued facts plus one active fact; each JPEG is capped at 2 MB and 1024 pixels on its longest edge. SQLite attempts have a 250 ms busy timeout, with 500 ms automatic retry waits. Attendance's writer-status area shows saved/queued/cancelled/overflow/error status and supports retry. Query, CSV and snapshot work each have one in-flight worker per panel.
- Pending unknowns are in memory, not a second durable log. Session cancellation/shutdown discards unsaved pending work; a process crash can also lose it. Committed encounters remain intact. Queue overflow is reported and subsequent fresh observations can try again.
- Unknown events do not call student attendance, discipline, account creation or notification/email paths. Later recognition does not relabel or attach historical unknown records to students. Registered-student processing remains separate.
- Every unknown report, snapshot and export checks an explicit admin/superadmin username; student-session database handles are rejected even when supplied a staff username. No student portal endpoint or navigation entry was added.

Migration details:

`CBVMSDatabase.initialize()` invokes a repeat-safe additive migration on the existing `security_events` table. It adds nullable TEXT columns `event_key`, `source_id`, `source_label`, and `session_id`, plus a unique index on `event_key` and a source/time index. NULL event keys preserve all historical rows, including duplicates. Existing timestamps and snapshot BLOBs are not rewritten. Historical references display as `legacy:<row id>` and absent camera metadata is explicitly marked unavailable. No new event table is created. The migration was exercised only on disposable databases during this work; normal application initialization applies it to an installation.

Verification (2026-10-05):

- **324 tests passed in 35.355 seconds** across attendance/store/writer/pipeline, the new unknown tests, live state/pipeline/persistence/model safety, recognition, coordinated monitoring, camera switching, evidence association, face capture/enrollment/recovery/registration, profile photos, startup/portal requests/web portal, violations, discipline email and suspensions.
- **68 tests passed in 57.461 seconds** across native Attendance/Unknown UI, live dashboard/monitor UI, native enrollment, native student portal and Records.
- New coverage includes repeated frames, two simultaneous unknowns, later recognition without historical mutation, camera changes, missing embeddings, ambiguous identities, model failure, lost tracking, stale/cancelled work, SQLite locks, a lost commit acknowledgement, queue overflow, repeat migrations, historical evidence, Manila midnight filters, pagination, all-page/selected CSV, formula-safe export cells, access denial, asynchronous image decoding, UI heartbeat and stale report/snapshot rejection.
- The blocked-discipline integration test now verifies that both registered attendance and the unknown encounter reach their respective existing stores while the discipline worker is blocked.
- A final focused run after adding the unknown-writer shutdown wait passed **36 tests in 3.538 seconds** (unknown sightings, attendance pipeline and live dashboard), including a new check that dashboard exit waits for the unknown writer.
- `git diff --check` passed. The first broad run aborted under the macOS sandbox because evidence tests also create Tk windows; the complete rerun outside the sandbox passed. Native windows used simulated cameras and disposable SQLite fixtures.
- Logs: `/private/tmp/unknown-regressions.log`, `/private/tmp/unknown-native-regressions.log`, and `/private/tmp/unknown-final-focused.log`.

No physical-camera testing was performed. The automated results demonstrate simulated behavior and actual Tk workflows, not real-world recognition accuracy.

Physical-camera walkthrough (still to perform):

1. From the repository root, launch a disposable database with the snippet below. It prints its temporary path and does not use `data/cbvms.db`. Only use consenting test participants and fixture student details. Do not configure real email recipients.
2. As admin, start Live Monitor, select the desired camera, and wait until face recognition is ready. Let an unmatched participant remain visible across several fresh frames. Open Attendance → Unknown / Unrecognized, clear the default date filter if necessary, and refresh. Expect one encounter, the correct camera and Manila time, and an on-demand face snapshot. Refresh repeatedly while the same track remains visible; its encounter should not multiply.
3. Put two unmatched participants in view together. Expect separate reference IDs and appropriate snapshots. Briefly obscure a face: uncertain/lost tracking alone must not create a record. A new track after leaving/re-entering may be a new encounter; it is not a verified new person.
4. Switch cameras while an observation is in flight. Cancelled old work must not appear against the new camera. New confirmed encounters should show the selected source. Cover/disconnect the camera and confirm stale input does not create encounters.
5. Enroll one consenting participant as a disposable student through the existing enrollment flow, then return to monitoring. Confirm normal registered-student sightings resume. Previously saved unknown rows must retain their original metadata and have no student attachment. Check that unknown-only encounters produced no student attendance, violations, strikes, suspensions, accounts or disciplinary email.
6. Exercise date/camera/reference filters, paging, selected/all-matching CSV and snapshot opening in both the sidebar and Records Attendance. CSV contains metadata and snapshot availability, never image bytes. Repeat with superadmin; student portals must have no access.
7. Record camera/source, lighting, simultaneous participant count, recognition/confirmation time, preview responsiveness and any failures. Close the fixture dashboard when finished; keep or delete only the printed temporary fixture directory according to your test-data policy.

Run this manually only when ready to use the physical camera:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import mkdtemp
from database.db_manager import CBVMSDatabase
from core.recognizer import FaceRecognizer
from core.person_detector import PersonDetector
from ui.dashboard import open_dashboard

fixture = Path(mkdtemp(prefix='cbvms-unknown-camera-')) / 'walkthrough.db'
print(f'Disposable camera-test database: {fixture}', flush=True)
database = CBVMSDatabase(fixture)
database.initialize(process_deadlines=False)
recognizer = FaceRecognizer(database)
open_dashboard(username='admin', database=database, recognizer=recognizer,
               person_detector=PersonDetector())
PY
```
