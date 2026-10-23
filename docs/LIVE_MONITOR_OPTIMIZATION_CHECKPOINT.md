# Live monitor optimization checkpoint — 2026-10-23

Baseline commit: `244d6805ee00c5f8b3bf60f9fecf0d288dc70b7c`. Working tree was clean
before this task. No commits, production database operations, deployments, model
replacement, threshold changes, retraining, or embedding migration were performed.

## Completed implementation

- `core/monitor_models.py`, `core/model_readiness.py`, `main.py`, `ui/dashboard.py`:
  shared login/dashboard resources, two prioritized preparation slots, actual
  synthetic warm inference, failure/retry and queued-work cancellation. Active
  native calls keep ownership until they return. Classifier remains usable without
  supplementary colour reference. Repeated/concurrent bundle construction reuses
  one completely initialized owner.
- `core/live_pipeline.py`, `ui/dashboard.py`: same-frame localization handoff reuses
  the task's already owned, read-only pixels; ordinary task creation still copies.
  Empty scenes avoid irrelevant roster/term/audio-setting reads and still update
  state normally.
- `core/performance.py`, `core/diagnostics.py`, `core/model_safety.py`,
  `core/recognizer.py`, `core/person_detector.py`, `core/trainer.py`, dashboard:
  `CBVMS_PERF=1` enables fixed-name numeric timings with at most 256 recent samples
  each. Includes load/warm/inference/locks/queues/DB/render/frame age/milestones.
  No images, embeddings, student names, or student identifiers in these summaries.
- `tests/test_monitor_startup.py`, `tests/test_monitor_performance.py`: targeted
  startup, shutdown, retry, ownership, empty-scene and metric regressions.
- Added `scripts/benchmark_monitor_startup.py`,
  `scripts/benchmark_monitor_pipeline.py`, `scripts/probe_monitor_camera.py`.
- Fixed an artifact in `scripts/benchmark_live_monitor.py`: its PhotoImage Mock
  retained every PIL input in call history. A plain replacement function avoids
  this artificial memory growth. The final sustained rerun completed successfully.

## Evidence already obtained

Logs/artifacts are in `/private/tmp/`; preserve these rather than rerun everything.

- `cbvms-monitor-core-tests.log`: **123 passed**. Command:
  `.venv/bin/python -m unittest tests.test_monitor_performance tests.test_monitor_startup tests.test_startup_portal.ReadinessTests tests.test_live_dashboard tests.test_live_model_safety tests.test_recognizer_identity tests.test_live_pipeline tests.test_live_state -v`
- After the final singleton/queue changes, `cbvms-monitor-final-focused.log`:
  **20 passed with diagnostics enabled**:
  `CBVMS_PERF=1 .venv/bin/python -m unittest tests.test_monitor_startup tests.test_monitor_performance tests.test_live_pipeline.LiveWorkerTests -v`
- `cbvms-monitor-integration-tests.log`: **157 discovered, 155 passed, two errors**.
  Modules: camera_activation, camera_switching, camera_freeze_watchdog,
  network_camera_worker, camera_violation_integration, live_persistence, attendance,
  attendance_writer, attendance_pipeline, face_capture, enrollment_preview,
  enrollment_recovery, detection_audio, live_notifications, term_enrollment.
  One error was an existing bare test import; corrected invocation
  `PYTHONPATH=tests .venv/bin/python -m unittest tests.test_detection_audio -v`
  passes **30 tests** (`cbvms-monitor-audio-tests.log`). The remaining error is below.
- `cbvms-monitor-downstream-tests.log`: **62 passed**:
  `PYTHONPATH=tests .venv/bin/python -m unittest tests.test_torso_region tests.test_evidence_association.EvidenceAssociationTests tests.test_evidence_association.CaptureProvenanceTests tests.test_unknown_sightings tests.test_violation_workflow tests.test_suspensions -v`
- `cbvms-monitor-native-tests.log`: **4 passed with desktop access**:
  `.venv/bin/python -m unittest tests.test_live_monitor_native_ui tests.test_camera_transition_native_ui -v`

Three existing failures reproduced from exported original sources under
`/private/tmp/cbvms-monitor-baseline-source` (using the same venv):

1. `tests.test_startup_portal.PortalTests.test_pending_visible_only_to_authenticated_string_owner`.
2. `tests.test_startup_portal.PortalTests.test_background_refresh_sees_committed_record_without_logout`.
   Both expect IDs from `log_violation`, but these fixtures get `None`.
   Original-source log: `cbvms-monitor-existing-portal-baseline.log` (5 tests, 2 failures).
3. `tests.test_live_persistence.LivePersistenceTests.test_guard_is_checked_inside_the_write_transaction`:
   closed SQLite connection error on the violations subcase. Original-source log:
   `cbvms-monitor-existing-failures-baseline.log` (also contains an initial unrelated
   export/import error; the later portal baseline run resolves that export issue).

## Measurements and limitations

Apple M4, 16 GiB RAM, Python 3.12.13, macOS 26.5 arm64, 10 logical CPUs. Installed versions: NumPy 1.26.4,
OpenCV 4.10.0.84, ORT 1.19.2, PyTorch 2.2.2, Ultralytics 8.3.27, InsightFace 0.7.3.
Face/body/uniform weights installed; earring weights absent.

- Authoritative paired desktop results: `cbvms-monitor-baseline-desktop.json`,
  `cbvms-monitor-candidate-desktop.json`. Three fresh processes per condition,
  five-second simulated login, ten-second fixed synthetic pixel probe.
  Baseline torso ready 0.772–0.919 s after login, uniform 0.741–0.876 s; candidate
  both ready by login in all three. Warm serial model inference essentially unchanged.
- `cbvms-monitor-sustained-models.json`: 60-second installed-model synthetic run,
  192 cycles, no required-model failure. Inspect JSON for exact memory/CPU/timing.
- Early sandbox ~18 s face load is NOT representative desktop startup: import-time
  profiling found ~16 s rebuilding Matplotlib font cache because its normal path
  was unwritable. Desktop runs supersede these exploratory timings. No cache workaround
  was added to product code.
- One exploratory sandbox run, during ongoing edits, reported body/uniform failure
  without error detail. The benchmark was corrected to retain errors and fail required
  model failures; the final repeated desktop conditions succeeded.
- Two physical MacBook camera probes were explicitly authorized by the user while a
  consenting participant was present. Results: `cbvms-monitor-physical-camera.json`
  (35 s), `cbvms-monitor-physical-camera-reasons.json` (20 s). Temporary synthetic
  identity/embedding existed only in memory. No business writes/images/embeddings saved.
  First frame 1.48/1.59 s, stable identity 5.16/5.29 s including calibration, torso
  6.58/6.18 s. Actual second-probe resolution 1280×720, capture ~29.5 FPS,
  native probe preview ~14.2 FPS; capture-to-assessment p95 ~838/902 ms.
  **Uniform abstained**: second probe reports skin/visibility guard rejection in
  20 assessments. Do not weaken this guard. No general accuracy claim is justified.
- The first pipeline sustained run has an artificial PhotoImage Mock retention
  artifact; do NOT use its 1.6 GB memory result as a product leak. Corrected result
  `cbvms-monitor-pipeline-final.json/.log`: 60.02 s, 26.83 unique preview FPS,
  4.5 analyses/s, maximum queued/in-flight each one, clean stop, frame-age p95
  39.092 ms, render p95 6.898 ms. Warm RSS sampled 116.05–116.88 MB, end 112.30 MB.
  Capture-to-assessment p50/p95 217.566/234.110 ms with controlled 200 ms analysis.

## Final delivery progress

- Product diff reviewed; all 19 changed Python files compile in memory.
- Nine aggregate JSON reports, weight/source hashes and an implementation-only
  reverse patch are preserved under
  `docs/validation/live_monitor_2026-10-23/`.
- Detailed `docs/LIVE_MONITOR_OPTIMIZATION_REPORT.md` completed, including hardware,
  accuracy limits, follow-up evaluation, training scope and rollback instructions.
- Hardware helper script/tests completed by the same builder; final root refinement
  makes synchronization errors fail explicitly. **7 helper tests pass**:
  `.venv/bin/python -m unittest tests.test_monitor_devices -v`
  (`/private/tmp/cbvms-monitor-device-tests.log`).
  Initial desktop results verify M4/MPS availability, default YOLO CPU backend and
  parameters, explicit MPS success, and installed ORT CoreML/Azure/CPU providers.
  CoreML face detector fails with a static-output shape-rank error; CoreML ArcFace
  runs much faster but produces different floating-point output. No product
  provider changes were made. Do not infer GPU/ANE node execution from registration.
- Initial hardware run forced a fresh Matplotlib cache, skewing its first CPU load;
  final script removes that override and synchronizes accelerator work for timing.
  Two final sequential desktop probes completed successfully:
  `/private/tmp/cbvms-monitor-devices-final-1.json`, `...-2.json`, combined
  `/private/tmp/cbvms-monitor-devices-final.log`.
  Command per run: `.venv/bin/python scripts/benchmark_monitor_devices.py --output /private/tmp/cbvms-monitor-devices-final-N.json --repeats 25 --timeout 120`.
- Final device results: default YOLO CPU confirmed after prediction; MPS body
  p50 11.944–20.758 ms versus default CPU 25.578–26.080 ms on blank inputs, with
  longer first call. MPS uniform p50 4.491–4.747 ms versus CPU 3.984–4.148 ms.
  CoreML detector fails on both repeats. CoreML embedding p50 3.889–4.099 ms
  versus CPU 127.562–127.680 ms, but cosine vs CPU is 0.9976904745 on blank input,
  and CoreML session load is 2.86–2.92 s. Keep CPU active; no hardware policy change.
- Final strict synchronization validation ran fresh MPS trials for both modules,
  25 calls each, and passed without errors. Artifact:
  `/private/tmp/cbvms-monitor-device-sync-verification.json`.
- `git diff --check` and
  `git apply --reverse --check docs/validation/live_monitor_2026-10-23/implementation.patch`
  pass. Rollback was **not** applied. No commit/deployment was made.

## Completion and follow-ups

Authorized local startup/inference implementation, hardware evaluation and delivery
are complete. Three pre-existing test failures, missing earring weights and absent
representative labeled footage remain explicit limitations, not pending silent work.
No further camera session, product provider change or full-suite run is planned.

Keep training follow-ups separate: grouped person/session splits, untouched test
   set, immutable manifest/image hashes tied to models, honest held-out provenance,
   current/complete crops, candidate evaluation before replacement with rollback,
   module-specific preprocessing/augmentation, and separate abstention/failure counts.

Physical accuracy remains limited to one consenting participant and a temporary
reference, without labeled varied footage or verified uniform ground truth. Do not
claim unknown false-accept/reject rates, crossing/occlusion accuracy, or improved
model accuracy from these checks. No further camera session is needed to complete
the pending synthetic/provider work.
