# Face capture investigation and verification

## Root cause

The Student Management oval was decorative. Its live status used `has_face()` on
any face in the frame. Capture collected eight full frames without selecting or
tracking the person in the oval. At save time, `encode_face_multi()` reran
InsightFace and selected the highest detection confidence in each frame, then
averaged those embeddings. That could combine different people. It returned a
box from the highest-confidence frame, while `_first_frame()` independently chose
a different frame for the saved photo crop.

Self-registration had the same failure: it froze a full-frame preview, then read
additional camera frames during submission and ran the confidence-based encoder.
The photo, embedding, and visible preview could consequently disagree.

## Implemented flow

1. Camera samples carry copied raw pixels, a source/frame identifier, and a
   monotonic acquisition time. Cached copies do not count as additional frames.
2. InsightFace returns each box paired with its embedding from that one frame.
   A shared selector uses the same normalized oval geometry as the rendered guide.
   The face center must be inside the oval, its box must be valid and unclipped,
   and only one detected face box may intersect the oval. Order, confidence, and
   face size do not rank candidates. Scores still serve the existing detector's
   minimum-quality filter.
3. Consecutive fresh samples must agree geometrically (box IoU >= 0.30) and in
   identity (embedding cosine similarity >= 0.55). Samples older than 1.5 seconds,
   camera changes, missing faces, and ambiguous overlaps invalidate the target.
   These are conservative continuity gates, not proof of identity or liveness.
4. Capture waits for an acquisition **after** the click. Losing the target or
   continuity cancels the request. A confirmed angle must also agree with the
   first accepted angle's identity. Retake creates a new session; results from
   earlier sessions cannot populate it.
5. The selected raw-frame box is highlighted. Overlays and camera pixels are
   resized and mirrored together. Each immutable capture owns its raw frame,
   selected box, embedding, exact JPEG crop, and student/session identity.
6. Each angle freezes an unmirrored portrait preview decoded from the exact JPEG
   bytes that will be saved. Use This Capture confirms the angle. The final
   review shows the primary photo with Save and Retake controls. Save stores that
   JPEG and the confirmed angles' individual embeddings; it does not read the
   camera or rerun detection. This deliberately replaces the old untracked burst
   averaging with one verified, previewed frame per captured angle.
7. Student identity is checked again before the existing database insert/update.
   Changing selection, editing registration identity, returning to details,
   closing, and retaking invalidate pending work. Database writes consume the
   reviewed payload on the UI thread, so a delayed inference callback cannot
   choose another record. Existing student-ID/contact validation, account creation,
   registration-pending status, and recognition duplicate-assignment logic remain.
   No enrollment duplicate-face rejection existed in these capture paths.

Both admin enrollment/Update Photo and self-registration use this selection and
frame binding. Self-registration requires a valid captured face before Register;
it no longer falls back to saving an arbitrary full frame when face inference
fails or registering without a capture when the camera is unavailable.

## Automated verification

Run:

```sh
.venv/bin/python -m unittest tests.test_face_capture tests.test_camera_switching tests.test_camera_violation_integration tests.test_student_management tests.test_violation_workflow tests.test_reports tests.test_suspensions
```

The focused regression tests cover:

- One centered person; additional people behind/beside and outside the guide.
- Larger/higher-confidence outside faces and reversed detection order.
- Empty guide, invalid boxes, and multiple overlapping faces (including a second
  face whose center is outside but whose box intersects the guide).
- Target departure, identity replacement, camera-source change, stale/repeated
  samples, and an in-flight pre-click result.
- Every angle's scaled guide geometry; matching pixel/overlay mirroring.
- Retake and stale callbacks; changed/deleted/remapped student state at Save.
- Exact crop bytes and embeddings tied to their source frame, without aliasing
  mutable camera/detector buffers.
- Admin enrollment, Update Photo, and registration submission with no new
  detection or camera read; temporary SQLite round-trip and unrelated-record
  preservation.

Tests use synthetic boxes/embeddings and temporary databases. The local Tk
runtime aborted (exit 134) on a minimal hidden-window initialization. Actual
widget layout, physical-camera behavior, InsightFace accuracy/latency under
real multi-person scenes, and production-database integration remain unverified.
No production records were modified or deleted.

## Live acceptance and existing-record review

On the deployment machine, repeat the scenarios above with the actual camera,
including mirror toggling, each angle, and all preview sizes. Check that the green
box follows only the person in the guide, departure cancels an outstanding
capture, ambiguity blocks capture with “Only one person should be inside the
guide.”, and an empty guide shows “Position your face inside the guide.” Confirm
the frozen portrait before Save. Reopen the student's stored photo and verify
recognition with that student alone. Low frame rates or slow inference may cause
the freshness gate to reject captures; measure this on the deployment hardware
before changing the conservative limit.

For suspected earlier mistakes, an authorized operator should select the student
in Student Management, open their stored photo, and verify it against the student
and institutional identity records. A correct-looking photo alone cannot prove
an old embedding is correct because the old code could mismatch them. Use Update
Photo to recapture all available angles with the verified student present and
explicitly review before saving. If the photo depicts someone else, do not infer
or automatically reassign their identity. This fix performs no migration,
reassignment, deletion, or automatic overwrite of existing face records.
