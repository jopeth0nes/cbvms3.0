# Student Registration preview performance

This follow-up changes the **Register window opened from login** (`auth/register.py`).
Existing enrollment/save safeguards and unrelated working-tree changes are preserved.

## Cause

The previous `_live_tick()` acquired camera frames but called `_render_frame()` only
when `enrollment_faces()` returned. Full SCRFD/ArcFace processing therefore set the
visible preview rate. While the model was loading, no live frames were rendered.

Additional contributors were synchronous `VideoCapture.read()` on the Tk thread,
`after(40)` scheduled after that read/work, repeated unchanged widget configuration,
and continuous full inference dispatch with no interval between completed requests.
Camera opening tried Windows backends on every platform, without requesting or
reading back resolution/FPS. A late camera open after window closure could leak a
handle; camera open/read/release also ran on different threads. Inspection did not
find two active preview callbacks in a normally opened registration window; the
primary regression was inference-gated rendering, not duplicate normal-loop starts.

## Changes

- `auth/register.py`: render each new camera sample independently of model loading
  and inference; compensate callback scheduling for render time; resize to preview
  dimensions before overlays/RGB conversion. Update labels/buttons only when their
  state changes. Dispatch at most four full validations/second with one in flight.
- `core/registration_camera.py`: a single owner opens, reads and releases the camera;
  holds only the newest sample, shares the existing device ownership lock, and
  cleans up late opens. New owners wait for previous release. Includes measurements
  of unique frames rendered, source delivery, preview gaps, and inference duration.
- `core/camera.py`: expose backend-reported settings. Registration reuses this
  existing platform-aware camera implementation, requests 1280×720 at 30 FPS, and
  inspects what the backend actually reports. Unsupported requests retain the
  negotiated native settings; zero/unknown reported FPS is not treated as proof
  of throughput. Capture pixels keep the negotiated resolution.
- `core/face_capture.py`: lightweight optical flow keeps the last selected face's
  highlight aligned with current pixels between validations. Uncertain tracking
  clears the highlight and cancels pending capture. This tracker supplies no
  embedding or saved crop. Full validation of a fresh post-click frame, ambiguity
  rejection, identity continuity, frozen JPEG/embedding pairing, student binding,
  retake and submission checks remain authoritative.
- Registration reuses its recognizer across windows under the same login, serializes
  model loading/inference, cancels its UI callback on close or parent destruction,
  and discards obsolete work. Worker threads never call Tk. A driver/native model
  call already executing is allowed to return; no replacement camera opens over it.

## Measured comparison (headless synthetic test)

Five seconds per mode, simulated 30 FPS 1280×720 source, 200 ms simulated full face
validation. Runs the actual registration callback/worker scheduling and OpenCV/PIL
rendering, with Tk widgets/image upload replaced. These are **not physical-camera,
real InsightFace, or on-screen compositor measurements**.

| Mode | Before rendered FPS | After rendered FPS | Before p95 frame gap | After p95 frame gap |
|---|---:|---:|---:|---:|
| Preview while model unavailable | 0.0 | 29.2 | no frames | 34.0 ms |
| Preview with validation | 4.4 | 29.0 | 247.5 ms | 36.7 ms |

Afterward the simulated source delivered 30.0 FPS. With validation, the p95 UI tick
cost was 7.5 ms; 19 full validations ran over five seconds. Maximum in-flight
inference and queued preview callbacks were each one. The baseline acquisition
loop consumed about 24 FPS because it waited 40 ms between callbacks.

Reproduce the current synthetic benchmark:

```sh
.venv/bin/python scripts/benchmark_registration_preview.py --seconds 5
```

The same harness supports `--before-source /path/to/earlier/register.py`; the above
baseline used the exact pre-follow-up working copy saved before editing.

## Automated checks

```sh
.venv/bin/python -m unittest tests.test_registration_preview tests.test_face_capture tests.test_camera_switching tests.test_camera_violation_integration tests.test_student_management tests.test_violation_workflow tests.test_reports tests.test_suspensions
```

Coverage includes preview during model loading/slow inference, duplicate cached
frames, throttling, current-frame rendering instead of old inference images,
tracking loss, exact frozen capture, retake, stale result rejection, unchanged
submission checks, student changes, callback cancellation, parent destruction,
close during delayed camera open, five reopen cycles, release failure recovery,
recognizer reuse, and requested versus reported camera settings. Database tests
use temporary databases; no existing face records were changed.

## Verify on the deployment device

The local Tk runtime aborts with exit 134 even for a hidden empty window, so native
rendering, the actual camera, and real-model CPU contention remain unverified.

1. Close the previous app instance and other camera users. Restart with diagnostics:
   `CBVMS_CAMERA_DIAGNOSTICS=1 .venv/bin/python main.py`.
2. Open Register from login. The console reports the backend's actual resolution
   and FPS. Every five seconds, `[Registration preview]` reports measured source
   FPS, unique rendered FPS, p95 preview gap, and p95 inference time. Observe while
   the model loads (preview alone) and after face validation starts. For a camera
   actually delivering 30 FPS, look for roughly 27–30 rendered FPS and no persistent
   jump to detection-speed updates. Devices delivering less than 30 should have
   preview throughput close to their measured source rate. Reported FPS alone is
   not the acceptance criterion.
3. Center one face, capture, verify the frozen crop, retake, then repeat with an
   outside bystander and with ambiguous overlap. Leaving the guide during a capture
   request must cancel it. Use a designated test student to check submission and
   reopen their photo; do not overwrite an existing student's record for testing.
4. Close/reopen registration five times, including during model/camera loading and
   inference. Confirm the camera indicator goes off after closure, no old window
   updates appear, capture works again, and measured FPS does not degrade. Also
   close the login window while registration is opening to exercise parent cleanup.

An unresponsive native driver can delay release until its open/read call returns;
reopening waits rather than opening another stream concurrently. Real face tracking
and recognition under low light/rapid motion still require the device checks above.
