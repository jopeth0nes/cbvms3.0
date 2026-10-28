# Latency correction checkpoint — 28 October 2026

The **FINAL STATE** section at the end is authoritative. Earlier entries preserve
the operation history and must not be interpreted as still running.

Branch main, base commit 7c1680a. Uncommitted correction work; no deployment.
Supersedes the old assessment checkpoint only for this correction pass.

Implemented: opt-in CBVMS_ASSESSMENT_FRESH_SELECTION selects newest usable evidence
within chosen passage and removes older queued evidence; preserves passage aging,
confirmation rules and all-person ownership. Default off, candidate route still
observation-only. No recognition cache or weight/backend default changes.
Bounded CBVMS_TRACE numerical observation spans, separate alignment/embedding,
gallery matching, preprocessing, frame-copy, confirmation and UI timings.

Modified: core/assessment_candidates.py, core/live_pipeline.py,
core/performance.py, core/recognizer.py, core/trainer.py,
scripts/benchmark_camera_runtime.py, scripts/replay_camera_assessment.py,
tests/test_assessment_candidates.py, ui/dashboard.py.
New: tests/test_observation_traces.py and this checkpoint.

Evidence so far (private local JSON, no saved frames, empty disposable galleries):
- /private/tmp/cbvms-correction-baseline.json: 60s +15s warmup, CPU operational
  route with observation-only writes suppressed; capture-to-assessment
  p50/p95/p99 214.347/239.681/248.599 ms, capture30/fresh29.35 FPS.
- /private/tmp/cbvms-correction-candidate-cpu.json: same physical source/duration,
  selection+priority on; 886.991/1412.170/1430.419 ms. Selection wait median658.284ms.
- /private/tmp/cbvms-correction-candidate-mps.json: same candidate flags,
  actual mps:0, no fallback; 809.233/1352.598/1360.722 ms.
These sequential live scenes are NOT identical prerecorded passage comparisons.
All three exited0 with no harness errors and all workers stopped. AC power,
lowpowermode0 reported by pmset; battery nevertheless reports discharging.
Initial sandbox baseline attempt exited134 before measurement; no result claim.

Tests: 148 focused tests passed in5.385s before latest trace/aging additions.
Then 22 trace/candidate tests passed in0.069s. Full final affected check pending.
Original freshness regression failed with unsupported constructor argument before
implementation; it now checks latest equal-quality evidence and no older replay.

ACTIVE operation: exec session13010, task-owned command:
CBVMS_TRACE=1 CBVMS_ASSESSMENT_OBSERVATION_ONLY=1 CBVMS_CLEAR_FRAME_SELECTION=1 CBVMS_PRIORITIZED_ASSESSMENT=1 CBVMS_ASSESSMENT_FRESH_SELECTION=1 CBVMS_BODY_YOLO_BACKEND=cpu .venv/bin/python scripts/benchmark_camera_runtime.py --mode full --source usb --seconds 600 --warmup 15 --output /private/tmp/cbvms-correction-fresh-cpu-600.json
Check its .progress.json and session outcome before starting any camera/model run.
Do not infer failure from an interrupted chat. No installs or migrations running.

Next: finish sustained freshness run; inspect timings/traces, then sequentially
complete matched replay and backend checks plus Front Gate check if accessible.
Final regression, sanitized artifacts/report, launch/rollback commands pending.
No labeled student footage/gallery identified. Accuracy and correct-confirmation
remain unverified; preserve CPU/default operational route pending evidence.
Rollback experiment: unset CBVMS_ASSESSMENT_FRESH_SELECTION, CBVMS_TRACE;
ordinary operation also unsets CBVMS_ASSESSMENT_OBSERVATION_ONLY,
CBVMS_CLEAR_FRAME_SELECTION, CBVMS_PRIORITIZED_ASSESSMENT, keeps body CPU, restarts.

Progress update during session13010 (still running, >5min): 153 focused tests
passed8.991s; fresh-mode subset30 passed0.055s. Added later sibling-obsolescence
regression: candidate/trace/replay31 passed0.061s. Persistence9 tests:8pass,
1 same documented closed-connection fixture error, log
/private/tmp/cbvms-correction-persistence-tests.log. No DB changes made.
Additional modified file tests/test_camera_replay.py; annotation utility now permits
body-visible/face-hidden entry labels and explicit __unknown__ identity labels.
New scripts/benchmark_face_compatibility.py prepared but NOT executed yet. Do not
claim its probe passed. Latest trace metadata/sibling cleanup changes postdate the
running CPU run; final verification must cover them separately.

CPU sustained run completed exit0, no errors, all workers stopped:
/private/tmp/cbvms-correction-fresh-cpu-600.json. 600s after15s warmup,
capture29.995FPS/fresh26.015FPS. Capture-to-assessment p50/p95/p99
338.649/444.922/495.335ms. Candidate selection latest256 samples
1.236/1.587/1.958ms. ONE passage first-useful513.737ms, not populationp95.
Bounded queues/candidates. Latest UI metrics/tracing and sibling cleanup were
added after this run began; source hashes in JSON describe executed revision.

ACTIVE now session65810, same command as above with
CBVMS_BODY_YOLO_BACKEND=mps and output
/private/tmp/cbvms-correction-fresh-mps-600.json, duration600/warmup15.
CPU run is stopped; no competing task camera reader. Next after MPS completion:
operational CPU sustained control, Front Gate availability check, sequential
public-fixture replay/YOLO/face/uniform probes, final artifacts/report.
Latest broad trace+fresh test:154pass13.454s (before last replay missing-result
counter correction). Thermal queries unavailable; no thermal-throttling claim.

Prepared but not started: /private/tmp/cbvms-correction-tail-runner.py (sequential
600s baseline CPU,60s Front Gate MPS+simulated sync,public YOLO100 repeats,
uniform MPS/CPU40 repeats,face compatibility20 repeats,public replay4 conditions
in forward/reverse order). Start ONLY after session65810 exits. Its status file
will be /private/tmp/cbvms-correction-tail-status.json. Prepared artifact summary
script /private/tmp/cbvms-correction-summarize.py, not yet executed. Latest core
code is included in running MPS run; subsequent changes only replay/annotation,
tests and documentation. Replay failed results now remain in no-result counts.

MPS600 completed exit0, no errors, all workers stopped. JSON
/private/tmp/cbvms-correction-fresh-mps-600.json. Capture-to-assessment782samples,
p50/p95/p99=215.616/264.439/300.294ms; fresh preview28.808FPS/capture29.998FPS.
Selection0.894/1.942/2.132ms; body23.935/26.744/30.113ms; embedding
112.186/143.882/167.510ms. Actual bodymps:0/no fallback; both face ONNX sessions
CPUExecutionProvider, uniformcpu. Two tracker passage starts first-useful
420.927/551.162/562.738ms, insufficient population evidence and target not met.

ACTIVE session92196: .venv/bin/python /private/tmp/cbvms-correction-tail-runner.py
Started only after MPS exited; currently sustained operational baseline CPU.
Read /private/tmp/cbvms-correction-tail-status.json before retrying anything.
Remaining sequence and per-run outputs are defined in the runner above. It uses
isolated observation-only data and executes children sequentially. No production
writes, migrations, credentials, gallery mutations or uploads authorized/performed.

Important timing correction: MPS600 recent_timings includes warmup. Post-warmup
costs_ms has ONE first-useful sample276.221ms but passage-entry-to-first-assessment
56980.732ms. Approximately56.7s elapsed before the first usable candidate. The
router only marks first_usable after face geometry/90% visibility checks; there is
no saved footage to verify which visual condition caused that interval. Added
numeric invalid-box/visibility/empty-crop counters (no policy change) after MPS
run; baseline run does not use router. Do not hide that entry delay or call276ms
a populationp95. Final recommended settings still await all tail evidence.

Added p99 to existing device-probe percentile report (scripts/benchmark_monitor_devices.py)
and updated its existing expected-percentile test (tests/test_monitor_devices.py).
Pending final affected suite also includes tests.test_monitor_performance and
 tests.test_monitor_devices. Current baseline600 remains the only heavy operation;
no concurrent model probes have been started.

Operational CPU600 control completed exit0 in618.909s; all workers stopped/no
errors. /private/tmp/cbvms-correction-baseline-cpu-600.json. 1045assessment samples,
capture-to-assessment482.021/559.676/587.631ms; capture29.988/fresh22.608FPS.
This is not the same recorded live scene as the MPS test. Tail session92196 now
runs Front Gate60s, followed by the prepared sequential public probes/replays.

Tail session92196 completed exit0. All15 child commands passed (including Front
Gate, public YOLO200 samples/device, uniform40/device, face80/device, replay8runs).
Front Gate no usable passage; acquisition19.933FPS, sync665/133 simulated batches.
Core ML embedding executed profile kernels and was fast, but gallery compatibility
remains unverified; not enabled. Uniform MPS median did not improve; stays CPU.

Final review scoped active-ticket replacement strictly behind freshness flag.
Two physical flag-off controls rerun on final behavior: session41843 completed
exit0, both children exit0. Artifacts cbvms-correction-final-control-{cpu,mps}.json.
ACTIVE final public replay-control runner:
/private/tmp/cbvms-correction-final-replay-controls.py (four sequential runs,
CPU/MPS then MPS/CPU), exec session reported in current chat. This follows all
camera shutdowns. No camera readers active. Final broad regression then artifact
export/report/checkpoint remain. No new production behavior enabled.

## FINAL STATE — authoritative; supersedes all running/pending notes above

Branch main, base HEAD7c1680a; correction changes remain uncommitted. All task
benchmark/replay/test processes have exited. Sessions13010,65810,92196,41843,
17974,3347,39096 and52984 are complete; no task camera reader/model probe remains.
No installs, migrations, deployments, uploads or uncertain business writes.

Implemented and available validation complete. No identity cache or face-backend
rollout. Freshness remains default-off and observation-only. Production remains
operational route/bodyCPU/faceCPU/uniformCPU. BodyMPS is a measured useful optional
backend; Core ML embedding is fast but gallery compatibility remains unverified.

Final exact test command (both conditions):
.venv/bin/python -m unittest tests.test_observation_traces tests.test_assessment_candidates tests.test_person_detector_backend tests.test_camera_replay tests.test_live_pipeline tests.test_coordinated_monitor tests.test_live_state tests.test_camera_switching tests.test_recognizer_identity tests.test_student_archive tests.test_monitor_performance tests.test_monitor_devices -q
- CBVMS_TRACE=1 CBVMS_ASSESSMENT_FRESH_SELECTION=1:167passed5.362s.
- Both flags unset:167passed5.211s.
- Separate tests.test_live_persistence:8passed/1existing closed-connection fixture
  error in test_guard_is_checked_inside_the_write_transaction, not counted as pass.
- 15 affected Python files parsed; git diff --check passed.

All completed benchmark results, salted bounded traces, counts, source/model
hashes and test logs exported to docs/validation/camera_latency_correction_2026-10-28/.
Summary: docs/validation/camera_latency_correction_2026-10-28.json.
Final report: docs/CAMERA_LATENCY_CORRECTION_REPORT_2026-10-28.md.
Commands: evidence directory commands.json and report/evaluation guide. Earlier
pre-final diagnostic runs are retained. Initial sandbox abort134 was premeasurement.
Final flag-off physical controls passed: CPU p95=1445.261ms, MPS p95=1558.090ms.
Final flag-off public replay controls repeated CPU/MPS then MPS/CPU: all four exit0.
Combined freshnessMPS public replay:exit0,5results,p95=583.468ms. All camera runs
report clean shutdown; Front Gate had no usable assessment passage.

Main verdict: stale candidate selection is fixed behind a flag; sustained MPS
freshness capture-to-assessment p50/p95/p99=215.616/264.439/300.294ms. This is not
population first-useful or correct-confirmation latency. Controlled public replay
still gives operational baseline better p95 than freshness; keep default route.
One post-warmup MPS passage waited56.7s before usable evidence, then276.221ms to
assessment. Do not hide this wait or claim the500ms passage target has been met.
Labeled student accuracy, correct-confirmation, real cloud contention and a run
longer than10min remain unverified. No labeled footage/gallery paths supplied.

Modified tracked files:
core/assessment_candidates.py, core/live_pipeline.py, core/performance.py,
core/recognizer.py, core/trainer.py, ui/dashboard.py,
scripts/annotate_camera_replay.py, scripts/benchmark_camera_runtime.py,
scripts/benchmark_monitor_devices.py, scripts/replay_camera_assessment.py,
tests/test_assessment_candidates.py, tests/test_camera_replay.py,
tests/test_monitor_devices.py, docs/CAMERA_ASSESSMENT_EVALUATION.md.
New: scripts/benchmark_face_compatibility.py, tests/test_observation_traces.py,
this checkpoint, correction report, summaryJSON and validation directory.
Unrelated modules and user work preserved.

Next concrete action requires data: obtain consented recordings with entry/exit,
walking/brisk/overlap/occlusion/lighting and an authorized disposable gallery;
annotate every frame including body-visible/face-hidden entry with
.venv/bin/python scripts/annotate_camera_replay.py --video /local/authorized.mp4 --every 1 --output /local/labels.jsonl
Then run baseline/candidate/freshness replay on person/session-held-out passages
using the guide, before any production accuracy decision. Do not rerun completed
hardware probes merely because the conversation was interrupted.
