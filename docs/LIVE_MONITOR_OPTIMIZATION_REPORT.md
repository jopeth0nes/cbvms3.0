# Live-monitor optimization — 2026-10-23

Implemented against `244d6805ee00c5f8b3bf60f9fecf0d288dc70b7c` on a clean starting
checkout. Changes are local and uncommitted. The main measured improvement is
earlier readiness of torso and uniform inference; steady model inference speed
has not materially changed. Identity, confidence, confirmation, evidence, expiry,
and disciplinary thresholds remain unchanged.

## Confirmed causes and changes

| Finding | Implemented change | Files |
| --- | --- | --- |
| Face warmup already began during login; body/classifier warmup waited for the dashboard. | One shared resource bundle starts preparation during login, with two slots and face/recognition priority. Dashboard reuses the same objects. | `main.py`, `core/monitor_models.py`, `core/model_readiness.py`, `ui/dashboard.py` |
| Readiness needed bounded scheduling and shutdown handling for newly queued work. | Explicit failure/retry, queued cancellation, complete singleton publication, and bounded shutdown wait. A stalled native owner keeps its slot/lock until it returns. | Same files; `tests/test_monitor_startup.py` |
| Localization handed off an already owned frame through `dataclasses.replace`, copying the entire image again. | Metadata-only handoff shares that task's read-only pixels, cancellation token, timestamp and generation. Ordinary task construction still copies external buffers. | `core/live_pipeline.py`, dashboard, `tests/test_monitor_performance.py` |
| Empty scenes queried term/audio configuration although no observation could use it. | Skip those reads for empty observations while still updating state. No student/status/configuration cache was added. | Pipeline and performance regressions |
| Preview rate alone obscured readiness, queue and inference costs. | Opt-in fixed-name numeric timing summaries, bounded to 256 samples per metric, with lifetime counts and p50/p95. | `core/performance.py`, diagnostics, model safety, recognizer, detector, trainer, dashboard |
| The synthetic preview benchmark retained every PIL image through Mock call history. | Replace the PhotoImage mock with a plain function. This fixes the benchmark's artificial memory growth. | `scripts/benchmark_live_monitor.py` |

Latest-frame queues remain size one. Tracking/assessment, persistence and camera
ownership remain separate from Tk rendering. Offer intervals were not reduced.
Same-frame detection/landmark reuse already existed; it is preserved, not claimed
as a new optimization. Optional earring preparation now warms real prediction,
but this checkout has no earring weights and reports that component unavailable.

## Environment and comparable measurements

Apple M4, 16 GiB RAM, 10 logical CPUs; macOS 26.5 arm64; Python 3.12.13;
OpenCV 4.10.0.84, NumPy 1.26.4, ONNX Runtime 1.19.2, InsightFace 0.7.3,
PyTorch 2.2.2, Ultralytics 8.3.27. Installed weight hashes are in
[`weights.json`](validation/live_monitor_2026-10-23/weights.json).

The startup probe compares the original scheduling code against the new bundle
using the same installed models, inputs and settings, three fresh processes per
condition. It simulates a five-second login. It does **not** time full application
boot, database/cloud startup, a human login, or a genuinely uncached launch.
Filesystem caches were not flushed. Fast real logins can still precede readiness.

| Measurement | Baseline | Updated |
| --- | ---: | ---: |
| Body readiness remaining after simulated login, three runs | 0.919 / 0.861 / 0.772 s | 0 / 0 / 0 s |
| Uniform readiness remaining after simulated login | 0.876 / 0.824 / 0.741 s | 0 / 0 / 0 s |
| Face readiness from preparation start | 2.051–2.274 s | 2.085–2.140 s |
| Recognition readiness from preparation start | 2.158–2.380 s | 2.192–2.255 s |
| All required models ready from preparation start | 5.777–5.921 s | 2.887–3.002 s |
| Serial model cycle p50, 720p blank input | 309.119–309.539 ms | 308.695–310.248 ms |
| Owned-frame handoff p50, 720p, 500 iterations × 3 | 0.0662–0.0848 ms | 0.0016–0.0017 ms |
| Empty-scene analysis p50, disposable SQLite, 500 × 3 | 0.6025–0.6125 ms | 0.0261–0.0268 ms |

The microbenchmarks alternate comparison order. These sub-millisecond savings
remove redundant work; they are not a claim of a comparable end-to-end speedup.
Ten-second startup-probe steady windows used 33 serial cycles each. Detailed
p50/p95 and component load/warm timings are retained in
[`baseline-desktop.json`](validation/live_monitor_2026-10-23/baseline-desktop.json)
and [`candidate-desktop.json`](validation/live_monitor_2026-10-23/candidate-desktop.json).

An early sandbox run rebuilt Matplotlib's font cache in a temporary directory,
adding about 16 seconds to import time. Normal desktop runs supersede that
environment artifact. No product workaround was added for it.

## Sustained checks

The 60-second installed-model probe completed 192 serial cycles (3.19/s).
Cycle p50/p95 was **310.387/331.380 ms**: face detection 96.367/111.001,
embedding 180.077/185.988, body 28.676/33.971, uniform 4.832/6.079 ms.
Required models stayed ready; blank scenes produced no face/body detections.
Sampled RSS started at 1068 MB, peaked at 1073 MB and ended at 725 MB. Process
CPU averaged 619% of one core. This intentionally serial synthetic probe is not
the live application's assessment rate.

The corrected 60-second controlled preview test used synthetic 720p/30 FPS input,
five synthetic people and a 200 ms analysis load. It achieved **26.83 unique
preview FPS**, 4.5 analyses/s, frame-age p95 39.092 ms and render-tick p95
6.898 ms. Maximum queued and in-flight analysis were each one; the worker stopped
cleanly. Capture-to-assessment p50/p95 was 217.566/234.110 ms and queue wait
0.028/0.050 ms. Sampled RSS after warmup stayed around 116.05–116.88 MB and ended
at 112.30 MB; CPU averaged 552% of one core. This supports bounded backlog during
this interval, not a long-term memory-leak or physical-camera FPS guarantee.

Artifacts: [`sustained-models.json`](validation/live_monitor_2026-10-23/sustained-models.json),
[`pipeline-final.json`](validation/live_monitor_2026-10-23/pipeline-final.json).

## Consented physical-camera checks and accuracy limits

Two MacBook-camera probes ran for 35 and 20 seconds with the user's explicit
consent. They used a temporary in-memory reference and synthetic roster, native
Tk preview, and the actual tracking/assessment models. They did not run the full
dashboard or authenticate against real students. No business records, images or
embeddings were saved. All camera/analysis workers stopped; no worker/model error
occurred except the expected missing earring asset.

| Measurement | Probe 1 | Probe 2 |
| --- | ---: | ---: |
| First camera frame | 1.479 s | 1.587 s |
| First face | 3.612 s | 3.675 s |
| Temporary reference ready | 4.108 s | 4.246 s |
| First stable identity, including reference setup and confirmation | 5.161 s | 5.292 s |
| First valid torso | 6.578 s | 6.180 s |
| Camera capture / native probe preview | 29.54 / 14.23 FPS | 29.44 / 14.22 FPS |
| Capture-to-assessment p50 / p95 | 700.932 / 838.059 ms | 719.693 / 902.040 ms |
| Successful uniform assessments | 0 | 0 |

Probe 2 confirmed 1280×720 capture. Its 20 torso assessments abstained with
“Shirt obscured or mostly skin; show more of your shirt”; one earlier result was
waiting for identity and one for the uniform model. The guard was preserved.
This does not determine whether those abstentions were correct or false rejects
without labeled torso/clothing ground truth.

Artifacts: [`physical-camera.json`](validation/live_monitor_2026-10-23/physical-camera.json),
[`physical-camera-reasons.json`](validation/live_monitor_2026-10-23/physical-camera-reasons.json).
These are updated-code observations, not paired before/after camera accuracy
measurements. Real-world false-match, unknown false-accept/reject, association and
uniform error rates remain unmeasured. Synthetic regressions cover expiry,
ambiguity, ownership and duplicate behavior, but cannot establish those rates.

Before claiming improved accuracy, use a consented labeled evaluation with known
and unknown participants, frontal/profile/distant faces, entry/exit, multiple
people crossing, occlusion, poor lighting and partial torsos. Replay identical
frames against baseline and candidate; record missed detections, identity false
accepts/rejects, one-to-one association errors, uniform false positives/negatives,
abstention coverage and capture-to-confirmed-result latency separately. Validate
compliant and incorrect uniforms and the missing earring model independently.

## Hardware acceleration

Actual post-prediction YOLO backend **and parameter** devices were `cpu` for both
the product's unspecified-device calls and explicit CPU controls. Explicit MPS
trials used `mps` / `mps:0`. PyTorch reports MPS built and available, CUDA
unavailable. Face detection and recognition use `CPUExecutionProvider`; their
sessions retained that provider after preparation and inference. Earring device
execution cannot be checked without its missing weights. Training explicitly
selects CPU in source; training was not run.

Two final desktop runs used fresh subprocesses per condition, 25 repeated calls
after first inference, the same local weights, fixed blank pixels and synchronized
accelerator timing. Results below are the range of each run's p50/p95; they are
isolated model probes, not full-application throughput. Face detection input was
320×320, embedding input 112×112, body input 1280×720, uniform input 224×224.

| Probe | Steady p50 range | Steady p95 range | Finding |
| --- | ---: | ---: | --- |
| Body, product default CPU | 25.578–26.080 ms | 28.118–28.693 ms | Actual default verified |
| Body, explicit CPU | 28.246–29.964 ms | 40.703–41.075 ms | CPU control works |
| Body, explicit MPS | 11.944–20.758 ms | 22.409–23.220 ms | Faster on blank inputs, variable; first call 1.71–1.75 s versus default CPU 0.69–0.70 s |
| Uniform, default CPU | 3.984–4.148 ms | 5.103–5.180 ms | Actual default verified |
| Uniform, explicit CPU | 4.025–4.177 ms | 5.408–5.584 ms | CPU control works |
| Uniform, explicit MPS | 4.491–4.747 ms | 5.247–5.985 ms | No steady advantage; first call 0.44–0.46 s versus CPU about 0.02 s |
| Face detector, CPU | 70.617–70.695 ms | 71.016–71.174 ms | Installed router class is `RetinaFace` |
| Face detector, CoreML | Failed both runs | — | Runtime static-output shape rank mismatch |
| Embedding, CPU | 127.562–127.680 ms | 128.697–128.797 ms | Installed class `ArcFaceONNX` |
| Embedding, CoreML + CPU fallback registered | 3.889–4.099 ms | 4.295–5.567 ms | Faster, but numerical output differs and session load rises to 2.86–2.92 s |

Both body devices returned zero boxes on blank pixels, so their matching outcomes
do not establish box equivalence on people. MPS uniform top class matched CPU,
with maximum probability delta 0.00000256. CoreML embedding output was finite and
the same shape, but its cosine similarity to the CPU blank-input embedding was
0.9976904745, maximum normalized-coordinate delta 0.0086941414 (raw delta
0.05122843). This is not an identity-accuracy test. Listing CoreML and CPU in a
session does not establish which nodes used GPU, ANE or CPU; per-node hardware
utilization remains unverified.

An additional strict synchronization pass verifies both MPS models complete
without suppressing synchronization errors. Accelerator errors now fail the
probe explicitly. The earlier exploratory device run used a fresh font cache;
its 14.9-second first CPU model load is excluded from these comparisons.

The CPU reset/no-reset experiment confirmed InsightFace recreates its already-CPU
session during `prepare(ctx_id=-1)`: detector preparation cost about 15 ms and
recognition 62–86 ms; skipping the reset cost under 0.01 ms, with identical blank
outputs. This experiment remains outside product code. Its small startup saving
does not establish a further post-login benefit once models are already ready.

**Decision: retain tested CPU product behavior.** No accelerator/runtime was
installed and no provider, weight, resolution, embedding or training configuration
was changed. The strongest next candidate is a separate, reversible evaluation of
CoreML for embeddings only, retaining CPU detection and a tested explicit CPU
fallback. It needs consented labeled faces, comparison against the existing CPU
gallery at unchanged thresholds/margins, provider failure tests, per-node profiling
and combined camera/preview contention measurement. Its longer cold preparation
and numerical drift must be assessed before adoption. MPS body inference also needs
nonempty multi-person/torso correctness and sustained UI testing; the uniform MPS
results do not justify adoption here. No embedding migration or re-enrollment is
part of this change.

Artifacts: [`devices-final-1.json`](validation/live_monitor_2026-10-23/devices-final-1.json),
[`devices-final-2.json`](validation/live_monitor_2026-10-23/devices-final-2.json),
[`device-sync-verification.json`](validation/live_monitor_2026-10-23/device-sync-verification.json).

## Regression evidence

Targeted checks used synthetic identities and disposable databases. No production
database, deployment, model replacement, enrollment migration or retraining was
performed. Counts below are separate runs and should not be summed as unique tests.

| Run | Result |
| --- | --- |
| Startup/performance, readiness, dashboard, shared-model safety, identity, pipeline/state | 123 passed |
| Final singleton/queue changes, diagnostics enabled | 20 passed |
| Camera activation/switch/reconnect/freeze, persistence, attendance, enrollment, notification/term integration | 157 discovered: 155 passed, 2 errors described below |
| Audio with repository's required test import path | 30 passed |
| Torso/evidence, unknown sightings, violation workflow and suspensions | 62 passed |
| Native monitor/camera transition UI, synthetic capture | 4 passed |
| Final device benchmark helpers | 7 passed |

One integration error was the existing bare `test_live_pipeline` import used by
`test_detection_audio`; `PYTHONPATH=tests` resolves it and all 30 audio tests pass.
Three other failures were reproduced from the **original commit's source** in an
isolated temporary export, with the same environment:

1. `PortalTests.test_pending_visible_only_to_authenticated_string_owner`.
2. `PortalTests.test_background_refresh_sees_committed_record_without_logout`.
   Both fixtures expect a violation ID but get `None` from `log_violation`.
3. `LivePersistenceTests.test_guard_is_checked_inside_the_write_transaction`:
   its violation subcase uses a closed SQLite connection.

These unrelated failures remain; assertions were not weakened. No new failure
was found in the exercised workflows. Live cloud synchronization and representative
production portal workflows were not exercised. Existing authorization and cloud
code was not modified.

Exact commands and log filenames are in
[`LIVE_MONITOR_OPTIMIZATION_CHECKPOINT.md`](LIVE_MONITOR_OPTIMIZATION_CHECKPOINT.md).

## Reproduce and observe

Run from the repository with its existing virtual environment and local weights.
Use the normal desktop environment for native hardware results. The benchmark
scripts do not open the production database; the pipeline script creates its own
temporary SQLite database.

```sh
.venv/bin/python scripts/benchmark_monitor_startup.py --policy baseline --runs 3 --seconds 10 --login-delay 5 --output /private/tmp/cbvms-monitor-baseline-desktop.json
.venv/bin/python scripts/benchmark_monitor_startup.py --policy candidate --runs 3 --seconds 10 --login-delay 5 --output /private/tmp/cbvms-monitor-candidate-desktop.json
.venv/bin/python scripts/benchmark_monitor_startup.py --policy candidate --runs 1 --seconds 60 --login-delay 5 --output /private/tmp/cbvms-monitor-sustained-models.json
.venv/bin/python scripts/benchmark_monitor_pipeline.py --runs 3 --iterations 500 --sustained-seconds 60 --output /private/tmp/cbvms-monitor-pipeline-final.json
.venv/bin/python scripts/benchmark_monitor_devices.py --output /private/tmp/cbvms-monitor-devices.json --repeats 25 --timeout 120
```

`CBVMS_PERF=1` enables aggregate timings for an otherwise authorized application
launch. Omit it to disable them. Existing diagnostic events are separate; the new
numeric summaries never retain student identities, pixels or embeddings. Physical
camera probes require a present consenting participant and the explicit
`--consenting-participant` option; do not use them as unattended benchmarks.

## Separate training follow-ups

No training rewrite is included. A later phase should group splits by person and
session, keep near-duplicates together, and reserve an untouched test set. Bind
immutable dataset manifests/image hashes to model versions; never describe a
reconstructed unverified split as guaranteed held-out. Check that cropped datasets
are complete/current; validate uniform and earring preprocessing/augmentation
separately. Evaluate candidates before replacing active weights, preserve rollback,
and count unknown predictions, failures and abstentions separately. Changing the
embedding model or regenerating student embeddings requires a separate compatibility
decision, evaluation and explicit approval.

## Rollback

The implementation patch is preserved alongside the validation artifacts. Close
the application, preserve any later edits, and first check that the reverse patch
applies cleanly:

```sh
git apply --reverse --check docs/validation/live_monitor_2026-10-23/implementation.patch
git apply --reverse docs/validation/live_monitor_2026-10-23/implementation.patch
```

The patch covers only this task's source, benchmark and test changes; reports and
aggregate evidence remain. If the check fails because files have changed since
delivery, reconcile those hunks rather than resetting the working tree. No data,
weights, thresholds, dependencies or database migrations need rollback. These
instructions have not executed the rollback.
