# Camera-to-portal reassessment

The subsequent coordinated torso/tracking fix and its latest live verification are documented in [LIVE_MONITOR_TORSO_FIX.md](LIVE_MONITOR_TORSO_FIX.md). The measurements below retain the earlier session's history.

Implemented on `main`, starting from a clean working tree. No commits, account reassignment, live database migration, forced confirmation, enrollment-image changes, embedding changes, or trained-asset replacement were performed by the verification work. Real database inspection used SQLite read-only connections; model probes used a temporary database backup.

## Confirmed causes and evidence

- **All analysis depended on one startup gate.** The previous `CBVMSDashboard._prewarm_models()` sequentially loaded/primed face and body models, classifiers, potentially rebuilt the colour reference, and loaded optional pose before setting `_models_ready`. `_update_feed()` submitted no analysis before that event. Login separately primed face/body inference. Failures were printed and the event was eventually set regardless of success. A stuck optional operation could therefore leave a healthy preview at 0 analysis/s indefinitely.
- **Unnecessary face-model initialization.** The installed InsightFace `FaceAnalysis` opens every ONNX file before applying `allowed_modules`, including two unused landmark networks. The new loader opens only the cached detector and recognition files directly.
- **Pending records were deliberately hidden.** `get_visible_violations_for_student()` selected only `CONFIRMED_STATUSES`. The portal’s labels and appeal controls also assumed every row was confirmed. This was a visibility-policy issue, not evidence of missing commits.
- **The open portal did not refresh its violation list periodically.** Its existing timer refreshed standing labels and AI results, while My Violations kept its previous snapshot.
- **The legacy login shortcut bypassed persisted ownership.** A hardcoded `student` account took precedence over database accounts and always routed to `2023-00883`. That shortcut is removed; existing registered accounts retain their own mappings and passwords.
- **Recognition could miss a face that tracking saw.** A real probe on `2023-00883`’s stored enrollment photo detected one face at 320 input and none at 640. Recognition now retries the preview scale only when the larger-scale pass finds no faces, using the same detector and confidence threshold. The real stored-photo probe then recognized `2023-00883`.
- **Camera status could contradict the preview.** The stale-frame path displayed a reconnecting placeholder without changing the active status label. Both now change together; repeated failed reads also trigger the existing owned release/reconnect path.

Read-only database inspection found the following for `2023-00883`: enrolled, registration verified, a nonempty encoding, a registered account with the same string ID, and **39 committed violations: 4 pending_review, 32 legacy unreviewed, 2 auto_confirmed, 1 reviewed**. The same counts remained after verification. Records for other students also exist. No ownership or timestamps were changed to manufacture visibility. Normal application startup retains the existing legacy migration and five-day deadline processing.

The initial real model probe completed, so the exact indefinite hang shown in the reported screenshot was **not reproduced**. Baseline face loading took 10.126 s, blank-frame face detection 0.043 s, blank-frame recognition 0.132 s, first body inference 0.498 s, uniform weight loading 0.045 s, and pose loading 0.018 s. Missing/corrupt required weights and universal three-second expiry were not established as causes on this machine. Earring weights are absent and optional. Lock contention, native-call stalls, and crowded-scene latency remain distinguishable through the new diagnostics rather than being presumed causes.

## Revised flow

1. Login, registration, enrollment, and dashboard reuse the supplied face recognizer. A shared readiness registry starts detector and recognition initialization independently and does not initialize a successful component again.
2. The camera owner publishes timestamped, uniquely identified samples. The preview consumes cached samples without waiting for models or database writes.
3. A separate detection worker creates conservative temporary tracks labelled **Identifying** as soon as the face detector is ready. These tracks contain no student identity or disciplinary evidence.
4. Recognition uses a separate model lock from detector inference. It publishes frame-bound identity results before slower uniform assessment. Both streams retain camera/session generations and cancellation; display projection never chooses a student from a new face position.
5. Uniform assessment waits for its own readiness, uses one-to-one face/body association, validates the torso, and retains repeated fresh per-track/per-identity evidence. Optional pose is not loaded by the live path; the face-derived chest region still requires a confidently associated body and the existing torso validation. There is no largest-body fallback. Assessment order rotates across people.
6. A bounded database worker handles accepted results. Freshness, source, current eligibility, projection, cancellation, transaction guards, cooldowns, and unknown-person security records remain enforced. The **three-second expiry has not been loosened**.
7. One committed `pending_review` record is visible only to its owning authenticated student as **Pending review — no strike yet**. Confirmation updates that record, awards the existing strike, and starts the existing five-day appeal window. Five-day automatic confirmation is unchanged. Dismissal and approved appeals remain visible as resolved history.
8. The portal reads the same database object supplied by authentication. It refreshes on navigation/focus, after workflow actions, and every four seconds while the relevant page is open. A worker performs snapshot reads; Tk polls and applies results. Unchanged lists retain their widgets; changed lists preserve the filter and scroll position. Closed sessions and other-owner responses are rejected.

Readiness states are waiting/loading/ready/failed/stalled. A 45-second initialization deadline exposes a specific stalled component; it **does not kill native inference or release its lock**. Retry starts only failed, finished loaders. A still-running initializer cannot be duplicated; restart the application if it never returns. Startup loads local assets only and does not trigger implicit model downloads. Restore missing assets at the reported path or train a classifier, then use **Retry model loading**. Saved uniform references are reused without scanning/rebuilding the training dataset on startup.

Structured JSON diagnostics cover component start/completion/failure/stall, paths/providers, captured/submitted/analyzed frames, rejection reasons, stage elapsed times, face/identity/torso counts, accepted assessments, and write outcomes. They do not include passwords, image pixels, or embeddings. Preview, tracking, and analysis rates are displayed separately.

## Files changed

| Files | Purpose |
| --- | --- |
| `main.py`, `auth/auth_manager.py`, `auth/login.py`, `auth/register.py` | Shared recognizer lifecycle, authoritative account routing, same database injected into portal, structured logging setup. |
| `core/model_readiness.py`, `core/diagnostics.py` | Independent initialization, controlled retries/deadline reporting, structured metadata diagnostics. |
| `core/recognizer.py` | Direct cached ONNX loading, independently owned detector/embedding inference, close-face scale fallback, no implicit downloads. |
| `core/person_detector.py`, `core/trainer.py`, `core/uniform_matcher.py` | Absolute asset paths, file/runtime/label/reference validation, optional pose policy, failure diagnostics. |
| `core/camera.py`, `core/live_pipeline.py`, `ui/dashboard.py` | Capture diagnostics, independent tracking, early identity publication, bounded persistence, fair assessment ordering, status and reconnect fixes. |
| `core/portal_state.py`, `database/db_manager.py`, `ui/student_portal.py` | Owner-scoped pending/resolved history, correct appeal states/counters/filters, background refresh and session guards. |
| `scripts/verify_portal_routing.py`, `scripts/smoke_camera_to_portal.py` | Updated isolation proof and reproducible real-model/camera probe that never saves live detections. |
| Regression tests | Readiness/retry/concurrency, slow assessment and database work, camera/session invalidation, pending ownership/status, same-record confirmation, dismissal, account conflicts, refresh, native portal controls. Existing crossing/torso/appeal/deadline/history suites remain in place. |

## Verification

- **296 headless tests passed** after removing accidental duplicate execution of imported workflow tests. These include temporary SQLite workflow tests with controlled timestamps, crossing/identity/torso ownership, stale/cancelled results, slow persistence, model failure/retry/no duplicate initialization, saved pending isolation, confirmation/appeals/dismissal, historical migration, database-path consistency, and refresh/session isolation.
- **All 18 native UI tests passed** across Live Monitor, student portal, student management, records, and suspensions. The existing layout test now waits for CustomTkinter’s 100 ms tab transition before asserting widget positions. Recreating multiple Tk roots in the combined test process still emits non-failing stale callback warnings; no assertion failed.
- Native Live Monitor tests passed (3 tests), exercising startup/navigation/shutdown with synthetic frames and real Tk widgets. These are UI checks, not camera accuracy evidence.
- Native portal test passed: a new committed pending detection appeared through the four-second timer without logout; another student’s detection stayed absent; filters and unchanged widgets were retained; the same record enabled appeal controls only after confirmation; a dismissed detection remained in Resolved history.
- `scripts/verify_portal_routing.py` passed its two-student temporary-database proof and read-only live-database summary.
- `compileall` and `git diff --check` passed. After the final refresh/persistence adjustments, the affected 113-test headless group also passed.

Reproduce the model probes with `.venv/bin/python scripts/smoke_camera_to_portal.py` and `.venv/bin/python scripts/smoke_camera_to_portal.py --camera --seconds 8`. Run `.venv/bin/python -m unittest discover -s tests` from a desktop-capable session for all tests. In a headless session, exclude the five native modules: `test_live_monitor_native_ui`, `test_student_portal_native_ui`, `test_student_management_ui`, `test_records_ui`, and `test_suspensions_ui`. The native modules require working Tk/desktop access.

Initial sandbox camera access was denied by macOS. A subsequent desktop-authorized real camera probe succeeded and analyzed **10 fresh camera frames with zero stale rejections**, without saving any detections. All four available components loaded successfully: detector **2.699 s**, recognition **2.809 s**, body **2.842 s**, uniform classifier **3.570 s**. Across those frames, median detection time was **0.1165 s** (range 0.0629–0.2715), and median recognition-stage time was **0.5626 s** (range 0.3439–1.2567). **No faces were detected during this run**, so first live face track, reliable identity, and uniform assessment were not observed. This serial diagnostic probe does not measure dashboard preview FPS.

A separate real-model probe on the stored enrollment photo produced a first temporary track at **19.955 s** and reliable identity `2023-00883` at **20.874 s**, including initialization. Median detection/recognition times were **0.0448/0.3981 s**. It found no confidently owned torso and correctly abstained from uniform assessment. These repeated archived-photo observations are **not live evidence or an accuracy benchmark**. A stored training crop exercised the actual uniform classifier in **0.035 s** and returned probabilities; the saved colour reference loaded. This also does not establish uniform accuracy. Sandbox imports/font-cache construction made this run’s startup substantially slower than the desktop camera run; these timings are not a controlled before/after speed comparison.

## Follow-up: visible shirt rejected as mostly skin

The September 29 screenshot showed a ready uniform model and a recognized student,
but the crop was rejected as "Torso obscured or mostly skin." The active fallback
used the entire matched body width, including bare arms, and began only 0.1 face
height below the chin. Its skin calculation also discarded bright white fabric
from the denominator and accepted low-saturation warm white as skin. These are
crop/assessment issues, independent of recognition.

The fallback now selects a central chest region: 1.8 face widths intersected with
the middle 60% of the associated body, starting 0.45 face height below the chin
and ending at most two face heights below it. It requires at least a quarter
face height of visible shirt. Optional pose can further restrict these bounds.
Bright white fabric remains in the skin-ratio denominator. Skin candidates now
require saturation of at least 50 and Cr of at least 135, excluding the measured
warm shirt shadows while retaining the measured exposed neck and arm colours.
This remains a conservative colour heuristic, not skin segmentation. The skin guard
now rejects a majority-skin crop (>50%), allowing a visible neckline when most of
the validated area is clothing. One-to-one body association, overlap validation,
and repeated-evidence thresholds remain enforced. Validated torso geometry stays visible even when classification
abstains; an insufficient crop asks the user to move back and show more shirt.
Body inference errors now have a distinct status. Crop dimensions, method, and
skin ratio are logged without images. The direct InsightFace loader uses bounded
ONNX sessions (two intra-op threads, one inter-op thread, no idle spinning) so
tracking/recognition pools do not starve YOLO. During the live test, unbounded pools
coincided with body inference exceeding five seconds and frames expiring; the
three-second freshness guard has not been extended. InsightFace 0.7.3's public
`get_model` drops session options, so the already-validated local path is loaded
through its `ModelRouter`, which actually forwards them. Error-only session logging
also prevents the fixed output metadata from spamming warnings at 320 input size.

Verification: **165 targeted headless tests and 3 native Live Monitor tests passed**.
New regressions cover a seated sleeveless shirt with bare shoulders, bright white
fabric, bare skin, inadequate visible chest, optional pose bounds, neighbour overlap,
warm white fabric samples versus exposed skin from the actual camera view,
and abstention that draws a torso without creating violation evidence. The actual
uniform classifier assessed the synthetic corrected crop (12.998% skin, 89.8%
wrong-uniform confidence); this verifies execution, not real-world accuracy.
A real camera probe processed **218 frames in 18 seconds with no model errors**,
but detected no faces, so it could not validate a live shirt crop. A separate
stored-photo probe recognized `2023-00883` in four analyzed frames with zero stale
rejections; a stored training crop returned actual classifier probabilities.
Neither diagnostic wrote attendance or violations to the live database.
During the first live restart, the real camera did recognize the student and find
a valid central torso, but the original skin hue band still rejected warm fabric
(up to 98% alleged skin). That live evidence prompted the saturation/chroma fix.
A visible shirt region measured from the app screenshot dropped from 62.7% to
38.7% alleged skin. Replaying the full captured camera view through the actual
face detector, body association, corrected crop and classifier gave a matched
identity, a valid 483×124 torso, 41.26% estimated skin and **98.58% wrong-uniform
confidence**. That crop passes the majority-skin guard. This archived-frame replay
did not write records and is not fresh, repeated violation evidence.
The full application was relaunched with these corrections. In the subsequent
fresh-camera run, the log recorded **18 valid torso crops and 8 accepted uniform
assessments**, with estimated skin fractions between 5.5% and 38.7%. Recent median
stage times were recognition **1.389 s**, body **0.291 s**, and uniform **0.051 s**.
This confirms the live crop/classification path works in the user's scene; it is
not an accuracy benchmark. Attendance committed for `2023-00883`; no new violation
commit was observed in that verification window, so portal visibility for a new
live detection still requires the normal persistence guards and the user's test.

## Deployment and remaining manual checks

Admin and student windows in this application resolve the same absolute `<repository>/data/cbvms.db`, independent of working directory, and authentication passes its actual database instance into the portal. **Separate computers with separate local SQLite files do not share records.** Cross-computer deployment needs an authoritative shared service/database; this change does not add synchronization or duplicate portal records.

Use registered accounts for `2023-00883` and a second student (for example `2023-00246`); the legacy hardcoded shortcut is intentionally unavailable.

1. Run Live Monitor with both students separately, then together and crossing. Record time to first Identifying track, reliable name, and valid uniform assessment separately. Check that optional model failure leaves face tracking active and that ambiguous/occluded torsos remain unassessed.
2. Produce a validated detection for `2023-00883`. Keep both portals open against the same intended database. Within a refresh interval after commit, only that student should see Pending review — no strike yet, no appeal button, no appeal-expired message, and no new strike.
3. Confirm that detection in the admin UI. Check the same record ID becomes confirmed, the strike count changes once, and the five-day appeal deadline starts at confirmation. Verify the second account remains unchanged.
4. Dismiss a different pending detection; check Resolved history and unchanged strikes. Submit a timely appeal on a confirmed detection, then approve/reject it and verify status/history and strike outcome in the same account.
5. Switch/disconnect cameras during loading and inference. Old results must disappear, preview and status must agree on reconnecting, and obsolete work must not write a violation. Restore a deliberately unavailable model in a test installation and retry without spawning duplicate loaders.
6. Verify scrolling/filter retention through refresh, focus away/back, logout/relogin, and no cross-account records. Person-in-view camera accuracy, live multi-person crossing, and time to first live uniform assessment remain unverified by the unattended probe.
