# Live Monitor reliability changes

Implemented against baseline `6f2e611`. Earlier registration/face-capture changes are preserved. No production records, enrolled face images, reference images, or model weights were changed during debugging.

## Root causes confirmed in the active code

- Each face chose a body independently, allowing several faces to claim one body. A failed association fell back to the largest detected body.
- Recognition was applied by overlap to newer UI tracks, without retaining the originating camera/frame context. Crossing people could exchange names or uniform results.
- The recognition worker queued mutable dictionaries before uniform checking changed those same dictionaries. Presence cards could show OK before checking completed.
- Greedy identity matching could give a losing face its second available student identity.
- Per-student uniform smoothing used for display did not govern database writes. One raw wrong-uniform result could be saved without repeated evidence.
- Unknown visitors shared presence/cooldown keys; Clear Alerts reset database cooldowns. The old inference workers had no shutdown mechanism.
- Preview scheduling added a full 33 ms delay after render work. Database statistics ran on Tk, and the FPS label was not a measurement.

## Implemented pipeline

1. The existing camera owner reads frames independently of Tk and inference. Its samples carry a source token, sequence, and monotonic capture time.
2. Tk renders the newest sample at an elapsed-time-compensated cadence. Rendering preserves aspect ratio; mirroring transforms only the displayed pixels and annotations. The FPS display counts distinct successfully rendered samples and reports analysis separately.
3. One serial analysis worker consumes a bounded latest-only queue, offered at most twice per second. Duplicate frames do not rerun inference. Each task freezes a copy of its pixels and carries camera generation, monitor generation, frame ID, capture time, observation time, and cancellation token.
4. Recognition proposes only each face's strongest unambiguous student match. Appearance embeddings and motion maintain temporary person tracks. Crossings with insufficient evidence abstain. Face/body assignment is one-to-one and rejects contested ownership, with no largest-body fallback.
5. Eligible students receive uniform checks only on a validated torso belonging to that body. Crop visibility, minimum size, neighboring bodies/faces, and the existing skin guard are checked. Existing classifier/color fusion and male-only earring eligibility are preserved.
6. Identity requires two distinct fresh observations. Uniform decisions require three supporting observations within the five-observation/eight-second evidence window. Wrong-uniform confidence retains the dashboard's 0.65 threshold; compliance uses 0.60. Earring evidence is independent. Configuration is in `LiveConfig`.
7. One immutable assessment drives overlays, current-presence cards, notifications, and persistence. Absence, uncertain identity/ownership, inference failure, and session changes invalidate dependent evidence. Incomplete checks never mean compliant.
8. Records use the accepted task's original pixels, student, category, and observation timestamp. Freshness and conservative motion continuity are checked before side effects and again inside database transactions. New unknown-person events use `security_events`, outside disciplinary violations.

Violation cooldowns remain per student/category (300 seconds), independent of fragmented tracks and display clearing. Failed writes do not consume cooldowns. Reliable active suspensions retain notifications/sounds with repetition control. Attendance and graduate/unenrolled premises-entry eligibility remain intact. Existing review, appeal, strike, and suspension workflows are unchanged.

Navigation, source changes, stalled/disconnected streams, and shutdown cancel old work. Shared recognizer/person/pose/classifier models serialize their calls. Work already inside native inference finishes before its worker can exit, but cancelled tasks cannot commit afterward.

## Files changed

| Files | Purpose |
| --- | --- |
| `ui/dashboard.py` | Integrate the authoritative pipeline; lifecycle cancellation; independent measured preview; background statistics. |
| `ui/camera_feed.py`, `ui/live_overlay.py`, `ui/live_alerts.py` | Letterboxing, bounded labels, consistent assessment colors/text, stable cards, current/history separation, display-only clearing. |
| `core/live_pipeline.py`, `core/live_state.py` | Frozen frame tasks, bounded worker, ownership/tracking, repeated evidence, guarded side effects. |
| `core/recognizer.py` | Best-match-only identities, ambiguity rejection, immutable embedding evidence, model serialization. |
| `core/model_safety.py`, `core/person_detector.py`, `core/trainer.py` | Shared model locking and atomic trained-weight publication. |
| `core/notifier.py` | Event timestamps and cancellation checks for notifications and queued sounds. |
| `database/db_manager.py`, `database/student_management.py` | Transaction-time validation and separate security-event storage. |
| `tests/test_live_*.py`, `tests/test_recognizer_identity.py` | Deterministic ownership, state, concurrency, persistence, rendering, and native lifecycle regressions. |
| Existing camera/student-management/suspension tests | Migrate private-helper harnesses to the active pipeline and assert navigation cancellation. |
| `scripts/benchmark_live_monitor.py` | Reproducible before/after render benchmark, optionally including native Tk. |

## Verification and performance

Final full suite: **294 tests passed in 8.449 seconds** using desktop-enabled execution. Native dashboard startup, navigation cancellation, shutdown, camera image upload/resize, and alert-card checks passed. Compilation and `git diff --check` passed. Tests use isolated temporary databases; native widget tests use synthetic frames.

An existing suspension scroll-layout assertion failed intermittently in earlier full-suite runs and also failed independently on pristine baseline `6f2e611`; it passed in the final run. The suspension panel implementation was not changed or its assertion weakened. This remains a pre-existing layout/timing check to watch on other display configurations.

Covered: different uniforms on two students; foreground/background and crossing ownership; contested/missing bodies; crop contamination; known/unknown mixtures and distinct presences; identity changes; occlusion and failed-analysis gaps; stale camera/session results; same-frame deduplication; immutable snapshots; student/category/timestamp/JPEG consistency; attendance; suspension warnings; failed writes and cooldowns; camera switching; shutdown; mirror/resize; card reuse; and display-only clearing.

A five-second built-in-camera probe negotiated **AVFoundation, 1280×720, 30 FPS** and read **150 frames at 29.92 FPS**. It stored no images and did not load recognition or touch student records. This measures capture alone.

Five-second native Tk synthetic benchmarks on this arm64 macOS environment used a 720p/30 FPS source and a 960×600 canvas:

| Visible annotations | Load | Before FPS | After FPS |
| --- | --- | ---: | ---: |
| 1 | Preview only | 16.68 | 28.95 |
| 1 | Controlled 200 ms CPU analysis | 17.30 | 27.46 |
| 5 | Preview only | 16.67 | 28.71 |
| 5 | Controlled 200 ms CPU analysis | 16.87 | 27.98 |

The controlled workload is repeated OpenCV blurring, not the recognition/uniform models. Both benchmark versions use the same bounded worker; the before path uses the original renderer and after-work delay from `6f2e611`. The benchmark includes native Tk image upload/drawing but excludes real-camera input, tracker motion projection, alert-card work, real models, and database processing. It establishes rendering/scheduling improvement, not a guarantee of end-to-end camera FPS. Every benchmark worker stopped; maximum active/queued analysis was one each. Headless render measurements were approximately 23–24 FPS before and 26–27 FPS after.

Reproduce:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/benchmark_live_monitor.py --native-tk --seconds 5
CBVMS_CAMERA_DIAGNOSTICS=1 .venv/bin/python main.py
```

Native Tk tests require a desktop session. This sandbox aborts at Tk initialization; approved desktop execution successfully ran those tests.

## Remaining device checks

Real multi-person recognition and uniform accuracy have not been measured end to end. Conservative gates can show Identity uncertain or Uniform not assessed in crowded/occluded scenes; they must not be loosened solely to force a verdict. Results over three seconds old are rejected. Very slow hardware or crowded scenes may therefore assess less frequently; inspect measured preview/analysis rates with the actual models.

On a test database with consenting test participants:

1. Show two known students in different uniform conditions, plus an unknown visitor; move them across each other at different depths. Track labels/cards must agree, and ambiguous bodies must remain unassessed.
2. Hide a torso, leave/reenter, cross quickly, and disconnect/switch the camera while analysis is busy. Old names, compliance claims, sounds, or new records must not carry across uncertain ownership or sessions.
3. Toggle mirror, resize, switch panels, and reopen repeatedly. Verify overlays stay aligned, preview remains smooth, and analysis rate does not multiply.
4. Wait for repeated wrong-uniform evidence, then compare the saved snapshot, student ID, category, and timestamp with that person's assessment. Verify attendance and active suspension warnings. Clear Alerts and confirm records/cooldowns remain intact.
5. Regression-check registration capture, retake, student switching, and submission; the earlier frozen capture behavior must remain unchanged.

Review any questionable historical records manually against their saved evidence. Dismiss or resolve disciplinary records through the existing review workflow. Verify a student's identity before recapturing their enrollment face; do not automatically reassign old identities. Legacy unknown-person violation rows remain untouched; only new events use the separate security table. A dedicated historical security-event browsing screen is outside this change.

## Camera-switch continuation and current network — 2026-10-18

Work resumed on clean `main` at `d1fd606fd381cf85b2240c268120b6b0614f4ae0`,
matching GitHub main at inspection. The older camera implementation was found in
`stash@{0}` on Ecc-Coc, not merged into main. Only its reviewed decoder process,
frame metadata handling, and focused process tests were adapted. The stash and
unrelated work remain untouched. No commit or push was made. The local ECC/Crack
configuration was absent apart from a historical backup receipt; it was not
reinstalled. An independent Astra review checked concurrency and hardware evidence.

### Observed defects and scope of the conclusion

The saved front-gate entry has a `secret_ref` from the earlier Keychain workflow.
Main ignored that reference and opened the credential-free URL. Actual initial
attempts failed in approximately 0.21 seconds and retried repeatedly. Resolving
that existing reference in memory, without changing credentials or stream profile,
restored actual 1920×1080 Dahua video at approximately 20 FPS. This is a confirmed
configuration-compatibility defect, not proof of a native Tk deadlock.

The original switch path was `_on_switch_camera` → `after(1)` →
`_commit_camera_switch` → `show_toast`/layout → `_on_camera_source_connected` →
dropdown reconstruction → `_deferred_start_camera`. The measured commit callback
reached 298.49 ms. The resolved baseline switched successfully but had switching
heartbeat gaps up to 350.76 ms. Its whole-run maximum, 407.00 ms, occurred during
startup. No permanently blocked Tk stack was observed in those automated runs.

A real local RTSP endpoint deliberately accepting TCP without responding showed
another defect: native open remained blocked for 6.06 seconds after stop was
requested. A stop flag cannot interrupt that native call. The decoder process now
allows cancellation without calling native release from another thread. The
real-decoder regression measured approximately 20 ms cancellation and verified
process reaping; a separate EOF test verifies cleanup if the GUI exits abruptly.

### Implementation

- `core/camera_secrets.py` and `api/camera_store.py` resolve existing Keychain
  references on the camera worker with cancellation and an eight-second deadline.
  Resolved credentials never enter verified-source preference files or API metadata.
- `core/network_capture.py` / `core/network_camera_worker.py` isolate IP decoding,
  keep bounded latest-only frames, preserve acquisition timestamps, classify native
  errors without forwarding credentials, and keep FFmpeg environment changes inside
  the child. Parent EOF ends an orphaned decoder. Normal cleanup terminates/reaps it.
- `core/camera.py` retains a usable first frame. Native local capture still has one
  owner for open/read/release; only network capture uses a decoder process.
- `ui/dashboard.py` separates requested and active sources. Activation/persistence
  follows a valid current-generation frame; inference provenance uses the active
  source instead of the dropdown text. Existing analysis cancellation remains in use.
  Switching no longer creates a toast or rebuilds menu entries. Tk only signals
  stop; it does not join a camera owner or wait for its lock.
- USB locking remains exclusive. IP workers/probes do not take the USB lock.
  Device-lease and previous-owner waits end after three seconds. A stalled local
  native owner is contained with a visible error; it is never released unsafely from
  Tk. First-frame deadlines are ten seconds for USB and eighteen for IP (including
  credential resolution). Superseded events/timers cannot activate an old source.
- A failed manual attempt does not retry automatically. An established source gets
  at most three reconnect attempts with 1/2/4-second backoff; a new choice, navigation
  or shutdown cancels pending timers. Errors expose Retry, Return to MacBook Camera,
  and Camera Settings. The feed does not overwrite connection errors with a generic
  endless reconnect message.
- Settings retains unreachable saved cameras when credential lookup fails, and
  scanning can be cancelled. No network address, firmware, camera profile, recognition
  threshold, model, enrollment record or production database was changed.

### Reproduction and outstanding acceptance

Close other camera readers, then run:

```sh
.venv/bin/python scripts/verify_camera_switch.py
# Physical native-popup interaction, with a disposable database:
.venv/bin/python scripts/verify_camera_switch.py --manual
```

The script uses installed models and empty temporary student data, preserves saved
preferences, retains timing-only JSONL and watchdog stacks, and saves no frames.
CPU measurements are parent-process CPU, can exceed 100% across cores, and exclude
child decoder CPU. Automatic checks invoke the actual menu callback; they do not
simulate a physical click in the macOS native popup. The manual mode closes after
two minutes; a heartbeat stall over eight seconds records stacks and exits.

Automated actual-camera switching has succeeded repeatedly, with normal tracking
and analysis resuming. **Physical native-popup click acceptance is still pending.**
The originally reported permanent dropdown freeze was not reproduced, so this work
must not be described as proof that every native macOS menu freeze is fixed.
A human should select front gate, switch back, select it again during analysis,
and navigate while it is connecting in manual diagnostic mode. Report any freeze
with that run's timing/stack files. No participant recognition-accuracy claim is
made from empty-gallery hardware runs.

### Verified network after the Wi-Fi change

Read-only checks on October 18 confirmed:

| Connection/device | IPv4 | Role |
| --- | --- | --- |
| Mac Wi-Fi (`en0`) | `192.168.100.74/24` | Internet via `192.168.100.1` |
| Mac USB Ethernet (`en5`) | `192.168.1.10/24` | Camera LAN |
| Saved front gate | `192.168.1.108` | HTTP 80 and RTSP 554 responding through `en5` |

`192.168.1.10` is the Mac's Ethernet address, not the verified camera address.
A fresh saved-stream probe returned 1920×1080 frames, first frame in 2.025 seconds,
19.98 FPS over the following three seconds, and successful release. Changing the
Wi-Fi connection did not require a camera IP change. Keep the current direct-camera
Ethernet settings and the saved front-gate entry. Moving the camera cable to the
router would be a different topology and requires verifying the router's assigned
camera address before changing CBVMS; do not guess a new address.

### Final verification results

The final 68-second hardware scenario used the current saved configuration, installed
models and disposable data. It produced three front-gate activations and four USB
sessions, including rapid supersession and navigation during connection. The rapid
request was cancelled before the IP owner started; cancellation *inside* native open
is established separately by the loopback decoder test, not by that hardware step.

| Measurement | Observed |
| --- | --- |
| Gate selection to usable frame | 2.401 / 2.513 / 2.471 s |
| Return to USB | 1.105 / 1.112 s; rapid return 0.943 s |
| Maximum heartbeat gap during transition/navigation windows | 122.77 ms |
| Whole-run heartbeat maximum (startup) | 525.40 ms |
| Dropdown callback / switch commit maximum | 0.154 / 1.102 ms |
| Native/capture release maximum | 127.49 ms |
| Window close callback | 38.16 ms; owner completion confirmed |
| MacBook source rate | 29.29–29.98 FPS (startup included) |
| Front-gate source rate | 19.96–20.05 FPS |
| MacBook settled preview / tracking / analysis session medians | 19.37–20.60 FPS / 3.94–5.04 per s / 1.42–1.69 per s |
| Gate settled preview / tracking / analysis session medians | 19.55–20.01 FPS / 7.09–7.22 per s / 1.74–1.78 per s |
| Settled parent CPU session medians | USB 342–652%; gate 192–195%; excludes decoder child |
| Callback errors / watchdog hangs | 0 / 0 |

These are scene-dependent throughput measurements, not face/uniform accuracy scores.
MacBook source capture stayed approximately 30 FPS; baseline preview/model rates
vary with scene content and startup, so no universal preview-speed claim is made.

Verification exercised 202 distinct focused backend/camera/CV/enrollment tests. An
initial run found one timestamp regression against a native-capture fake; native USB
reads now use local acquisition timestamps explicitly. The affected 81-test rerun
passed; the other 121 focused tests passed in the prior run. Twelve distinct native
Tk/enrollment/loopback tests passed across the two native runs (the MJPEG test was
run in both). Astra independently reran 36 activation/process/switching tests and
approved the scoped concurrency changes after its two findings were corrected.
Python compilation and `git diff --check` passed. No credentials appeared in diagnostic
output; sentinel-secret tests cover decoder errors, API metadata and preferences.

Timing evidence is retained locally at `/private/tmp/cbvms-switch-audit/`:
`resolved-baseline.jsonl`, `updated.jsonl`, and `final/timings.jsonl`. Re-run the checked-in
script to regenerate evidence if temporary files are cleared. Hardware probing was
sequential, with each camera reader released before the next test.

The working tree has 11 modified tracked files and 8 new camera/diagnostic/test files;
changes are uncommitted. No unrelated work was discarded. The unresolved acceptance
item is the physical native-popup interaction described above, not network access.

## Native-dropdown investigation resumed — 2026-10-19

The historical working-tree counts above describe the earlier session, not current
status. This investigation resumed with a clean checkout at `2535189`, matching
remote HEAD/main. Recovered automated hardware logs have no hang events and empty
stack files; they do **not** reproduce or resolve the physical dropdown freeze.
No application behavior fix is justified by that evidence yet.

The diagnostic script now defaults to durable `diagnostics/camera-freeze/<UTC>/`
output. A separate Python process detects heartbeat stalls even when the GUI holds
the GIL, signals faulthandler, and requests macOS process samples. Only the native
call graph is retained (no process header or binary paths); Python dumps omit locals.
Three captures maximum, at least 15 seconds apart, bound diagnostic capture.
Manual sessions run for ten minutes while responsive. The supervisor expires after
15 minutes; it does not kill the application. Native-menu open/button/CTk callback
entry and return, camera generations, device lease waits, credential resolution,
open/release, frame/analysis rates and cleanup are traced without source URLs.
A heartbeat pause while deliberately holding the native menu open is not by itself
a deadlock: correlate it with selection/return events and recovery after dismissal.

Run from the repository, with other camera readers closed:

```sh
.venv/bin/python scripts/verify_camera_switch.py --manual
```

Wait for moving MacBook video and nonzero Analysis, physically open Source, click
front gate, and leave any freeze untouched for 25 seconds. Then report the printed
output directory and whether the menu closed, clock stopped, or only video stopped.
If responsive, select MacBook again. If still frozen after capture, use Force Quit
on the diagnostic window's Python process. Do not run competing camera readers.
The disposable database has no enrollment gallery: model inference can run, but
this cannot verify recognition accuracy against enrolled identities.

See `CAMERA_FREEZE_CHECKPOINT.md` for exact results and the remaining acceptance
matrix. Current changes improve evidence collection; they do not establish a fix.
