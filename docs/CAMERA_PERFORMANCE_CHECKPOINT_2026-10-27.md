# Camera performance checkpoint — 27 October 2026

Task in progress. Initial branch main, commit 05ccaca; working tree initially clean.
Recovered camera/startup/monitor, incremental sync and password recovery checkpoints.
No production DB, shared camera settings, credentials, models or thresholds changed.

Confirmed desktop: Mac16,12 MacBook Air, M4 10 CPU/10 GPU cores, 16 GiB,
macOS 26.5 (25F71), native arm64 (proc_translated=0), AC lowpowermode=0.
No recorded thermal warning; pressure probe reported 60% free.
Hardware queries require desktop execution; sandbox sysctl was denied.

Pipeline: owner-thread capture; latest sample copies; bounded tracking and assessment
workers; 30 FPS default preview, tracking offers >=120 ms, assessments >=500 ms.
No explicit 20 FPS cap found. Preserve generation tokens and optical-flow ownership.
Ruflo/ToolSearch tools unavailable. Vision-eval and verify skills loaded; direct work.

Running installed model synthetic device benchmark:
`.venv/bin/python scripts/benchmark_monitor_devices.py --output /private/tmp/cbvms-camera-devices-20261027.json --repeats 50 --timeout 120`
Tool session 63547. Log `/private/tmp/cbvms-camera-devices-20261027.log`.
Not yet accepted as sustained evidence or accuracy.

Added `scripts/camera_formats.swift` for sanitized read-only AVFoundation modes.
Next: inventory modes; implement isolated capture/preview/full monitoring measurement,
profile before changing product; run viable 10-minute conditions and focused regressions.
User confirms no labeled footage is available; accuracy improvement cannot be verified.

## Current measurements and next step

- AVFoundation inventory completed: `scripts/camera_formats.swift` output
  `/private/tmp/cbvms-camera-formats-20261027.json`. Every exposed format max 30 FPS,
  including 640x480, 1280x720, 1920x1080 and portrait/square modes.
- Device benchmark completed successfully, session 63547 exit 0. Default body
  CPU p50/p95 29.788/46.029 ms; MPS 12.869/22.185 ms (50 blank-input calls).
  CPU face detection ~70 ms; embedding ~127 ms. CoreML detector failed; embeddings
  differ from CPU. No provider changes accepted.
- First harness used nested Tk update() and did not return; native stack shows
  Tk_UpdateObjCmd/CoreGraphics drawing/color conversion. Stopped that isolated
  process with SIGINT, then corrected harness to use mainloop()/after().
  Do not use the interrupted run as throughput evidence.
- Corrected 30-second physical baseline: `/private/tmp/cbvms-full-baseline-short.json`:
  capture 29.767 FPS, fresh preview 19.2 FPS, tracking 4.167/s, assessments 1.433/s.
  Feed callback p50/p95/p99 7.629/13.492/17.890 ms; render 3.971/5.919/7.513 ms.
  Empty disposable roster; unknown-person processing only, no production writes.
- Ten-minute physical baseline currently running: session **2330**, output
  `/private/tmp/cbvms-full-baseline-600.json`; numeric progress at same stem
  `.progress.json`. Command: `.venv/bin/python scripts/benchmark_camera_runtime.py
  --mode full --seconds 600 --warmup 15 --output /private/tmp/cbvms-full-baseline-600.json`.
  Uses product modules loaded BEFORE candidate edits. Do not run competing heavy
  tests/camera readers until it finishes.

Candidate (NOT yet measured/accepted): `ui/camera_feed.py` reuses PhotoImage/paste,
updates geometry only when changed; `ui/dashboard.py` caches writer-status text.
`core/performance.py` adds bounded p99. Focused tests added in live_monitor_ui,
live_monitor_native_ui, live_dashboard and monitor_performance. Headless benchmark
PhotoImage fake updated to support paste. New runtime benchmark supports isolated
capture/preview/YOLO/full, physical/synthetic sources, baseline render ref and
real incremental worker with disposable receipts for --busy; no network sync.

Next: await baseline; run short candidate and paired synthetic baseline/candidate.
Reject/revise render change if it fails to improve measured behavior. Then run
600-second accepted full candidate and busy-sync condition, camera-only/YOLO/
preview/source-target probes, Front Gate reachability, focused regression suites.
Record actual thermal/model settings and sanitized report/rollback. Nothing committed.

## Later progress (supersedes running baseline above)

- Baseline session 2330 finished, exit 0: 600 s, capture 29.927 FPS, preview
  20.228 FPS (10-second p1 16.418), tracking 5.392/s, assessments 1.570/s.
  Queues bounded; RSS fell 813→653 MiB. Final scene included more processing;
  do not attribute its throughput decline to heat without pressure evidence.
- User confirms manually switching the short candidate window to Front Gate.
  `/private/tmp/cbvms-full-candidate-short.json` is therefore **NOT a paired USB
  comparison**. It ended at FFMPEG/HEVC 1920x1080, reported 20 FPS, measured 19 FPS.
  No preference was saved. Runtime harness now rejects unexpected source changes
  and records each opened backend without URLs.
- Paired deterministic native-Tk synthetic 720p/30 preview (30 s each): baseline
  26.067 FPS/render p50 5.259 ms; reused buffer 26.733 FPS/render p50 4.066 ms.
  Source delivered 30 FPS both. Files `/private/tmp/cbvms-synthetic-{baseline,candidate}-30.json`.
- 219 focused tests passed in 13.004 s (`/private/tmp/cbvms-camera-focused-tests.log`).
- Added steady monotonic preview deadlines, skipping missed slots instead of
  accumulating callback lateness; assessment/tracking interval guards unchanged.
  53 UI/headless/native tests passed in 6.723 s, including real Tk pixel updates,
  bounded image count, simulated camera transitions and timer regressions.
  Log `/private/tmp/cbvms-camera-ui-final-tests.log`.
- Current session **11278**: paired 30-second **full synthetic dashboard** baseline
  (render + feed callback from 05ccaca) and candidate with the steady timer.
  Outputs `/private/tmp/cbvms-synthetic-full-{baseline,candidate}-30.json`.
  Wait for results before starting any heavy tests/camera benchmarks.
- New `scripts/mac_thermal_probe.swift` reads only Foundation thermal state,
  low-power flag and active CPU count; not compiled/run yet.

Next: accept/revise steady timer from paired results; compile thermal helper;
run 600 s physical USB candidate and 600 s Front Gate busy isolated-sync candidate,
then short capture/preview/YOLO and 20/60 synthetic target probes. Full runs have
empty roster; no labeled accuracy or real enrolled-uniform throughput claim.

## Resumed 28 October (latest)

No prior benchmark process remained. Changes preserved; no commit/push.
User confirmed manual Front Gate switch and absence of labeled footage.

Completed full synthetic pair: baseline 27.667 FPS vs candidate 29.033 FPS;
median feed callback 3.002→2.157 ms, render 2.385→1.710 ms. No errors.
Model metadata artifact `/private/tmp/cbvms-model-metadata.json` verifies float32
CPU, body tensor 1×3×384×640 and classifier 1×3×224×224; ONNX float inputs.
Thermal helper compiled at `/private/tmp/cbvms-mac-thermal-probe`.

Power is now **battery, Low Power Mode enabled**, thermal nominal (12% at resume).
No power settings changed. Earlier AC and current battery runs are not controlled
A/B comparisons. User was offered plugging in; no answer received before resume.

Sync smoke initially failed: benchmark forgot migrate() caller transaction.
Fixed only benchmark with BEGIN IMMEDIATE. Retest passed, 60 accepted synthetic
operations in 12 batches; camera/tracking/assessment workers stopped.
Artifact `/private/tmp/cbvms-sync-harness-smoke-fixed.json`.

**Running session 40919** executes `/private/tmp/cbvms-sustained-runner.py`:
1. `--mode full --source usb --seconds 600 --warmup 15`, output
   `/private/tmp/cbvms-usb-candidate-600.json`.
2. Then `--mode full --source front-gate --busy --seconds 600 --warmup 15`, output
   `/private/tmp/cbvms-front-gate-busy-600.json`.
Each writes `.progress.json`; runner writes `.thermal.json` every 10 seconds.
Only temporary DBs and simulated sync receipts; no frames or business events saved.
Do not run competing camera/heavy workloads during these measurements.

Sanitized completed JSON and regression logs copied into
`docs/validation/camera_performance_2026-10-27/`. Draft report exists alongside
this checkpoint. Next: inspect both sustained results, complete short mode/target
matrix and physical switching regression; finalize report, artifacts and rollback.

### Continued after context recovery, 28 October

Front Gate busy 600s completed exit 0: capture 19.947, unique preview 19.893, preview p1 19.659, gap p99 81.559 ms; meets 20 FPS criterion for this scene. No live body/embedding work in this scene after warmup. JSON and thermal sidecars copied to validation directory. USB sustained candidate copied too.

Queued YOLO initially exited before camera startup due to a harness indentation error. Fixed and compiled; discarded failed launch. Restarted USB YOLO 600s+15s warmup in session 31245 via `/private/tmp/cbvms-yolo-sustained-runner.py`; output `/private/tmp/cbvms-usb-yolo-600.json`. Do not overlap another camera benchmark until it exits. Next: inspect sustained YOLO, short isolated mode/target matrix, physical switch check, final focused tests/report/rollback patch. Product changes unchanged.

Remaining matrix queued in session 83695 (`/private/tmp/cbvms-camera-final-matrix.py`), waiting for successful YOLO JSON. Eight 30s+5s warmup runs: USB capture30, preview30, full20; Front Gate capture30, preview30, full30; synthetic full20/full60. Then automatic 68s switch diagnostic to `/private/tmp/cbvms-camera-switch-final`. Run final unittest UI/persistence after matrix releases cameras. At YOLO ~100s power probe changed to AC but reported 5% discharging; preserve per-run thermal/low-power samples and do not call physical comparisons controlled.

USB YOLO 600s finished exit 0: capture29.982, YOLO13.337/s, low-10s p1 12.4, inference p50/p95/p99 70.361/86.35/120.236ms; nominal thermal, lowpower true, reader stopped. Artifacts copied. Session83695 now running sequential short matrix, then physical camera switch. Rollback patch generated and reverse-check passed.

Short USB full target20 produced19.067 preview, p1 18.504, gap p99 89.783ms, source still30FPS (request20 not honored by AVFoundation). Because this is a viable preview target, queued a 600s+15s full USB20 validation in session96453 (`/private/tmp/cbvms-usb20-sustained-runner.py 6720`) waiting for matrixPID6720 exit, then final UI/persistence tests. Output `/private/tmp/cbvms-usb-full20-600.json` and thermal sidecar; final tests `/private/tmp/cbvms-camera-final-tests.log`. Do not run competing load.

Resumed again: session96453 active, USB20 t526/600. Matrix stopped at synthetic60 required_model_unavailable (body/uniform failed); all workers stopped, 48.267 preview is invalid full-model comparison. Physical switch did not execute due to matrix fail-fast. Next after96453: read final tests, run physical switch separately and repeat synthetic60 diagnostic; preserve failure artifact.

### Final checkpoint — work complete, 28 October 2026

Branch main, baseline05ccaca; no commits/push/deployment. Implementation remains
8 tracked edited files plus3 new diagnostic scripts, all listed in the scoped
implementation.patch; docs and numeric evidence added. Completed USB target20
600s:18.738preview, p1 15.167, p99gap170.804ms; failed20FPS stability. All workers
stopped, required models ready. Final33tests:32pass/1preexisting closedDB fixture
error; DB/test identical to baseline. Physical switch diagnostic exit0, all
required models ready, repeated USB/FrontGate frames+analysis, all workers
stopped; optional earring absent. Synthetic60 retry exit0 at58.567preview over30s;
first failed startup retained, cause unverified. Sync contention and recorder
overhead probes completed. No benchmark remains running. Report finalized at
`docs/CAMERA_PERFORMANCE_REPORT_2026-10-27.md`, sanitized artifacts and rollback
patch in matching validation directory. Exact next step: user reviews report;
any acceleration/model policy experiment requires new controlled labeled data.
