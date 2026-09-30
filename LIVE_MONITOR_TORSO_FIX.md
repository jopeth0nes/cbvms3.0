# Coordinated Live Monitor verification — 2026-09-29

## Confirmed stopping points

- The last reported session did schedule uniform work: its log contained eight valid torso crops and eight completed classifier calls. Missing model files were not the cause of the missing overlays. Optional earring weights were absent, but did not prevent uniform inference.
- Fast detection, early identity publication and final assessment had independent tracking states. They could issue different presence IDs and alternate the displayed state/card for one person. Early identity results could also replace a completed torso result with a face-only result.
- The renderer required optical-flow projection onto current preview pixels. A detected forehead slightly outside the image made the old projection reject every resulting box, even when most of the face was visible. A captured camera view had a face top of -8 pixels. Torso and face annotations disappeared together.
- Body localization ran after recognition and was gated by enrollment eligibility. Anonymous tracks therefore could not show a torso while recognition proceeded.
- Recognition repeated face detection at a different input size instead of using the tracked face's exact landmarks. This cost extra inference and supplied separate geometry to the two tracking paths. Previously observed native workloads also exceeded frame freshness limits; the three-second limit has not been extended.
- All status text and the retry button shared a fixed-height, single packed row, which clipped performance text.
- Native verification exposed an additional lifecycle problem: cyclic font cleanup from an old, destroyed Tk root occurred on a database worker, blocking a filter load. A captured thread stack showed `tkinter.font.Font.__del__` in that worker. Cleanup now occurs on the main thread after login; the native test harness does the same between roots. Closing the dashboard also gives cancelled camera/model workers a bounded grace period before Python tears down native runtime types.

## Active flow

1. The camera owner captures immutable pixels, source/frame IDs and timestamps.
2. A tracking/localization worker runs face detection and, once its separate body component is ready, associates bodies one-to-one and validates central chest crops. Hips and legs are not required. Anonymous faces and valid torso boxes can be displayed immediately.
3. The tracking worker submits those exact pixels, landmarks, body/torso boxes and immutable presence tokens to the bounded recognition queue. ArcFace embeds those landmarks without a second face-detection pass.
4. Recognition and uniform inference operate on that original frame. The trained classifier is required; colour-reference results supplement it and cannot substitute for a failed or missing classifier. Enrollment eligibility, identity confirmation, distinct-frame evidence counts, confidence thresholds, freshness and camera/session cancellation remain enforced.
5. The assessment state only matches an observation to a track with the same source presence token and compatible appearance. An ambiguous/change-of-owner track cannot inherit another person's evidence. Detector velocity smoothing prevents small positional jitter from producing large predicted jumps after a brief interruption.
6. The UI associates a final assessment with the same current presence token only after validating motion and geometry. It uses current localized geometry for annotations, with the same assessment driving the card and overlay. Unverified motion withholds identity rather than assigning it to another face. A brief failure recovers on subsequent frames without a model retry.
7. Persistence rechecks freshness and motion before writing, keeps the original capture time/student/crop, and saves pending review records through the existing disciplinary workflow.

The status bar now has separate camera-state and performance rows, with a separate retry column. States include Identifying, Locating torso, Checking uniform, Uniform compliant, Suspected uniform violation, and a specific abstention reason. Torso localization can continue when the classifier is unavailable or disabled.

## Files for this follow-up

- `core/live_pipeline.py`: immutable tracked observations, anonymous localization, same-frame handoff, projection clipping/offsets, classifier-required assessment, and focused localization/evidence/display/write diagnostics.
- `core/live_state.py`: source-presence constraints, smoothed motion, and track-creation diagnostics.
- `core/recognizer.py`: retain detector landmarks and embed supplied detections without redetection.
- `core/model_readiness.py`: separate torso readiness label and bounded loader shutdown wait.
- `ui/dashboard.py`: coordinated scheduling, one presence namespace, current geometry plus verified assessment, readable status layout, and shutdown grace.
- `ui/live_alerts.py`: Locating torso presentation.
- `main.py`: main-thread cleanup of the disposed login root.
- `tests/test_coordinated_monitor.py`: new ownership, continuity, clipped-face, anonymous-localization, recovery and two-person regressions.
- `tests/test_live_monitor_native_ui.py`: status/performance layout assertions.
- `tests/test_suspensions_ui.py`: production-style event pumping and old-root font cleanup in native test setup.
- `scripts/verify_live_torso.py`: bounded real-camera/native-dashboard test against an isolated SQLite copy, including original-frame snapshot verification. Diagnostic image capture is opt-in.

Earlier enrollment, portal, model-readiness and crop changes in this working tree are preserved. No enrollment, trained weights, colour reference or disciplinary rules were replaced.

## Actual verification

The successful 28-second native camera test used the actual face, body and uniform models, real Tk rendering, and the user's seated camera view. Its measurements include model initialization:

| Milestone | First displayed after startup |
| --- | ---: |
| Face track | 3.668 s |
| Valid torso box | 6.679 s |
| Completed uniform result | 9.771 s |

One presence ID remained stable for the entire successful run. The probe observed 248 display samples with a face and 215 with a torso. Median tracking/localization time was 0.394 s, recognition 0.415 s, and uniform inference 0.049 s. No frames were rejected as expired; the two end-of-run rejections were explicit session cancellation.

One `pending_review` record for `2023-00883` was committed in the **isolated test database**. The probe verified the saved JPEG byte hash, student ID and capture timestamp against the original accepted frame (`…efa`, frame 161), rather than the latest preview frame. The probe made **zero writes to the live database**. No Tk callback errors occurred.

An earlier 35-second diagnostic did not reach a verdict because its visible crops were predominantly skin; it correctly abstained. This was not counted as a successful clothing test and did not justify relaxing the skin guard. The subsequent camera run with visible clothing completed the pipeline above.

A previously captured camera view with glasses/headset also passed the new landmark handoff, identity match and torso validation; real inference returned 98.55% wrong-uniform confidence. This recorded-image replay did not supply fresh violation evidence or write records.

Four stored validation crops (two per class) were processed by the actual classifier and reference. Both correct-uniform samples had fused P(correct) above 0.998; both incorrect-clothing samples had P(correct) below 0.001. This small fixture check is not a general accuracy benchmark.

Automated verification passed **318 headless tests and 18 native UI tests**. Coverage includes two people with different clothing and reversed detection ordering; ownership changes at the same coordinates; crossings/overlap and contaminated crops; brief missed detections; clipped foreheads; unavailable classifiers; model retry/readiness; late/cancelled results; camera generations/switching; original-frame persistence; and existing enrollment/disciplinary/portal behavior. Native checks cover monitor startup/navigation/shutdown, status layout, student management, records, suspension filters and portal refresh. The native timeout was resolved after collecting old-root fonts on Tk's thread; the full 18-test group then passed.

Reproduce the real-camera test from a desktop-capable session:

```sh
.venv/bin/python scripts/verify_live_torso.py --seconds 35
```

It opens an explicitly labelled test dashboard, copies the database into a temporary directory, and closes automatically. Lack of a completed visible face/torso/verdict returns a nonzero result. Its parent terminates a stalled native child at a bounded deadline. Add `--capture-debug-frame` only when local diagnostic images are needed.

## Remaining manual checks

- Wear the actual correct uniform, then incorrect clothing; test seated and standing with the face and enough shirt in frame.
- Test small movements with glasses/headset and brief hand/torso occlusion. The track should recover; a cropped-out shirt should remain unassessed.
- Use two real people with different clothing, then cross/overlap. Automated ownership tests passed, but a physical two-person session was not available in this run.
- Switch cameras and leave/reopen Live Monitor. Confirm no old name/verdict transfers and no cancelled session saves a detection.
- In the normal app, verify that a newly committed pending detection appears only in its student's portal, without a strike until confirmation. The isolated live test verified pending persistence, while automated native portal tests cover visibility and ownership.

The skin/geometry heuristics and trained model remain lighting- and framing-dependent. Optical flow intentionally withholds annotations when motion ownership cannot be verified; it must not be replaced with an overlap-only identity fallback.
