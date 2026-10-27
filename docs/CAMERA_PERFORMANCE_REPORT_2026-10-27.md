# CBVMS camera performance — MacBook measurements, 27–28 October 2026

**Measured rendering improvements implemented; 60 FPS is not supported by the tested configuration.** Starting revision
`05ccaca`, clean `main`. No production records, saved source settings, trained
weights, recognition thresholds or confirmation intervals changed.

## Confirmed hardware and source ceiling

MacBook Air `Mac16,12`, Apple M4, 10 CPU cores, 10 GPU cores, 16 GiB RAM,
macOS 26.5 build 25F71. Native arm64; `sysctl.proc_translated=0`. Initial AC policy:
Low Power Mode off. Later checks found battery power with Low Power Mode enabled;
power settings were not changed by this work. Do not treat physical runs across
these conditions as a controlled A/B comparison. Initial memory-pressure probe:
60% free; macOS reported no
recorded thermal/performance warning. This is not a continuous temperature or
GPU-utilization trace; privileged power counters were not collected.

Read-only AVFoundation enumeration reports **15–30 FPS for every exposed format**:
640×480, 1280×720, 1760×1328, 1328×1760, 1552×1552, 1920×1080 and 1080×1920.
The built-in camera cannot supply genuine 60 FPS through the installed backend.
Requesting 60 FPS or redrawing a 30 FPS frame twice cannot remove this ceiling.

## Pipeline and measurement boundaries

Capture owns native open/read/release on its worker. Network cameras decode in a
separate FFmpeg process and use a bounded latest-pixel queue. USB frames have a
session token, acquisition sequence and post-read monotonic time. Network time is
post-decode, before pipe delivery. OpenCV exposes no sensor sequence or sensor
timestamp here; frames are not deduplicated by pixel hashes.

The dashboard defaults to 30 FPS and skips repeated source frame IDs. Tracking
offers are separated by at least 120 ms; assessments by at least 500 ms. Each
worker has at most one queued request; expired or superseded work is rejected.
Models are prepared once and serialized off Tk. UI projection uses optical flow
and generation/source/presence guards; predictions never become new evidence.
Evidence/SQLite work is worker-owned. Settings, session ownership, body overlap,
identity thresholds and temporal evidence policy remain unchanged.

Preview measurements mean **unique image submissions to Tk**, not confirmation
that the compositor displayed each image. Capture-to-preview timings end after
image upload; sensor exposure, driver buffering and compositor scanout are not
measured. Inference throughput and time to a confirmed correct decision differ.

Stability criterion for a target: at least 600 seconds after warmup; average
throughput ≥95% of target, first-percentile 10-second rate ≥90% of target,
p99 frame gap ≤2 target periods, no queue growth beyond one, no sustained RSS
increase above 64 MiB between initial/final 30-second windows, no >10% final
throughput decline, and clean worker shutdown. Report failures rather than
equating an average/peak with this criterion.

## Initial diagnosis

Corrected 30-second physical baseline at 1280×720/30: capture **29.767 FPS**,
fresh preview **19.2 FPS**, tracking **4.167/s**, assessment **1.433/s**.
Feed callback p50/p95/p99: 7.629/13.492/17.890 ms; render:
3.971/5.919/7.513 ms. Thus the observed ~20 FPS preview was reproduced while
the source delivered ~30 FPS; there is no explicit 20 FPS cap in this path.

A native stack sample of the first, discarded nested-update harness showed Tk
idle drawing and CoreGraphics image/color conversion. The corrected harness uses
normal `mainloop`/`after` scheduling. Callback timing excludes compositor work;
the evidence points to display/event-loop overhead, not a 20 FPS source limit.
It does not quantify every macOS compositor cost individually.

## Installed models and device probe

Python 3.12.13, OpenCV 4.10.0.84, PyTorch 2.2.2, Ultralytics 8.3.27,
ONNX Runtime 1.19.2, NumPy 1.26.4. MPS built and available; CUDA unavailable.
Default YOLO execution was verified on CPU after prediction. Face sessions use
CPUExecutionProvider. CoreML provider registration alone does not prove GPU/ANE
placement. No dependency upgrades or provider changes were made.

50 steady blank-input calls, accelerator synchronization included:

| Module/backend | p50 ms | p95 ms |
|---|---:|---:|
| Body YOLO default CPU | 29.788 | 46.029 |
| Body YOLO explicit MPS | 12.869 | 22.185 |
| Uniform default CPU | 5.355 | 7.129 |
| Uniform explicit MPS | 4.290 | 4.928 |
| Face detector CPU | 70.315 | 70.577 |
| Face embedding CPU | 127.204 | 127.891 |

Body weights: `yolov8n.pt`; trained classifier: `uniform_cls.pt` (correct/wrong
uniform); face assets: `det_10g.onnx`, `w600k_r50.onnx`. Optional earring weights
are absent. Installed face detector router class is `RetinaFace`, despite older
comments calling the pipeline SCRFD. Live face input is 320×320; embeddings use
112×112. Actual body input tensor measured `[1,3,384,640]`, 3,151,904 parameters,
`DetectionModel`; classifier `[1,3,224,224]`, 1,437,442 parameters,
`ClassificationModel`/`yolov8n-cls.yaml`. Both use float32 parameters and inputs,
`half=False`, CPU. Both ONNX graph inputs are float32; detection has dynamic
spatial dimensions and ArcFace declares 112×112. OpenCV enables AVFoundation and
FFmpeg (avcodec/avformat 60.3.100), not GStreamer.

`PYTORCH_ENABLE_MPS_FALLBACK` was unset. Blank-input MPS prediction completed with
parameters actually on MPS, but empty detections do not exercise every NMS or
crowded-scene operation. No per-operation accelerator/CPU placement trace was
collected; absence of a fallback message is not proof that every stage ran on GPU.

CoreML detector failed. CoreML embeddings were faster but differed from CPU
(blank-input cosine 0.9976904745). This is not an accuracy equivalence test.
MPS body p95 already exceeds the 16.7 ms budget for 60 FPS in this short isolated
probe. It is a reversible future candidate, not evidence of stable 60 FPS or a
reason to change the production device policy without labeled evaluation.

## Accuracy limits

The user confirmed no labeled footage is available. Physical runs use an empty
temporary roster and suppress business writers; unknown-face localization and
embedding work can run, but these do not verify enrolled identity matching or
uniform confirmation. Regression fixtures exercise those paths separately.
Missed students, false violations, identity swaps, abstention rate and time to a
**correct** confirmed decision cannot be estimated from this data. No accuracy
improvement is claimed. More correlated frames or higher confidence scores do
not establish better accuracy.

## Changes and controlled comparison

- Reuse the Tk PhotoImage for equal-sized frames, updating its pixels; recreate
  it on size changes and reposition it when letterboxing changes. Placeholder,
  resize, reconnect and cleanup semantics are covered by pixel-level native tests.
- Avoid configuring the attendance-writer label when its text has not changed.
- Schedule preview against a monotonic deadline. Late callbacks no longer move
  every subsequent deadline; missed slots are skipped, never replayed in a burst.
- Add p99 to existing opt-in bounded timing summaries. Normal disabled metrics
  retain their no-recording behavior.

No FPS constant, inference interval, model, provider or decision threshold changed.

Identical deterministic 720p/30 synthetic source, native Tk, 30-second samples:

| Comparison | Baseline | Candidate |
|---|---:|---:|
| Preview-only fresh FPS (buffer reuse) | 26.067 | 26.733 |
| Preview-only render p50 ms | 5.259 | 4.066 |
| Full dashboard fresh FPS (all changes) | 27.667 | 29.033 |
| Full dashboard render p50 ms | 2.385 | 1.710 |
| Full dashboard callback p50 ms | 3.002 | 2.157 |

The two harness modes have different canvas sizes and must not be compared to
each other. Each row compares the same mode and source. These short synthetic
tests isolate display cost; they do not prove sustained camera performance or
accuracy. Baseline render/feed callback is loaded from `05ccaca` by the harness.

## Sustained physical evidence

MacBook candidate, 600 seconds after 15 seconds warmup, 1280×720/30 acquisition,
702×360 canvas, CPU models, empty roster, battery/Low Power Mode:

| Quantity | Measured result |
|---|---:|
| Capture | 29.935 FPS |
| Unique preview submissions | 18.342 FPS |
| Preview 10-second rate p1 | 17.000 FPS |
| Preview gap p50 / p95 / p99 | 54.217 / 69.085 / 89.719 ms |
| Tracking completion | 3.030/s |
| Assessment completion | 1.355/s |
| Capture-to-preview p50 / p95 / p99 | 24.941 / 41.212 / 48.760 ms |
| Capture-to-assessment p50 / p95 / p99 | 761.478 / 919.461 / 1006.079 ms |
| Body inference p50 / p95 / p99 | 188.872 / 292.794 / 347.218 ms |
| Face inference p50 / p95 / p99 | 167.345 / 225.594 / 253.485 ms |
| Embedding p50 / p95 / p99 | 353.716 / 428.693 / 491.688 ms |

All three workers stopped, no reported errors, queues stayed bounded. Sampled RSS
fell from 914.14 to 633.11 MiB. First/final 30-second preview rates were
19.400/19.267 FPS. Thermal pressure remained nominal in sampled observations.
This **fails the defined stable 20/30/60 FPS preview criteria** despite stable
camera acquisition and no growing backlog. Capture-to-assessment is not time to
a correct confirmed disciplinary decision.

The earlier 600-second physical baseline measured 29.927 capture / 20.228 preview
FPS, with p1 preview 16.418 FPS. Its scene and power conditions were not held
constant against the candidate; it must not be used to claim a physical speedup
or regression. Controlled synthetic results above are the paired change evidence.

Front Gate, 600 seconds after 15 seconds warmup, 1920×1080 HEVC/FFmpeg,
reported 20 FPS, same 702×360 canvas, battery/Low Power Mode, simulated sync busy:

| Quantity | Measured result |
|---|---:|
| Capture / fresh preview | 19.947 / 19.893 FPS |
| Preview 10-second rate p1 | 19.659 FPS |
| Preview gap p50 / p95 / p99 | 52.179 / 67.182 / 81.559 ms |
| Capture-to-preview p50 / p95 / p99 | 26.727 / 51.410 / 57.303 ms |
| Tracking / assessment completions | 7.430 / 1.848 per second |
| Capture-to-assessment p50 / p95 / p99 | 103.842 / 127.229 / 130.862 ms |
| Isolated SQLite writer wait p50 / p95 / p99 | 0.279 / 0.468 / 0.558 ms |
| Acquired frames not submitted as fresh previews | 32 of 11,968 |

This run meets the defined **20 FPS preview** criterion. Queues remained bounded,
RSS declined, thermal pressure stayed nominal and all camera/inference workers
stopped. The isolated sync worker accepted 5,395 synthetic operations across
1,079 batches including warmup. Transport was simulated; these are not production
network throughput measurements. This scene did not exercise body/embedding
inference after warmup, so its assessment rate is not representative of a crowded
gate or enrolled recognition. No scene-independent 20 FPS guarantee is claimed.
The configured stream is 20 FPS; other source-side modes and the camera's hardware
maximum were not discoverable through this decoded stream.

CPU YOLO only, built-in 1280×720/30, 600 seconds after 15 seconds warmup:

| Quantity | Measured result |
|---|---:|
| Capture | 29.982 FPS |
| YOLO completion | 13.337 fresh frames/s |
| YOLO 10-second rate p1 / median | 12.400 / 13.400 FPS |
| YOLO gap p50 / p95 / p99 | 72.996 / 89.231 / 123.617 ms |
| Body inference p50 / p95 / p99 | 70.361 / 86.350 / 120.236 ms |
| Capture-to-result p50 / p95 / p99 | 90.103 / 110.039 / 139.449 ms |

First/final 30-second inference rates were 13.500/13.300 FPS. RSS changed from
533.56 to 445.78 MiB; no errors, reader stopped, sampled thermal pressure nominal.
Low Power Mode remained enabled throughout; power-source reporting transitioned
to AC during this run. Capture meets the 30 FPS stability criterion; YOLO fails
20/30/60 FPS targets. This is body detection only, excluding face recognition,
uniform classification and disciplinary decisions. About 9,987 acquired frames
were not selected for measured YOLO completions; latest-frame processing avoids
a stale inference queue. No pre-decode source loss measurement is available.

## Verification

Focused suite: **219 tests passed** across live preview/dashboard, metrics,
camera switching and activation, pipeline/state/model safety, recognition,
network worker/decoder, incremental sync, camera violation integration,
attendance and discipline workflow. After the timer change, **53 UI/native
transition tests passed**, including actual Tk pixel updates and stable image
object counts. Logs preserve exact test names and outcomes.

The tests cover bounded queues, generation/source rejection, frozen frame
ownership, overlap/identity ambiguity, elapsed-time evidence, cooldowns,
duplicate discipline prevention, cancellation at write time, reconnect and
worker shutdown. A new dashboard test feeds 20/30/60 source rates and confirms
the minimum 500 ms assessment interval remains intact. No production student
records or cloud events were created.

Final UI/persistence run: 33 tests, 32 passed and one existing persistence test
error (`test_guard_is_checked_inside_the_write_transaction`, violations subcase:
closed SQLite connection). The test patches every database connection to a single
connection; this failure was already recorded before these edits. Database code
and that test are unchanged. Cancellation while waiting for a real SQLite write
lock passed. The suite is not represented as entirely green.

## Operating settings and feasibility

Keep the existing 30 FPS preview ceiling, built-in 1280×720 acquisition, CPU
model policy, 120 ms minimum tracking offers and 500 ms minimum assessment
offers until a controlled labeled evaluation supports a further change. The
30 FPS setting is a ceiling, not a promised display rate. Front Gate can retain
its current 1080p/20 HEVC profile; no shared source setting was changed. A lower
resolution or H.264 profile might reduce decoding cost but was not measured and
is not an accepted recommendation from this experiment.

**Genuine stable 60 FPS preview is unavailable from the sources tested:** every
AVFoundation format exposed by this MacBook tops out at 30 FPS and the configured
Front Gate stream supplies 20 FPS. Changing a UI constant cannot fix that. The
MacBook's full monitoring preview also falls below 30 FPS under the measured
physical workload. A 60 FPS camera would remove only the acquisition ceiling;
this work does not establish that it would yield 60 FPS monitoring on this UI.

**Stable 60 FPS inference is not established.** CPU live measurements and short
MPS timings do not satisfy that target. Sustained YOLO and end-to-end processing
must be read separately; body/uniform work is conditional on eligible subjects,
while assessments have a deliberate independent cadence. Higher preview FPS
must not multiply disciplinary evidence from a brief observation.

Representative enrolled students, crowd sizes, motion and lighting were not
controlled or labeled. Sleep/wake was not physically induced. Automated source
and cancellation fixtures cover recovery contracts, not every hardware suspend
scenario. Network jitter here means decoded-frame arrival variation, not a
packet-loss or sensor jitter measurement; pre-decode loss cannot be counted.

## Reproduction and evidence

All commands run from the repository with the existing `.venv`. Hardware tools
need macOS camera permission and a native desktop session. Run one camera test
at a time and leave the selected source unchanged:

```sh
.venv/bin/python scripts/benchmark_camera_runtime.py --mode full --source usb --seconds 600 --warmup 15 --output /private/tmp/cbvms-usb-full.json
.venv/bin/python scripts/benchmark_camera_runtime.py --mode full --source front-gate --busy --seconds 600 --warmup 15 --output /private/tmp/cbvms-front-gate-busy.json
.venv/bin/python scripts/benchmark_camera_runtime.py --mode yolo --source usb --seconds 600 --warmup 15 --output /private/tmp/cbvms-usb-yolo.json
```

Use `--mode capture` or `--mode preview` for isolated stages. `--fps` changes an
in-memory request and timer, not the shared source configuration; verify negotiated
settings and measured arrival rate in every JSON. `--source synthetic --fps 60`
is explicitly computational testing without a physical 60 FPS sensor.
`--render-ref 05ccaca` restores baseline render/feed code inside the disposable
benchmark process for repeatable same-input comparisons.

Artifacts are in `docs/validation/camera_performance_2026-10-27/`. Per-run JSON
records configuration, input source code hashes, whole-run percentile timings,
10-second throughput percentiles, one-second process resource samples, model
readiness and shutdown status. `recent_timings` retains only 256 samples per
metric and must not be confused with the whole-run `costs_ms` distributions.
CPU percentages use one core as 100%; GPU utilization was unavailable. Neither
thermal pressure nor RSS alone proves GPU use, temperature, or throttling.

Instrumentation is opt-in. Benchmark wrappers add monotonic timestamps and
bounded numeric deques, with resource sampling about once per second. They retain
no images or identities. Instrumented rates include this overhead; synthetic
before/after uses the same wrappers. Fine-grained compositor, preprocessing,
postprocessing and uniform timings without eligible subjects are not inferred
from absent samples. No end-to-end instrumentation-free A/B was performed.

## Rollback

No commit, push or deployment was made. A scoped patch contains this task's eight
tracked implementation/test edits and three new diagnostic scripts. Preserve any
later edits before reversing it. From the repository root:

```sh
git apply --reverse --check docs/validation/camera_performance_2026-10-27/implementation.patch
git apply --reverse docs/validation/camera_performance_2026-10-27/implementation.patch
```

The reverse check passed against the final implementation. The second command
performs the rollback; it was not executed. Reports and numeric evidence remain
available. No database, source configuration, weights, dependencies or thresholds
need restoration.

## Additional target checks

Thirty-second isolated samples, five-second warmup, same installed CPU models:

| Source / mode / target | Capture FPS | Fresh preview FPS |
|---|---:|---:|
| USB capture only / 30 | 30.000 | — |
| USB preview only / 30 | 30.000 | 26.533 |
| USB full / 20 | 29.833 | 19.067 |
| Front Gate capture only / 30 | 19.967 | — |
| Front Gate preview only / 30 | 19.967 | 19.933 |
| Front Gate full idle / 30 | 19.967 | 19.833 |
| Synthetic full / 20 | 20.000 | 19.333 |

The USB 20 FPS request still negotiated 30 FPS acquisition. Its promising short
sample prompted a full 600-second follow-up: **18.738 preview FPS**, p1 ten-second
rate **15.167**, gap p50/p95/p99 **49.743/73.982/170.804 ms**. First/final 30-second
rates were 19.167/16.467 FPS. Capture remained 29.958 FPS, tracking 5.483/s,
assessment 1.643/s. Required models were ready, queues bounded, all workers stopped,
and no errors were reported. This fails the defined 20 FPS stability criterion;
reducing the timer target is not an accepted performance fix. Scene/power changes
prevent attribution of the late slowdown specifically to thermal throttling.

A synthetic 60 FPS attempt produced 48.267 preview FPS but body/uniform startup
failed. It is retained as a failed diagnostic and excluded from valid full-model
comparisons. Synthetic noise frames do not exercise real student recognition.

## Physical switching acceptance

`scripts/verify_camera_switch.py --output-dir /private/tmp/cbvms-camera-switch-final`
completed with exit 0. Repeated USB→Front Gate→USB transitions delivered fresh
frames and generation-matched analysis. Front Gate first-frame delays were
1.85–2.49 s; USB returns about 0.97–0.98 s. Maximum heartbeat gap was 313.3 ms;
this is responsive recovery, not a zero-stall claim. Capture, tracking, analysis,
persistence, attendance and unknown workers all reported clean shutdown.
Required face/recognition/body/uniform models were ready. The diagnostic's
`failure.json` records the already-known missing optional earring model, not a
camera transition failure. Numeric event traces are retained; no frame or native
stack dump is included in the report artifacts.

The single synthetic 60 FPS retry completed without errors and with all required
models ready: 60.000 source / 58.567 preview FPS, preview p1 ten-second rate 58.206,
gap p99 30.581 ms. The first startup failure did not recur; its root cause is
unverified. This 30-second noise-input result is not sustained or physical 60 FPS
acceptance and does not include representative student inference. It does show
that the renderer is not hard-capped at 20 or 30 FPS.

Additional isolated sync check: 100 pending operations drained in 10 simulated
transport calls, no pending operations remained; busy timer jitter p95 2.085 ms
versus idle 2.026 ms. A deliberately held SQLite writer caused the expected
OperationalError after 287.852 ms against a configured 250 ms timeout. This tests
disposable-data contention, not production network service latency.

Five 100,000-call instrumentation microbenchmarks measured median 0.041 µs per
disabled `observe` call and 0.384 µs enabled. These numbers cover the metric
recorder only, not all wrappers, Tk polling, tracing or resource sampling. All
nine changed/new Python files compile; `git diff --check` and scoped rollback
reverse-check pass. Database implementation and the failing persistence test
are byte-identical to baseline revision `05ccaca`.

## Final assessment

A. Built-in acquisition sustained approximately 30 FPS; configured Front Gate
approximately 20 FPS. B. Full-monitoring fresh preview measured 18.342 FPS on USB
at target30, 18.738 at target20, and stable 19.893 on the observed Front Gate scene.
C. CPU body YOLO sustained 13.337 fresh inferences/s. D. USB full assessments were
1.355/s at target30, capture-to-assessment p95 919.461 ms; these are completions,
not confirmed correct decisions. Accuracy and enrolled/crowded-scene feasibility
remain unverified. Keep the existing operating policy; accept the measured small
rendering improvements without a 60 FPS or accuracy promise.
