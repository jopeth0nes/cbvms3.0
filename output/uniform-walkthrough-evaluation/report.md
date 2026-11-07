**Evaluation handoff — 6 November 2026. Available software checks and timing measurements are complete. Real walking-speed effectiveness remains blocked by missing data.**

Repository `/Users/jopethones/Documents/GitHub/cbvms3.0`, branch `main`, HEAD
`ea997f42d5b75b12e58787f1b2691461256a5589`. Existing edits were preserved; no training,
approval, production activation, attendance/disciplinary writes, commit or push occurred.
All owned jobs have completed. Commands, start/end times and exit codes are in the
adjacent `*-process.json` receipts and the [checkpoint](../../docs/UNIFORM_UPGRADE_CHECKPOINT.md).

The recovered catalog was rechecked: **963 pending, 0 approved, 0 excluded; 36
Organizational Shirt and 927 unassigned legacy examples; 0 reviewed printed-logo boxes**.
All capture provenance remains unknown for training split readiness. No real candidate
exists. The retained three-class candidate is a synthetic plumbing fixture, not a uniform
recognizer validated on people. Logo status remains **Not trained · Disabled**.
The original ZIP is unchanged; its matching archived audit found 116 images / 36 distinct
byte hashes. No examples were automatically relabeled or approved.

| Stage | Normal application capability | Verification reused or completed | Remaining limitation |
|---|---|---|---|
| 1 | Named category galleries; safe imports/dedup; original/torso and label review; provenance and conservative capture-group splits | Matching Stage 5 dataset/reviewer source pins; 45 data/report and 29 native UI checks retained | Zero approved examples; unknown capture groups; independent evaluation sessions still need collection |
| 2 | Shared approved-dataset training action; fixed class IDs/preprocessing; background job, cancellation/recovery and candidate bundle | 17 prerequisite tests recovered; prior actual synthetic cancel/resume smoke and weight preservation retained | No reviewed Campus Uniform/Other Clothing coverage; no meaningful real candidate training |
| 3 | Held-out evaluation and version display; authorized explicit activation/rollback | 35 isolated evaluation/registry/integration tests recovered; genuine model-load smoke retained | No real independent evaluation; baseline binary labels cannot establish Organizational Shirt performance; no activation eligibility |
| 4 | Explicit observation candidate uses existing face/body association, torso quality gates, freshness/cache invalidation and logical passage results; policy separate | Retained 59 pipeline/camera/persistence and 28 observation/logo/latency checks; new 29 scorer/replay tests and actual replay CLI smoke | Physical passage association and before-exit success unmeasured; synthetic candidate cannot establish accuracy |
| 5 | Reviewed printed-logo boxes/visibility/source/torso association; reference artwork separate; disabled configuration | Matching Stage 5 software/native evidence; profiler observed zero logo scheduling calls; no logo executor exists | No trained verifier; no torso-plus-logo accuracy or latency comparison |

Stage 1 source pins were superseded by later stages; its old test result alone is not
treated as current verification. Matching final Stage 5 tests cover those changes.
Detailed pin matches and **1,772 preserved dataset/model files, seven active artifacts,
unchanged catalog/archive and unrelated tracked diffs** are in `preservation-and-state.json`.
The active model pointer remains absent, retaining the existing legacy deployment.

**No physical-camera baseline is available.** The user confirmed no footage yet.
Neither personal videos nor predictions were used as ground truth.

| Source | Saved role | Real labeled passages | Correct association + garment before exit | Entry / usable torso latency | Missed, late, unassessed, wrong-person rates |
|---|---|---:|---|---|---|
| Front Gate | IP source in separate preview selection; not saved primary | 0 | Unavailable | Unavailable, n=0 | Unavailable |
| MacBook Camera | Saved primary `usb_0` | 0 | Unavailable | Unavailable, n=0 | Unavailable |

These limitations apply separately to normal walks, brisk walks, crossing people and
obstructed views. Actual camera resolution, capture FPS, lighting and color/IR mode were
not measured. Saved settings are not evidence of a running source. Preview-only cameras
do not receive simultaneous analysis. No authorized identity gallery was evaluated.

**Recovered inference-only comparison on this Apple M4.** `pipeline-profile-native.json`
finished on 5 November; it was not repeated during this handoff. Four warmup and 20 measured
frames per backend alternated the same two installed public images (810×1080 and 1280×720).
No camera, rendering, live preview workload or continuous passages. Fresh independent
frames deliberately bypass evidence reuse. Both configurations used the same synthetic
candidate on CPU and an empty private identity gallery. Thresholds and input sizes stayed fixed.

| Measured stage | CPU body median / p95, ms | MPS body median / p95, ms | Sample unit |
|---|---:|---:|---|
| Face detection | 38.080 / 39.133 | 37.840 / 38.222 | 20 frames |
| Person detection | 22.905 / 26.183 | 26.220 / 28.122 | 20 frames |
| Face alignment | 0.206 / 0.235 | 0.206 / 0.230 | 40 faces |
| Face embedding | 69.358 / 70.843 | 68.743 / 69.562 | 40 faces |
| Recognition including both faces | 139.229 / 142.222 | 138.040 / 139.322 | 20 frames |
| Association | 0.017 / 0.027 | 0.016 / 0.028 | 40 calls |
| Torso localization | 0.006 / 0.008 | 0.006 / 0.007 | 40 calls |
| Garment preprocessing | 0.133 / 0.146 | 0.163 / 0.202 | 10 usable attempts |
| Garment inference | 2.720 / 2.930 | 2.711 / 2.817 | 10 usable attempts |
| Assessment queue | 0.034 / 0.040 | 0.033 / 0.035 | 20 frames |
| Frame to completed result | **203.732 / 209.006** | **206.126 / 208.535** | 20 frames |

MPS body detection changed whole-frame median by **+2.394 ms (1.175% slower)**; p95
changed by −0.471 ms. The median of individual paired differences is +0.915 ms, a
different statistic from the difference of medians. Raw pairs are in
`paired-still-timing.json`. CPU ran before MPS, with only two unique images; these small
differences may be timing variation. These are descriptive percentiles, not reliable
entrance tail-latency estimates. **No backend change is justified.**

The actual runtime is SCRFD `det_10g.onnx` face detection (320), ArcFace `w600k_r50.onnx`
embedding (112 aligned), both verified `CPUExecutionProvider`; YOLOv8n person detection
(640 default), verified parameters on `cpu` versus `mps:0`; geometric torso localization
with face/body overlap checks; YOLOv8n classification with fixed 224 RGB [0,1] letterbox,
no silent flip. MPS timings synchronize GPU completion. Pose and earrings were off.
Torch threads were 2; OpenCV reported 10 despite requesting 2. No face model was replaced.
Embedding was the largest measured cost, but no unmeasured caching/input-size change was adopted.

Each backend produced 40 face assessment rows, 30 associated torso proposals, ten garment
inference attempts and **zero recognized garments** with this synthetic candidate.
Low-resolution/invalid-association/uncertain cases remained unassessed, not violations;
policy was unset. Matching detection counts do not establish matching correctness.
The previous Stage 4 standalone garment CPU/MPS benchmark remains retained; standalone
inference speed must not be confused with whole-passage completion.

**New work after recovery:** completed the interrupted scorer guards and tested 29 focused
cases; ran a 12-frame generated-public-still CLI smoke, then verified its telemetry after
enabling the existing bounded tracing automatically in the evaluation CLI. Both are
plumbing checks with zero human labels, not walking trials. Final smoke has CPU model
providers, private empty DB, populated telemetry, zero logo calls and null accuracy.
The earlier smoke lacked optional telemetry flags and is retained. The initial profiler
with a missing observation handoff is invalid for whole-path comparisons; the corrected
sandbox run disclosed unavailable MPS. Only the corrected native run above supports the
backend comparison. The launcher’s initial failing test was a `/var` versus `/private/var`
fixture expectation; the corrected seven-test run and both no-camera preflights passed.

The new scorer uses all independently annotated passages, separate wrong-neighbor errors
even for identical garments, timing distributions plus individual values, misses,
unassessed/late results and logical-result duplicates. Never-usable views remain failures
in the denominator. Missing truth or truncated history suppresses rates. A later correct
result can resolve uncertainty; an earlier wrong result is also reported, so success and
error counts may overlap. Geometry matching uses the existing 0.25 IoU convention; it is
an audit matching rule, not a deployment acceptance threshold. Human review remains
necessary for ambiguous overlaps. Replay currently assumes constant-frame-rate media;
variable-frame-rate footage requires timestamp validation before a meaningful comparison.

**What this establishes:** the software can safely prepare/review data, manage candidates,
abstain and measure an isolated replay. It does not establish reliable walking-speed
uniform scanning. For the same real walk-throughs, milliseconds gained/lost and percentage-
point change are **unavailable**, not zero. The 500 ms target is not validated.

**Single next action:** collect one consented, independently annotated Front Gate
walk-through set covering the four scenarios. Use the [handoff](HANDOFF.md) for fields,
the tested isolated preflight and future paired replay commands. A separate reviewed real
candidate is also required for meaningful garment accuracy; current data must first be
reviewed through Training → Uniform Check. Keep the synthetic bundle out of accuracy claims
and keep logo verification disabled.
