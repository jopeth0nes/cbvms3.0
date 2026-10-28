# Face and torso assessment rollout and replay

This change keeps the current live assessment route as the default. The optional
candidate path is limited to a session-wide observation-only mode: attendance,
discipline, notifications, and persistence writers stay disabled for the whole
session, regardless of task flags or direct worker calls. Candidate logic never
changes a production attendance or discipline decision.

## Feature controls

Export these values in the launch shell before starting CBVMS; values are read at
import/startup and require a process restart.

```sh
export CBVMS_BODY_YOLO_BACKEND=cpu                 # default; `mps` requests verified MPS
export CBVMS_ASSESSMENT_OBSERVATION_ONLY=1          # required for the candidate route
export CBVMS_CLEAR_FRAME_SELECTION=1                # independent optional selection flag
export CBVMS_PRIORITIZED_ASSESSMENT=1                # independent optional scheduler flag
# Start CBVMS using the normal project launch command in this shell.
```

Clear-frame selection and prioritization are independent. Either flag creates a
bounded candidate router, but it is used only while observation-only is enabled.
With all candidate flags off, dashboard dispatch and the CPU YOLO default retain
the existing behavior. Requested and actual YOLO parameter placement are reported
separately. An MPS inference failure drops that frame, attempts CPU placement once,
then fails closed if placement cannot be verified.

Rollback is scoped to process configuration: unset
`CBVMS_ASSESSMENT_OBSERVATION_ONLY`, `CBVMS_CLEAR_FRAME_SELECTION`, and
`CBVMS_PRIORITIZED_ASSESSMENT`, set or leave `CBVMS_BODY_YOLO_BACKEND=cpu`, and
restart CBVMS. For code rollback, revert only the feature-specific hunks in the implementation
files listed in the final report, preserving unrelated edits made since this change.
Unset the three observation/candidate flags and restart to restore the original
operational route immediately.
No model weights, dependencies, gallery entries, thresholds, or production DB
schema changed.

## Local annotation and model-backed replay

Use locally authorized video only. The annotation utility opens original frames,
stores local passage aliases and face/body/torso boxes as JSONL, and does not save
or upload images. Use `--every 1` for frame-by-frame passage-entry labels. Identity
labels must be pseudonymous aliases. Uniform labels are `correct_uniform`,
`wrong_uniform`, or `unable`; body and torso boxes may be omitted when not visible.

```sh
.venv/bin/python scripts/annotate_camera_replay.py \
  --video /local/authorized.mp4 --every 1 --output /local/labels.jsonl
```

The replay decodes every video frame through `FaceTrackingProcessor`; model warmup
is completed before task timestamps are created and is reported separately.
Baseline assessments follow a 500 ms monotonic capture-time offer interval with
latest-frame worker backpressure. Candidate mode uses the bounded router and its
per-passage refresh interval; it dispatches on worker idle and does not inherit the
legacy global offer throttle. Playback is paced at the source video FPS, and both
modes use the same actual LiveWorker/LiveProcessor path. A selected prior frame is joined to labels by its own frame ID, never by
the current decode index. Video-timeline passage latency is separate from actual
compute wall latency. An annotation row should be present for each evaluated frame
so entry/miss metrics have adequate coverage.

```sh
.venv/bin/python scripts/replay_camera_assessment.py \
  --video /local/authorized.mp4 --annotations /local/labels.jsonl \
  --mode baseline --output /local/baseline.json
.venv/bin/python scripts/replay_camera_assessment.py \
  --video /local/authorized.mp4 --annotations /local/labels.jsonl \
  --mode candidate --clear-frame-selection --prioritized-assessment \
  --output /local/candidate.json
```

Without an evaluation gallery and alias map, identity accuracy is `null` with an
explicit reason. For authorized identity evaluation, provide a disposable SQLite
copy and `--identity-alias-map /local/aliases.json`, shaped as
`{"local_alias":"student_id"}`. Replay uses SQLite backup into a private temporary
database, loads the real local CBVMS models and actual `LiveProcessor`, and removes
the temporary database at exit. It never opens the source evaluation DB for writes
and never emits student IDs or per-passage records. Do not pass a live production
database; prepare an authorized evaluation copy first. Uniform metrics remain
unverified without suitable labels and a trained local classifier.

## Evidence and limits

Sanitized numeric evidence for the 28 October 2026 implementation is in
[`docs/validation/camera_assessment_2026-10-28.json`](validation/camera_assessment_2026-10-28.json).
It separates synthetic/public-fixture plumbing, physical acquisition, physical
CPU inference, actual backend placement, and model-backed replay. No controlled
physical before/after performance claim is made. The paced replay used public Zidane footage with no passage labels and an empty
temporary roster; baseline completed 5 worker assessments (10 person rows) and
candidate completed 8 (16 rows). Different offer counts and warmup/cache states
mean these numbers prove model-backed plumbing only, not a controlled performance
comparison. Accuracy and uniform outcomes remain
unverified. The trained uniform classifier has no independently benchmarked
accelerator placement in this change.

## Final physical evidence and recommendation

The final report supersedes earlier pending-hardware notes. Both selected sources
were tested separately for60s after15s warmup with candidate flags enabled, CPU,
empty temporary rosters and battery Low Power Mode ON. Front Gate delivered
19.95capture/19.83freshFPS with simulated sync; MacBook29.80capture/18.13freshFPS.
These are not matched before/after or accuracy comparisons. No identity embedding
cache was enabled; passage refresh/dedup keeps fresh recognition. See the final
report for the unmet latency/preview targets and the pre-existing persistence
fixture error. Keep CPU and the original operational path for both cameras.
