# Enrollment capture recovery

## Proven blocker

The pre-fix code at commit `6a528ac` made optical flow a prerequisite for enrollment readiness. `FacePreviewTracker.reset()` leaves its sample/points unset when fewer than six corners are found. `_wizard_tick()` then sees `advance()` return `None` and calls `CaptureSession.invalidate()`. That clears the previous authoritative face detection. Every valid detection is therefore treated as the first observation, never the second consistent observation needed for Ready. Another flow-only check also cancels a capture request before its fresh face-validation result can be applied.

Two regressions failed before implementation: three valid centered detections with `goodFeaturesToTrack=None` never reached Ready; a valid post-click detection could not freeze while flow initialization failed. Both pass after the fix. This proves the reset-loop defect in the repository. The screenshot alone does not establish whether that particular camera exposure lacked corners, lost established tracking, or experienced slow inference; the new bounded diagnostics distinguish those conditions.

Additional findings:

- Tracking corners were reduced after each optical-flow pass without replenishment.
- Preview and inference were already scheduled independently; that separation is retained.
- Enrollment model failures returned an empty face list, conflating model failure with no detected face.
- Capture requests lacked an independent deadline and explicit request identity.
- Readiness used fresh detections and embedding continuity; it did not require matching an already enrolled student. That remains true.
- The three-angle wizard moved the guide but did not measure head pose. It still uses guided poses and operator confirmation; the interface now says so explicitly.
- Student/account persistence used separate operations, and some saving/callback work could block or cross Tk threads. The revised path saves the reviewed payload on a worker, delivers UI callbacks on Tk, and creates the student and account atomically.

## Implementation

`core/face_capture.py`

- Searching → validating target → Ready → validating capture → frozen review, with recoverable failure messages.
- Optical-flow initialization failure cannot erase valid detection observations. Established flow loss can still reject an in-flight capture, and the detector reacquires automatically.
- Two distinct, ordered, fresh detections must still agree at the existing IoU ≥ 0.30 and embedding cosine ≥ 0.55 thresholds. No recognition/classifier thresholds were relaxed.
- Repeated cached samples and older results do not establish readiness. Missing/invalid embeddings, competing guide targets, source changes, and identity replacement cannot complete capture.
- Each request has a sequence token and monotonic request time; only an exposure after that click may complete it. A logical request times out after **4 seconds**. Rejected, cancelled, and frozen requests emit metadata-only diagnostic events.
- The frozen object owns read-only copies of the validated raw frame and embedding. Its JPEG is cropped from that same frame. `capture_payload()` additionally rejects mixed-student or inconsistent-identity captures.
- Preview flow keeps the existing minimum six-point checks and replenishes surviving tracks toward 40 corners when fewer than 20 remain.

`ui/enrollment.py`

- Readiness comes from detector/embedding validation. Missing preview flow clears the overlay and announces reacquisition, without repeatedly discarding the detector's history.
- Latest camera samples are rendered independently of inference. One validation worker owns the panel lock, and the result queue is bounded to one. Its input frame is copied before asynchronous inference.
- Results are gated by wizard/session, student, camera source, request token, exposure time, and freshness. Pre-click, cancelled, previous-angle, and previous-student results cannot freeze a new request.
- The Capture button immediately shows **“Validating capture…”**. Timeout, model error, stale camera, lost target, and ambiguity produce recoverable feedback. Copy/allocation failures and worker-start failures release the lock and clear busy state.
- Back, Cancel, widget destruction, and reopening cancel logical capture work. An already executing native call retains its lock until its `finally` block returns; the UI does not unsafely release a lock held by an active detector. A native model call that never returns requires an app restart, but the capture request/UI do not remain pending indefinitely.
- The anonymous target embedding is retained across confirmed and skipped angles. Each angle still needs fresh validation; replacement requires the original person to return or a deliberate restart.
- The instructions refer to the person's own left/right and disclose that pose is not measured. Mirroring and resizing remain display transformations of raw image coordinates. Clipped faces retain the safety rejection with a specific “move farther away” message; faces are not silently cropped across image boundaries.
- Frozen review/Retake remain. Final review adds Previous/Next controls for the actual confirmed captures. Existing skip rules remain: no zero-capture save, and confirmed captures alone supply the photo/embeddings.
- Save runs off Tk. Failed saves retain entered details and confirmed captures. Duplicate clicks are blocked, the recognition gallery reloads after success, and optional credential email delivery does not decide whether enrollment committed.
- Structured `enrollment_capture_state` diagnostics run at most twice per second per wizard. They report a random wizard identifier, step, camera frame IDs/age, validation duration, face-selection outcome, readiness/phase, tracking failure, and worker/lock state. They contain no face images, embeddings, passwords, or student names. Existing five-second preview metrics remain available with `CBVMS_CAMERA_DIAGNOSTICS=1`.

`core/recognizer.py`

- Enrollment now reports model initialization/detection errors instead of pretending they were an empty face detection. The same detector, embedding models, quality/confidence thresholds, and gallery matcher remain in use.

`database/db_manager.py`

- An optional account password lets admin enrollment insert the student, contacts, reviewed photo, reviewed embeddings, and account in one transaction. Account conflicts roll back the student insertion. Existing callers retain their previous behavior.
- Update Photo can require the expected student ID in the write itself, protecting against a record being remapped between review and commit.
- No schema migration or historical data repair is needed for this fix. Tests use temporary databases; the production database was not used for test writes.

Tests changed: `tests/test_face_capture.py`, `tests/test_enrollment_preview.py`, `tests/test_enrollment_recovery.py`, and `tests/test_enrollment_native_ui.py`.

## Thresholds and timing

The existing 250 ms validation interval (at most four per second), 1.5-second frame/observation freshness limit, two independent observations, and identity/geometry thresholds are retained. A 200 ms inference benchmark completes inside that interval. The four-second request deadline accommodates an in-flight validation, a post-click validation, and scheduling margin while retaining the 1.5-second freshness check on any frame actually accepted. Slow validations fail explicitly instead of relaxing freshness or saving the newest unrelated camera frame.

A frozen review represents the validated exposure. It is not a guarantee of continuous presence after that exposure, nor proof of a person's legal identity or measured head angle. The operator still checks the person and pose before confirming.

## Verification results

The debug loop first reproduced the defect, then reran selection/preview/state tests, exercised native UI workflows, and ran all repository regressions. Tests use synthetic exposures and model outputs with real camera-sample interfaces, real Tk widgets/event loops, temporary SQLite databases, the real gallery loader, and the existing recognition assignment logic.

The native harness drives `CameraCapture.read()` / `get_latest_sample()` through a fake camera driver at 30 FPS. It forces optical-flow initialization to fail, exercises the real enrollment form and Capture/Confirm/Retake/Save buttons, verifies post-click frame IDs and exact JPEG bytes/embeddings, and checks background save/retry behavior. No physical camera is opened by these tests.

Native scenarios:

- **3 complete front/left/right enrollment attempts, 3 successful, 0 unexpected failures.**
- **12 normal capture requests, 12 frozen captures**, including Update Photo/retake and the save-retry scenario.
- Departure, replacement, source switching, and a deliberately blocked detector produce **4 expected capture rejections**, with no student silently saved.
- One deliberately failed database save retains details/captures; retry succeeds. Update Photo preserves the other student's record.
- Back during inference, reopening, disconnect/reconnect, final review navigation, and double clicks are exercised.
- Recognition compatibility uses the real gallery loader/matcher with deterministic model embeddings. It does not claim physical-camera recognition accuracy.

Final results:

- `.venv/bin/python -m unittest discover -s tests`: **400 tests passed in 102.868 seconds**, no failures/errors/skips reported. Includes existing detection, appeal, evidence, disciplinary, and registration regressions.
- Final focused selection/state/preview/persistence run: **63 tests passed in 0.420 seconds**.
- Native tests are included in the full run. Across 12 normal requests, time to Ready was **367.4–460.5 ms**; capture validation was **114.0–419.9 ms**, median **135.65 ms**.
- The blocked-detector test rejected its request at **4.042 seconds** (the four-second logical deadline plus UI scheduling), with **107 live preview renders** during the wait. Its late result was discarded and subsequent detection recovered.
- `compileall` and `git diff --check`: passed.

The debug loop also caught a timeout message being overwritten by a generic stale-validation message; that is fixed and covered. Existing assertions were updated for the more specific stale-camera message, immutable copied worker input, and recoverable thread-start failures. The final full run includes the last continuity guard across skipped, unconfirmed angles.

Headless scheduling benchmark (3 seconds per mode, synthetic 720p/30 FPS source, 200 ms simulated inference):

| Measurement with validation | Before | After |
| --- | ---: | ---: |
| Preview FPS | 28.32 | 27.33 |
| UI tick p95 | 8.37 ms | 8.66 ms |
| Rendered frame age p95 | 35.41 ms | 35.99 ms |
| Inference calls | 11 | 11 |
| Maximum inference in flight | 1 | 1 |
| Maximum queued UI ticks | 1 | 1 |
| Frame sequence rewinds | 0 | 0 |

Preview-only measured 27.66 FPS before and 29.66 FPS after. These short synthetic measurements demonstrate that preview remains independent and bounded; they are not a claimed speedup or a physical-camera FPS guarantee.

Reproduction commands:

```sh
.venv/bin/python -m unittest tests.test_face_capture tests.test_enrollment_preview tests.test_enrollment_recovery
.venv/bin/python -m unittest tests.test_enrollment_native_ui
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/benchmark_enrollment_preview.py --seconds 3
```

To repeat the same baseline comparison:

```sh
git show 6a528ac:ui/enrollment.py > /tmp/enrollment-baseline.py
.venv/bin/python scripts/benchmark_enrollment_preview.py --seconds 3 --before-source /tmp/enrollment-baseline.py
```

## Physical-camera checks still required

**Physical-camera attempts: 0.** No attended real-person trial was run, and no real-world reliability percentage is claimed. Detector performance with the user's lighting, glasses, close framing, and natural head turns still needs this supervised check:

1. Restart the app. Open Student Management → Enroll New Student with a disposable test ID. Start with one centered person and verify automatic Ready, including brief movement and reacquisition.
2. Capture front, own left, and own right. Check each frozen image; Retake one angle, then use the final Previous/Next controls to inspect every confirmed capture before saving.
3. Repeat with mirror on/off, different preview/window sizes, a background person outside the guide, and competing faces inside it. Outside bystanders should not be selected; competing targets must block capture.
4. After clicking Capture, leave the guide or have another person replace the target. Also disconnect/reconnect or switch sources. Verify either the exact validated intended-person exposure or an actionable rejection, never a background/replacement person.
5. Exercise Back, Cancel, reopen, double clicks, and Update Photo. Confirm other records remain unchanged and the new student is recognized in Live Monitor.
6. Record attempts, successful full sequences, rejection reasons, time to Ready/capture, and preview responsiveness. If a failure remains, retain the bounded diagnostic events—not face images/embeddings—for investigation.

Enable preview metrics when starting the app:

```sh
cd /Users/jopethones/Documents/GitHub/cbvms3.0
CBVMS_CAMERA_DIAGNOSTICS=1 .venv/bin/python -u main.py
```
