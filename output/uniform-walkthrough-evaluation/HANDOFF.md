Run all commands from `/Users/jopethones/Documents/GitHub/cbvms3.0`.
No model activation or production storage is needed for these checks.

**Tested isolated preflight:**

```sh
.venv/bin/python scripts/launch_uniform_observation.py \
  --config output/uniform-walkthrough-evaluation/observation-synthetic-cpu.json \
  --primary 'front gate' --body-backend cpu --check
```

This exact preflight passed. Replacing the source with `'MacBook Camera'` also passed.
It imports the normal application, validates the pinned synthetic bundle, initializes an
empty private database, confirms observation mode/cloud isolation and selects only that
primary source in private preferences. It opens neither UI nor camera.
Remove `--check` for the manual native observation launch. That physical-camera/UI launch
was **not performed**. It retains normal sign-in and existing camera ownership; use only
the temporary evaluation workspace. Close the application when done; private app storage
is discarded. The checked config is explicitly synthetic, policy unset, logo disabled,
and has no calibrated assessment zone. It can demonstrate plumbing, not garment accuracy.
Do not launch ordinary repository `main.py` for this evaluation: observation inference
gates alone do not isolate startup database/cloud actions.

**Collect the missing evidence:**

1. Obtain consent and record the actual Front Gate stream at original resolution/cadence.
   Save camera ID, capture FPS/timestamps, color/IR mode, lighting, zone boundaries and
   model/configuration versions. Run it as the analyzed primary source in the isolated
   workspace; a separate preview window alone does not analyze it. Keep MacBook trials
   in separate files. Verify constant frame cadence before using the current replay.
2. Include normal and brisk walks, two people crossing (including the same garment),
   and partial obstruction. Include all three garment categories and never-usable views.
   Keep whole passages and known sessions together; reserve evaluation sessions from
   training and tuning. Do not claim independence from filenames or model tracks.
3. Independently annotate every visible person's face/body/torso in each frame needed for
   matching, with one anonymous `passage_alias` per actual entry/exit. Supply true garment,
   entry/exit milliseconds and first usable face/torso times. For a region never usable,
   set the explicit status to `never_usable` and leave its timestamp null. Use `unknown`
   when review is incomplete; it prevents an accuracy claim. Check labels without seeing
   the model's proposed identities. Use no raw student names or IDs in the JSONL.
4. Record policy truth only when department/day/schedule rules and actual permissions
   are known. Otherwise leave `policy_label` null. Identity scoring requires a separately
   authorized disposable gallery and alias map; it is independent of face/torso association.

Each JSONL line has `frame_index` and a `people` list. Coordinates are original-raster
`[x1,y1,x2,y2]`; times are milliseconds on the video timeline. A **schema example only**
(invented values, not evaluation labels):

```json
{"frame_index":0,"people":[{"passage_alias":"example-only","garment_label":"organizational_shirt","policy_label":null,"zone_entry_ms":0,"zone_exit_ms":2000,"first_usable_face_ms":100,"first_usable_torso_ms":200,"usable_face_status":"visible","usable_torso_status":"visible","face_box":[100,20,150,80],"body_box":[70,0,200,400],"torso_box":[80,90,190,260],"scenario":"normal_walk","camera":"Front Gate","camera_mode":"color"}]}
```

Repeat stable passage metadata on its rows; update geometry per frame. For empty frames,
use `people: []`. Use separate short clips per camera/scenario, preserving complete
passages. Bounds are 32 MiB JSONL, 10,000 annotation frames, 48 people/frame and 10,000
retained scoring rows shared across passages. Truncation invalidates rates; split clips
at passage boundaries, never silently exclude missed people. Validate annotations across
the full clip before interpreting any result; sparse annotations can miss model outputs.

**Future real replay, after data and a real candidate are available:**

Copy the observation config to a new file, replace only its bundle path/hash with the
reviewed candidate's verified descriptor, and record the zone/configuration. Keep class
mapping/preprocessing fixed, `logo_verification.enabled` false and policy null unless
explicitly established. Do not replace the retained smoke config or activate the bundle.
Paths below are placeholders because those inputs do not exist:

```sh
.venv/bin/python scripts/replay_camera_assessment.py \
  --video /absolute/path/front-gate-normal-walks.mp4 \
  --annotations /absolute/path/front-gate-normal-walks.jsonl \
  --mode candidate --uniform-observation-config /absolute/path/reviewed-candidate-observation.json \
  --body-backend cpu --camera-label 'Front Gate' --camera-mode unknown \
  --output output/uniform-walkthrough-evaluation/frontgate-cpu-new.json
```

After the CPU run completes, use the identical files/settings and change only
`--body-backend mps` and the new output filename. Check actual backend/fallback fields.
Do not run both simultaneously. No `--max-frames` cutoff for the final comparison.
The same candidate/scheduler must be used in both runs; CLI `--mode baseline` selects
the legacy path and is not an interchangeable three-class baseline. The legacy model
has no Organizational Shirt class. Do not tune thresholds or input size against final tests.

The command path was exercised with generated `generated-still-smoke.avi/.jsonl`, the
synthetic config, CPU, 12 frames and no human labels. Final exact command is in
`replay-telemetry-smoke-process.json`. It verifies model loading, routing, temporary
storage, bounded telemetry and null-metric behavior. It does not validate the placeholder
real inputs above. The script refuses an existing output file.

Use `walkthrough_metrics`, not the legacy binary-uniform summary, for the main outcome.
It reports correctly associated **and** correctly classified before exit divided by all
fully annotated passages, including misses and never-usable views. Also review wrong-person
associations, errors preceding later corrections, unassessed, timed-out, late and duplicate
results. Timings include raw values, contributing counts, median and p95 for entry and
first usable torso to first result, plus first correct associated recognition. First-result
timings include uncertainty/late results; correct-recognition timings count only timely
successes. Never read those survivor timings without the all-passage failure counts.
Small-sample p95 is descriptive; 500 ms remains an engineering target, not an acceptance rule.

To diagnose a miss: inspect the human usable-view status first, then `first_face_ms`,
`first_body_ms`/`first_torso_ms`, per-frame detected boxes and association state, then
per-result reason/classification. A never-usable view is a capture/visibility limitation;
a labeled usable face with no detection is detection failure; neighbor overlap is an
association error; valid association plus an uncertainty/quality reason is unassessed.
Use queue/frame-age traces and completion versus exit to distinguish late processing.
Nested trace spans overlap; do not sum them as independent wall time. Unlabeled or
geometrically ambiguous cases require human review rather than a guessed cause.

For candidate preparation, open Training → Uniform Check and review originals, torso
crops, stable categories and capture groups across Campus Uniform, Organizational Shirt
and Other Clothing. Preserve ambiguous legacy labels as pending. Approve only human-
verified examples. The current task performs no approvals or training.

Rollback, if requested later: review `evaluation.patch` against `before/`, stop any app
using these files, and reverse only evaluation-owned hunks/new scripts after checking
for later edits. Keep data, models, prior stage fixes, checkpoint and evidence. Never
reset/clean the checkout or copy whole before-files over newer work.
