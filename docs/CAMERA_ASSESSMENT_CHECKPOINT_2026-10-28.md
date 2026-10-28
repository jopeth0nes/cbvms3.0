# Camera assessment checkpoint — 28 October 2026

## Current state (supersedes earlier interrupted operations)

Branch `main`, HEAD `d7a62a30836aa1d33673cb1ada47d230bf60ef0b`. Task changes are uncommitted; no push/deployment.
Original tree was clean at d7a62a3. Existing trained weights, database/schema,
credentials, unrelated modules and camera/power settings were preserved.

Implemented: verified optional body MPS with one-time CPU fallback; bounded
same-frame candidate selection; priority/aging/overload handling; worker idle and
completion dispatch; face-only recognition; all-person torso sanitation; passage
validity and gallery/session invalidation; separate monotonic timing; global
observation-only write/audio guards; local annotation and real-model paced replay.
No identity/embedding cache: fresh recognition plus per-passage refresh/dedup.
Default production path/CPU preserved. All new flags default off; candidate logic
is usable only with CBVMS_ASSESSMENT_OBSERVATION_ONLY=1. No new automatic discipline
behavior enabled. Face ONNX and uniform classifier remain CPU; optional body
mps:0 verified on public images, not accepted as a speed improvement.

## Last confirmed verification

147 focused tests passed in 9.942s:
```
.venv/bin/python -m unittest tests.test_assessment_candidates tests.test_person_detector_backend tests.test_camera_replay tests.test_live_pipeline tests.test_coordinated_monitor tests.test_live_state tests.test_camera_switching tests.test_recognizer_identity tests.test_student_archive -q
```
4 native live-monitor UI tests previously passed. 18 simulated camera-switch cases
are in the focused suite. Separate tests.test_live_persistence: 8 pass, 1 unchanged
closed-database fixture error in test_guard_is_checked_inside_the_write_transaction;
this is the known prior-baseline issue, not silently counted as passing.
The final lead corrections cover expired queue tasks retaining active_task,
overload evicting new entrants, and already-assessed sibling passages being treated
as new. Focused tests include these. Ten source modules syntax parsed; diff check
clean. Previous public fixture/replay evidence is retained, with revision limits
explicit in the final report.

## Physical checks completed, no running task operations

Runner /private/tmp/cbvms-assessment-final-runner.py completed both children,
exec session8092 exited0. There are no running task camera/model benchmarks.
The unrelated recovery-mail relay was identified and left untouched.
Both final physical commands used env values:
CBVMS_ASSESSMENT_OBSERVATION_ONLY=1 CBVMS_CLEAR_FRAME_SELECTION=1
CBVMS_PRIORITIZED_ASSESSMENT=1 CBVMS_BODY_YOLO_BACKEND=cpu.
Commands, sequentially:
```
.venv/bin/python scripts/benchmark_camera_runtime.py --mode full --source front-gate --seconds 60 --warmup 15 --busy --output /private/tmp/cbvms-assessment-verified-front-gate.json
.venv/bin/python scripts/benchmark_camera_runtime.py --mode full --source usb --seconds 60 --warmup 15 --output /private/tmp/cbvms-assessment-verified-usb.json
```
Both exit0, no harness errors, required models ready, all camera/tracking/assessment
workers stopped; no frames/business writes. Battery and Low Power Mode ON, unchanged.
Front Gate capture19.950/fresh19.833FPS, no usable passage, 660 simulated sync ops.
MacBook capture29.800/fresh18.133FPS, tracking2.650/s, assessments0.900/s;
first-usable-to-assessment1135.772ms for ONE session passage (not populationp95).
500ms target and MacBook30freshFPS not met. Candidate memory peak5.27MiB.
Earlier Front Gate open failure/recovery artifact preserved. No unmatched AC vs
battery or public/synthetic vs physical speed claim. Empty roster means no uniform
accuracy/enrolled throughput/correct-confirmation evidence.

Evidence: docs/validation/camera_assessment_2026-10-28.json and its companion
camera_assessment_2026-10-28/ directory contain numeric results, full run source
hashes, failed-start and final artifacts. Read docs/CAMERA_ASSESSMENT_REPORT_2026-10-28.md
for outcomes/limitations and docs/CAMERA_ASSESSMENT_EVALUATION.md for replay/rollback.

## Modified/untracked task files

```
M core/live_pipeline.py
 M core/performance.py
 M core/person_detector.py
 M core/recognizer.py
 M scripts/benchmark_camera_runtime.py
 M tests/test_coordinated_monitor.py
 M tests/test_live_pipeline.py
 M ui/dashboard.py
?? core/assessment_candidates.py
?? docs/CAMERA_ASSESSMENT_CHECKPOINT_2026-10-28.md
?? docs/CAMERA_ASSESSMENT_EVALUATION.md
?? docs/CAMERA_ASSESSMENT_REPORT_2026-10-28.md
?? docs/validation/camera_assessment_2026-10-28.json
?? docs/validation/camera_assessment_2026-10-28/
?? scripts/annotate_camera_replay.py
?? scripts/benchmark_yolo_backends.py
?? scripts/replay_camera_assessment.py
?? tests/test_assessment_candidates.py
?? tests/test_camera_replay.py
?? tests/test_person_detector_backend.py
```

## Remaining limitations / exact next action

Implementation and available verification complete. No pending installation,
migration, commit, benchmark, or uncertain write. Next optional validation action:
obtain authorized labeled local footage/evaluation-gallery copy and run the guide's
baseline/candidate replay sequentially under matched AC/Low-Power-off conditions.
This is not required to keep the existing production route running. Keep candidate
behavior observation-only until validated. Accuracy, crowded labeled passages,
physical long sustained MPS, deliberate sleep/wake and real cloud-sync latency
remain unverified. Do not describe the experimental pipeline as a proven speedup.
Rollback: unset observation/selection/priority flags, retain CPU, restart; reverse
only feature-specific code hunks if desired, preserving later unrelated edits.
