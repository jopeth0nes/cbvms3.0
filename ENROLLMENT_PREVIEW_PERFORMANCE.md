# Student Management enrollment preview FPS fix

This change targets **Student Management → Enroll New Student → Face Capture**,
the panel in the September 26 screenshot. Update Photo shares this wizard and
receives the same improvement. The earlier performance change targeted the separate
Register window opened from login.

## Root cause and implementation

The dashboard already acquires frames on a dedicated camera owner thread, normally
requesting 1280×720 at 30 FPS. However, `EnrollmentPanel._wizard_tick()` only rendered
frames when a full InsightFace result arrived, and it rendered the older analyzed
frame. Full recognition latency therefore controlled visible FPS and frame age.
The callback additionally waited 50 ms after finishing its work and repeatedly
configured unchanged widgets.

Changes in `ui/enrollment.py`:

- Render the newest unique camera sample independently of full face validation,
  using a 33 ms cadence that subtracts time spent rendering.
- Resize before drawing overlays and converting to RGB; preserve the original
  full-resolution pixels for recognition and frozen captures.
- Reuse the existing small optical-flow tracker to move the selected-face box into
  current pixels. Mirror the current frame and its overlay together.
- Limit expensive validation to four starts/second with only one running call.
  A panel-level lock also covers obsolete workers after Back, Close, or reopening.
- Update status/buttons only when their values change. Reset performance windows
  after intentional frozen-review pauses.
- Preserve guide selection, ambiguity rejection, frame freshness, student binding,
  exact frozen JPEG/embedding pairing, confirmation and Retake. Optical flow never
  supplies a saved crop or embedding. Check current tracking **before** accepting
  a delayed pending capture result, so departure in that same callback cancels it.

`ui/dashboard.py` now reads actual camera settings on the camera owner thread and
prints them when diagnostics are enabled. Unsupported readback does not break the
stream. Existing resolution/FPS preferences and single-camera ownership remain.
No normal duplicate Live Monitor inference loop was found: that view stops offering
work when navigation leaves Live Monitor. No existing student records were changed.

## Measured comparison

Five seconds per mode using the actual enrollment callback, image processing,
optical flow, and background worker scheduling. The source is synthetic 1280×720
at 30 FPS, preview 520×390, with simulated 200 ms full validation. Tk widgets/image
upload and physical-camera acquisition are substituted.

| Measurement | Before | After |
|---|---:|---:|
| Unique rendered FPS, validation paused | 0.00 | 29.20 |
| Unique rendered FPS, validation enabled | 4.20 | 28.99 |
| p95 preview frame gap, validation enabled | 255.86 ms | 36.86 ms |
| p95 displayed-frame age | 288.31 ms | 35.91 ms |
| p95 frames behind source | 8 | 1 |
| Status-widget updates per five seconds | 98 | 3 |

Maximum in-flight validation calls and queued preview callbacks both stayed at
one. There were no backward frame-sequence renders. These measurements demonstrate
the scheduling/rendering fix; they do not establish real-device FPS or real-model
CPU contention.

Reproduce the current benchmark:

```sh
.venv/bin/python scripts/benchmark_enrollment_preview.py --seconds 5
```

The harness accepts `--before-source /path/to/old_enrollment.py`. The recorded
baseline used the exact pre-change working copy saved before editing, not the
repository HEAD, which predates earlier capture fixes.

## Verification and device check

Regression coverage includes independent preview while validation is busy, cached
frame deduplication, throttling, current-frame highlights, mirror/resize behavior,
missing/stale samples, source/student changes, target departure while a capture
result arrives, exact frozen captures, Retake, closed widgets, and worker cleanup
across reopening/errors. Existing save/registration/camera tests remain in place.
The final run passed 145 tests, including 23 new enrollment-preview tests. Syntax
compilation and `git diff --check` also passed. The table records the final after
measurement; an earlier run measured 28.59 FPS with validation, showing similar
throughput across both runs.

```sh
.venv/bin/python -m unittest tests.test_enrollment_preview tests.test_face_capture tests.test_registration_preview tests.test_camera_switching tests.test_camera_violation_integration tests.test_student_management tests.test_violation_workflow tests.test_reports tests.test_suspensions
```

The local Tk runtime still aborts with exit 134 when creating a hidden empty window.
Actual camera throughput, native Tk image upload/compositing, and real InsightFace
CPU contention remain unverified here.

Close the old app instance and restart on the deployment machine with:

```sh
CBVMS_CAMERA_DIAGNOSTICS=1 .venv/bin/python main.py
```

Open **Student Management → Enroll New Student → Face Capture**. Console output
shows `[Dashboard camera]` requested and reported settings and, every five seconds,
`[Enrollment preview]` measured unique preview FPS, frame gaps, validation turnaround,
and source FPS observed by this preview consumer. The observed source measurement
is not a count of all camera-worker reads. Check live-phase measurements: a device
actually supplying 30 FPS should stay close to 30 displayed FPS during validation.
An intentionally frozen preview is labelled `phase: review`.

Verify centering, a bystander outside the guide, ambiguous overlap, leaving during
Capture, mirror toggling, all angles, Retake, Back/reopen, and Update Photo. Use a
designated test student for any save. Repeated reopening should not add inference
workers or degrade preview rate. If the reported/measured source rate itself is low,
check the camera's supported mode and lighting separately from preview rendering.
