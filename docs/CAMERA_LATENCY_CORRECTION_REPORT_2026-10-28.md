# CBVMS latency correction — 28 October 2026

The old candidate scheduler adds avoidable delay. The opt-in freshness correction
substantially removes that delay, and body MPS benefits this measured workload.
Neither the candidate route nor accelerated face recognition is approved for
production accuracy. The operational CPU route remains the default. Changes are
uncommitted above `7c1680a`; no deployment or real business writes occurred.

## Causes and implemented corrections

The candidate router retained older high-scoring frames, rejected equal-quality
fresh replacements when full, and selected the oldest pending evidence. In the
initial AC run, candidate selection waited 658 ms median / 1,175 ms p95 even though
assessment-worker queue wait was negligible. Recognition and tracking CPU inference
were the other substantial costs. Lock waits, image copies and gallery matching
were not material bottlenecks in the empty-gallery workload.

`CBVMS_ASSESSMENT_FRESH_SELECTION=1` now selects the newest usable observation
within the chosen passage, discards its older pending evidence, and removes old
sibling work already superseded by the assessed frame. Passage aging survives
replacement frames, active work remains protected, and refresh/confirmation
requirements remain unchanged. The flag defaults off and only operates through
the observation-only candidate route. Final review restored the exact prior
active-ticket replacement rule when the flag is off; final controls below were
rerun afterward. Earlier diagnostic runs are retained separately.

This bypasses relative quality-score retention, whose accuracy benefit has not
been established. Geometry/visibility, face-to-torso ownership, overlap guards,
source timestamps, distinct-frame evidence, gallery/camera cancellation and
current eligibility checks remain. It is not an identity cache. No model weights,
thresholds, dependencies, schema, credentials or real gallery data changed.
Existing same-frame face landmarks/body localization were already reused; adding
recognition caching or targeted crops was not justified to remove the measured
queue delay and would add identity/ownership risks without labeled validation.

Timing now separates face alignment, embedding, gallery matching, uniform
preprocessing, confirmation compute, queue wait, UI availability and persistence.
Bounded `CBVMS_TRACE=1` traces retain numerical timestamps and salted frame/passage
aliases, never images, embeddings or student IDs. Capture timestamps follow
backend decode, not sensor exposure. Per-observation traces retain 4,096 rows with
explicit overflow counts; early rows are absent in long runs. Native `costs_ms`
and stage sample counts cover the measurement interval after warmup, while
`recent_timings` is a bounded lifetime tail that may include startup. Do not sum
nested or overlapping worker spans as serial time. Zero-valued rejection metrics
are counters, not inference durations.

## Physical results and their limits

The Mac reported AC power and Low Power Mode 0. Battery status nevertheless said
“discharging”; this discrepancy is recorded, not hidden. No power setting changed.
The prior 1,912 / 2,697 ms result used battery/Low-Power-on conditions and is not a
matched before/after control. Thermal-status queries failed; a throughput decline
alone does not establish thermal throttling.

All runs below used the native operational models/workers, a disposable empty
gallery, writes disabled and 15 seconds warmup. Scenes changed between sequential
live runs, so these are workload observations, not identical-recording causal or
accuracy comparisons. Some lightweight regression tests overlapped the first CPU
freshness run; no competing camera or heavy model benchmark ran concurrently.

| MacBook route | Measured seconds | Assessment samples | Capture-to-assessment p50 / p95 / p99 ms | Capture / fresh preview FPS |
| --- | ---: | ---: | --- | --- |
| Operational CPU, short | 60 | 111 | 214.3 / 239.7 / 248.6 | 30.00 / 29.35 |
| Existing candidate CPU, final control | 60 | 85 | 910.9 / 1445.3 / 1456.5 | 30.02 / 28.95 |
| Existing candidate MPS, final control | 60 | 82 | 947.5 / 1558.1 / 1752.6 | 30.00 / 26.07 |
| Operational CPU, sustained | 600 | 1045 | 482.0 / 559.7 / 587.6 | 29.99 / 22.61 |
| Freshness CPU, sustained | 600 | 819 | 338.6 / 444.9 / 495.3 | 30.00 / 26.02 |
| Freshness MPS, sustained | 600 | 782 | 215.6 / 264.4 / 300.3 | 30.00 / 28.81 |

All completed physical runs exited cleanly without harness errors and reported
camera/tracking/assessment workers stopped. CPU freshness retained at most two
candidate images in sampled operation (about 5.27 MiB). The MPS sustained run's
sampled RSS ranged 395–763 MiB; queues and candidate retention remained bounded.
These are ten-minute stability checks, not an all-day or sleep/wake certification.
No final run longer than ten minutes was performed.

For sustained freshness MPS, post-warmup stage p50 / p95 / p99 (ms) was:

| Stage | Samples | p50 / p95 / p99 ms |
| --- | ---: | --- |
| Face localization | 4492 | 57.9 / 77.8 / 97.1 |
| Body YOLO | 4404 | 23.9 / 26.7 / 30.1 |
| Face alignment | 783 | 0.3 / 0.4 / 0.5 |
| ArcFace embedding | 784 | 112.2 / 143.9 / 167.5 |
| Empty-gallery matching | 784 | 0.0 / 0.0 / 0.0 |
| Candidate waiting | 783 | 0.9 / 1.9 / 2.1 |
| Assessment worker queue | 783 | 0.1 / 0.1 / 0.1 |
| Confirmation computation | 782 | 0.1 / 0.2 / 0.2 |
| Worker result to UI availability | 782 | 15.5 / 31.9 / 40.1 |
| Owned frame copy | 4496 | 0.1 / 0.1 / 0.2 |

Uniform after-warmup throughput and business persistence latency are unavailable:
no enrolled gallery was used and observation mode suppressed writes. The warmup
classifier call is not counted as enrolled uniform throughput. Confirmation
computation is separate from the elapsed evidence-collection requirement.

**First-useful and entry delay must not be confused with capture-to-assessment.**
The CPU freshness run had one tracker passage, with first-useful assessment at
514 ms including startup. The MPS run had two lifetime tracker passage starts
(421 ms median / 551 ms interpolated p95), including warmup. After warmup it had
only ONE first-useful sample: 276 ms. That passage's tracker-entry-to-first-
assessment was **56,981 ms**, about 56.7 seconds of waiting before usable evidence.
The router retains first entry across face-geometry/90%-visibility rejection;
new numeric rejection counters identify those cases in future runs. No footage
was saved to verify the visual cause. Tracker entry is not ground-truth first
appearance, and none of these counts establish population p95. The 500 ms
first-useful goal and entry-to-correct-confirmed result remain unproven.

Front Gate was tested separately for 60 seconds with simulated background sync:
19.93 capture / 19.93 fresh-preview FPS at its current 1080p/20 FPS source mode,
665 disposable sync operations in 133 simulated batches. No usable passage or
assessment occurred. This validates acquisition/preview/sync plumbing only, not
Front Gate recognition or simultaneous-camera throughput. No real cloud-sync
latency was measured.

## Controlled public-fixture checks

The same local eight-frame public Zidane fixture, empty gallery, weights,
resolution and thresholds were replayed sequentially in forward then reverse
condition order; the final flag-off CPU/MPS controls were then repeated in
reversed device order after the last scope correction. The baseline calls the actual operational dashboard dispatch
method, plus the actual tracker/LiveWorker/LiveProcessor. All runs reported zero
pacing skips. This repeated-image fixture is timing/plumbing evidence, not
independent temporal evidence or labeled student footage.

| Route | Assessments per run | Capture-to-assessment p95, two runs (ms) |
| --- | ---: | --- |
| Operational CPU | 4 | 450 / 421 |
| Existing candidate CPU, final flag-off controls | 6 | 1,711 / 1,711 |
| Existing candidate MPS, final flag-off controls | 6 | 1,716 / 1,707 |
| Freshness CPU | 5 | 605 / 633 |

Freshness helps the old candidate design but still has a worse tail than the
operational control in this short fixture. Retained refresh waiting and sparse
end-of-recording observations remain relevant. Different offer counts are
reported, not normalized away. Do not claim the new route universally beats the
original or enable it by default. Raw per-run samples, including the combined
MPS/freshness probe (5 results, p95 583 ms), are retained in the evidence directory.

## Backends, accepted and rejected experiments

- **Body YOLO:** CPU default; optional MPS verified at `mps:0`, synchronized with
  no fallback in successful physical runs. On the same public bus/Zidane images,
  200 timed calls per device gave CPU 37.43 / 47.99 / 50.61 ms versus MPS
  12.01 / 21.71 / 27.62 ms. Both repeatedly detected nonempty multi-person scenes
  (4 and 2 people). This supports MPS throughput, not box/identity accuracy.
  Existing bounded one-time CPU fallback remains, with injected-failure tests.
- **Face detection:** `CPUExecutionProvider`. Earlier Core ML detector failure
  was an incompatible static-output shape rank (five-dimensional static output
  versus two-dimensional inferred output). It was investigated from retained
  error evidence; no unsupported shape patch, weight edit or blind retry/upgrade
  was introduced. CPU detection remains active.
- **Face embedding:** `CPUExecutionProvider` in the application. The separate
  same-weight Core ML probe executed Core ML kernels (84 profile events, with
  168 CPU events), not merely provider registration. Four public faces / 80 timed
  calls per backend gave CPU 97.73 / 119.42 / 161.76 ms versus Core ML
  2.45 / 2.77 / 2.98 ms. Loading rose from 98 ms to 2,063 ms. Minimum same-face
  cosine versus CPU was 0.999707 and maximum pairwise-cosine change 0.002672.
  This does NOT validate existing-gallery recognition near decision boundaries.
  Core ML remains a rejected production rollout pending held-out gallery checks;
  CPU/GPU/ANE execution inside Apple's runtime is not distinguished by this probe.
  Splitting the existing CPU alignment/embedding calls produced normalized
  comparison delta 1.49e-8, consistent with float rounding; no model substitution.
- **Uniform classifier:** CPU in the application. A separate synchronized
  synthetic-input probe (40 calls each) measured CPU 3.24 / 5.30 / 5.35 ms versus
  MPS 3.42 / 3.84 / 4.29 ms. MPS first-call cost was 235.5 ms versus CPU 12.8 ms.
  Median benefit was absent, and actual garment/gallery throughput is untested;
  retain CPU. Equal synthetic top class is not uniform accuracy evidence.

## Accuracy and verification

No authorized labeled student footage/evaluation gallery was supplied or found.
Passage-level wrong identities, unknown false acceptance/rejection, wrong torso
ownership, false/missed uniform violations, abstentions and time to correct
confirmation remain **unverified**, with no scored accuracy sample or confidence
interval. Fast entry/exit, crossings/occlusion and lighting are required held-out
cases. No zero-error production claim follows from unit tests or public images.

Replay now retains passages with no completed result, distinguishes explicit
`__unknown__` outcomes and face-visible opportunities, and flags annotated torso
IoU mismatches for manual review. Uniform scoring remains conditional on correct
identity. No live duplicate-business-event attempt rate was measured; existing
transactional/duplicate guards are regression-tested with disposable fixtures.
Annotation can record body-visible/face-hidden entry, preserving waiting time.
The exact recording, local gallery and person/session-held-out checklist is in
[the evaluation guide](CAMERA_ASSESSMENT_EVALUATION.md).

**167 focused tests passed in 5.362 seconds with trace and freshness enabled.**
Coverage includes latest-frame selection, sibling obsolescence, active-ticket
protection, entry-before-usability timing, bounded/thread-isolated traces, missing
replay results, unknown/torso accounting, expiry, overload, gallery invalidation,
GPU failure/fallback, camera switching, eligibility/archive restrictions and
observation-only write gates. **The same 167 tests passed in default mode in 5.211 seconds.**
`git diff --check` also passes.

The separate persistence suite remains **8 passes / 1 existing fixture error**:
`test_guard_is_checked_inside_the_write_transaction` reuses a closed connection.
No database implementation changes were made. The failure is preserved, not
silently counted as a pass. Physical acquisition, synthetic correctness fixtures,
public-model probes and labeled accuracy are explicitly different evidence types.

## Commands, default and rollback

Recommended production configuration remains the operational route and CPU.
To evaluate the corrected candidate with measured beneficial body MPS:

```sh
CBVMS_ASSESSMENT_OBSERVATION_ONLY=1 CBVMS_CLEAR_FRAME_SELECTION=1 \
CBVMS_PRIORITIZED_ASSESSMENT=1 CBVMS_ASSESSMENT_FRESH_SELECTION=1 \
CBVMS_BODY_YOLO_BACKEND=mps CBVMS_TRACE=1 CBVMS_PERF=1 \
.venv/bin/python scripts/benchmark_camera_runtime.py --mode full --source usb \
  --seconds 600 --warmup 15 --output /private/tmp/cbvms-fresh-mps.json
```

For observation-only app evaluation, use those same environment values with
`.venv/bin/python main.py`. Do not remove observation-only while keeping candidate
changes. Use `--source front-gate` as a separate test with people present.
Restore ordinary operation by restarting from a shell with:

```sh
unset CBVMS_ASSESSMENT_OBSERVATION_ONLY CBVMS_CLEAR_FRAME_SELECTION
unset CBVMS_PRIORITIZED_ASSESSMENT CBVMS_ASSESSMENT_FRESH_SELECTION
unset CBVMS_TRACE CBVMS_PERF
CBVMS_BODY_YOLO_BACKEND=cpu .venv/bin/python main.py
```

No whole-file rollback is needed. Reverse only correction-specific hunks if code
rollback is desired; preserve unrelated synchronization, recovery, Recycle Bin,
Reports, enrollment and camera-switching work.

Evidence: [summary JSON](validation/camera_latency_correction_2026-10-28.json),
[per-run artifacts](validation/camera_latency_correction_2026-10-28/), and
[checkpoint](CAMERA_LATENCY_CORRECTION_CHECKPOINT_2026-10-28.md). Source hashes,
model hashes, power observations, counts, trace overflow and failed fixture logs
are retained. No production behavior was enabled and no student data uploaded.
