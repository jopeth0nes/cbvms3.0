# Camera assessment implementation report — 28 October 2026

Implemented an opt-in, observation-only assessment pipeline. Normal production
monitoring remains the default. Accuracy improvement and a full-monitor speedup
have **not** been established; do not enable new automatic discipline behavior.

## Implementation and configuration

- Original immutable camera frames retain source, camera/monitor generation,
  passage and frame IDs, capture/occurrence timestamps, landmarks and source-pixel
  face/body/torso geometry. No screenshots, uploads or continuous video buffer.
- Same-frame candidates use size, normalized sharpness, exposure, visibility and
  ownership checks. Face-only recognition remains possible. Invalid torso geometry
  is removed for every assessed person, including non-target people.
- Bounds: 48 tracked passages, two candidates per passage, 24 candidates total,
  24 MiB of retained image arrays, queue limit 48, capture-age expiry three seconds.
  These limits are configurable through `ClearFrameRouter` construction.
- New entrants, useful improvements and near-exit observations get priority;
  waiting work ages, and overload evicts lower-priority redundant confirmations.
  Worker-idle dispatch and completion/expiry acknowledgments keep work bounded.
  All people actually assessed in one frame receive the refresh interval; tracking
  predictions and repeated frames never count as fresh recognition evidence.
- No embedding or identity cache is introduced. A passage-scoped 500 ms refresh
  interval and immutable-frame deduplication reduce redundant submissions while
  retaining fresh recognition and enrollment checks. New passages bypass the old
  global 500 ms offer guard. Confirmation/cooldown rules remain unchanged.
- Passage-specific validity tokens reject disappeared/ambiguous/reused passages,
  invalid torso evidence, gallery reloads, camera/session changes and expired work.
  Existing transactional eligibility/cancellation and duplicate-write protections
  remain in the normal operational path.
- Observation sessions omit persistence writers and block direct attendance,
  security, discipline and audio calls. Candidate inference replaces production
  assessment for that session. Tk calls remain on the UI thread; selection and
  inference run in background workers.

All new flags default off. To evaluate locally, restart with:

```sh
CBVMS_BODY_YOLO_BACKEND=cpu \
CBVMS_ASSESSMENT_OBSERVATION_ONLY=1 \
CBVMS_CLEAR_FRAME_SELECTION=1 \
CBVMS_PRIORITIZED_ASSESSMENT=1 \
.venv/bin/python main.py
```

Selection and prioritization can be enabled separately, but both require
observation-only for dashboard use. Enable `CBVMS_PERF=1` for bounded numerical
latency diagnostics. [Evaluation instructions](CAMERA_ASSESSMENT_EVALUATION.md)
cover local annotations, authorized evaluation-gallery copies, paced baseline and
candidate replay, configuration and rollback. Replay uses actual models/workers;
labels follow the selected frame, and completion timing is separate from capture
and compute time. Behind-schedule frames are skipped and timing marked unverified.
Missing labels/gallery produce null accuracy, not zero-error claims.

## Model placement

| Model | Verified placement and policy |
| --- | --- |
| YOLOv8n body | CPU in final physical runs. Optional `mps:0` verified on public bus/Zidane images. CPU remains default. |
| Trained uniform classifier | Unchanged CPU placement from the retained model-metadata probe; no MPS classifier claim or change. |
| Face detection and embedding ONNX | Unchanged `CPUExecutionProvider`; Core ML was not enabled and gallery model was not replaced. |

MPS prediction is synchronized for timing and actual parameters are checked.
An inference failure drops that frame, attempts CPU transfer once, and fails closed
if placement cannot be verified. Public-image CPU/MPS checks yielded matching
person counts [4, 2] for three repeats: CPU p50/p95 25.610/30.412 ms, MPS
26.032/31.180 ms. Six samples do not establish a speed advantage or sustained MPS
performance. Weights, preprocessing, mappings, thresholds and dependencies remain
unchanged. [Prior placement metadata](validation/camera_assessment_2026-10-28/prior-model-placement.json)
is retained separately from current backend checks.

## Actual performance evidence

Final-code physical checks were sequential, 60 seconds each after 15 seconds of
warmup, battery power with Low Power Mode enabled. No power or shared camera
settings were changed. Both used an empty temporary roster, CPU backend and all
observation/candidate flags enabled. Front Gate additionally ran the real sync
worker against disposable data and simulated transport, with 660 accepted
operations in 132 batches. No frames or business records were saved.

| Source | Capture FPS | Fresh preview FPS | Tracking results/s | Assessment results/s |
| --- | ---: | ---: | ---: | ---: |
| MacBook, AVFoundation 1280×720/30 | 29.800 | 18.133 | 2.650 | 0.900 |
| Front Gate, FFMPEG HEVC 1920×1080/20 | 19.950 | 19.833 | 7.450 | 0.000 |

Front Gate had no usable tracked passage; its result establishes preview/capture
and sync plumbing only. MacBook candidate arrays peaked at 5,529,600 bytes (5.27
MiB), sampled worker queues at [1, 0], and RSS ranged 679–813 MiB. Gate candidate
arrays stayed empty and RSS ranged 832–874 MiB. All required models were ready;
the optional earring model was unavailable. No harness errors occurred in either
final run, and camera/tracking/assessment workers stopped cleanly.

MacBook capture-to-assessment p50/p95 was 1911.779/2696.726 ms. Its single session
first-usable-to-first-assessment observation was 1135.772 ms (including session
warmup tracking). This is **not a population p95**, not correct-confirmation
latency, and does not meet the 500 ms engineering target. No enrolled uniform
throughput or correct-confirmation latency could be measured. The MacBook fresh
preview target of 30 FPS was not achieved. These are 60-second checks, not a new
10-minute stability certification.

An earlier Front Gate attempt failed to open initially, then recovered to
19.433 capture/19.133 fresh FPS in its final 30 seconds; that failed-start artifact
is preserved, not silently replaced. Earlier AC measurements and historical
camera reports have different conditions and are not controlled A/B comparisons.
The eight-frame public Zidane replay exercised actual assessment in both modes
without writes. Different offer counts, empty roster and absent labels mean it
provides plumbing evidence only. Its numerical results predate the final overload
policy correction; that correction has separate regression coverage.

Full numeric artifacts and source hashes are under
[validation/camera_assessment_2026-10-28](validation/camera_assessment_2026-10-28/).
The [summary JSON](validation/camera_assessment_2026-10-28.json) separates historical,
public-fixture, replay, failed-start and final physical evidence.

## Correctness verification and limitations

Final focused command:

```sh
.venv/bin/python -m unittest tests.test_assessment_candidates tests.test_person_detector_backend tests.test_camera_replay tests.test_live_pipeline tests.test_coordinated_monitor tests.test_live_state tests.test_camera_switching tests.test_recognizer_identity tests.test_student_archive -q
```

**147 tests passed** in 9.942 seconds. Coverage includes queue overload, completed
frame refresh, active-ticket bounds, expiry recovery, original-frame ownership,
all-person torso sanitation, successful-result validity, disappearance, gallery
revision/removal, generation changes, observation write gates, MPS fallback,
replay false-positive accounting and existing recognition/archive behavior.
Four native live-monitor UI tests passed earlier; the 18 simulated switching cases
are included in the focused suite. Both actual sources were exercised separately
on final code; this is not a claim of a new manual switching or sleep/wake test.
Ten affected source modules parse, and `git diff --check` passes.

The separate nine-test live-persistence suite has eight passes and one unchanged
pre-existing fixture error: `test_guard_is_checked_inside_the_write_transaction`
reuses a closed connection when an ineligible synthetic student takes the
existing enrollment-review path. Database code and that test are untouched; the
same error is documented in the prior camera report. No unrelated fix was made.

No authorized labeled student footage/gallery was supplied. Identity/uniform
accuracy, passage misses, crowded-scene performance and correct-confirmation
latency remain unverified. Real network sync/cloud contention, deliberate
sleep/wake, long sustained candidate runs and sustained physical MPS were not
validated. The tests are not grounds for activating automatic violations.

## Recommended settings and rollback

For both sources, retain CPU and the existing production assessment route. Keep
the user's existing preview setting (the tests requested 30); Front Gate's actual
source remains 20 FPS and the MacBook source 30. Do not raise inference frequency
or change stream settings based on these unmatched, unlabeled results. Evaluate
candidate behavior only in observation mode with authorized labeled footage.

To return to ordinary operation, unset the three observation/selection/scheduling
variables, set `CBVMS_BODY_YOLO_BACKEND=cpu`, and restart. For code rollback, review
and reverse only this feature's hunks; do not restore entire files over newer
unrelated changes. No migrations or data/weight rollback are needed. Work remains
uncommitted on `main` above `d7a62a3`; no push or deployment occurred.
