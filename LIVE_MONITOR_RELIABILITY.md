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
