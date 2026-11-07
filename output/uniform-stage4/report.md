# Stage 4 verification and latency report

Software status: **verified** for explicit observation-only candidate integration. Real passage latency, garment accuracy, policy correctness and activation eligibility remain **blocked by missing reviewed data/footage**. No model was trained or activated. No physical camera was opened.

Location: `/Users/jopethones/Documents/GitHub/cbvms3.0`; branch `main`; HEAD `ea997f42d5b75b12e58787f1b2691461256a5589`. All existing edits retained.

## Implementation

- `core/uniform_observation.py`: pinned config/bundle, bounded evidence scoped by camera/session/generation/track, geometry/quality gates, fresh retry, valid short-lived cache, cancellation and reassignment invalidation.
- `core/live_pipeline.py`, `core/live_state.py`, `ui/dashboard.py`, `ui/live_alerts.py`: existing tracker/router/worker/render handoff, separate garment outcomes and one logical passage, explicit observation status, no live persistence/audio. Production default path and preview-only camera ownership retained.
- `core/uniform_model_registry.py`: explicit CPU/MPS candidate inference with synchronized preprocessing/inference measurements; same 224px preprocessing/mapping.
- `core/uniform_latency.py`, replay extension and two benchmark/smoke CLIs: actual sample distributions, missed/unassessed cases, missing ground truth, backend placement and condition reporting.
- [Observation setup/manual checks](../../docs/UNIFORM_OBSERVATION_WORKFLOW.md). Config is opt-in; no environment setting or production pointer was enabled by this task.

## Real component execution on installed public images

Apple M4 / Mac16,12, 10 CPU cores, macOS 26.5, Python 3.12.13, Torch 2.2.2, Ultralytics 8.3.27, OpenCV 4.10.0. Sequential CPU then MPS; same installed `bus.jpg`/`zidane.jpg` pixels and local weights, 4 warmups per stage, 30 measured calls per stage/backend. These full public images are timing inputs, not garment evaluation labels. Input hashes and dimensions are in `backend-benchmark.json`.

Actual model parameter placement: CPU=`cpu`, MPS=`mps:0`; no fallback reported. Measured Torch thread count was 2 on both. OpenCV was requested at 2 but reported 10 on both; this is not a claim that its native thread count was capped at 2. One initial body warmup reset Torch to 8; measured calls restored 2. MPS timings synchronize before/after the relevant work. Body split wrappers add instrumentation overhead. Face inference remains the existing ONNX CPU path and was not benchmarked or changed.

Values below are **median / p95 milliseconds** (30 samples each).

| Stage | CPU | MPS |
|---|---:|---:|
| Candidate preprocessing + transfer | 4.048 / 4.160 | 4.300 / 4.558 |
| Candidate inference + readback | 2.458 / 2.621 | 3.682 / 6.080 |
| Candidate total | 6.483 / 6.704 | 8.030 / 10.991 |
| Body preprocessing | 0.579 / 0.765 | 1.280 / 1.643 |
| Body inference | 23.514 / 27.042 | 10.880 / 20.893 |
| Body postprocessing | 0.221 / 0.281 | 3.652 / 4.789 |
| Body total detector call | 24.721 / 28.670 | 17.033 / 28.166 |

Cold-load results are retained in JSON but are order-sensitive (CPU first includes initial imports); they are not a fair CPU/MPS startup comparison. No smaller input size or production backend change was adopted.

## Synthetic passage integration

10 simulated passages per backend, three 640×480 frames each, same deterministic pixels, full-frame configured zone, scripted face/body detections with actual tracking/association/router/analysis worker and real candidate inference. Two passages per backend have only blurred/unusable torsos; eight have usable torso frames. Four classifier warmups, explicit smoke confidence 0.8 (not a recommended operating threshold), 224px model input, 35 ms waits between fixture frames. Torch=2, OpenCV-reported=10 on both; PyTorch MPS operator-fallback environment unset. No camera or database opened; one-slot worker queue and clean shutdown verified.

Both backends: **10 passages, 0 missed, 10 unassessed, 0 recognized**, 16 fresh inference attempts. No garment/policy ground truth was supplied. A fast inability-to-assess result is not successful recognition. Real passage count is **0**.

Values below are **median / p95 milliseconds**.

| Fixture metric | Samples per backend | CPU | MPS |
|---|---:|---:|---:|
| Zone entry → first decision (unable) | 10 | 0.353 / 0.666 | 1.601 / 2.279 |
| First usable torso → first decision (unable) | 8 | 3.628 / 3.899 | 15.705 / 18.702 |
| First usable torso → recognition | 0 | unavailable | unavailable |
| Classifier preprocessing | 16 | 0.140 / 0.166 | 1.600 / 1.973 |
| Classifier inference | 16 | 2.637 / 2.763 | 10.346 / 12.502 |
| Assessment worker queue delay | 16 | 0.022 / 0.026 | 0.058 / 0.098 |
| Garment assessment duration | 16 | 2.806 / 2.967 | 12.284 / 14.222 |
| Captured frame → decision | 16 | 3.596 / 3.829 | 15.514 / 18.496 |

**These are synthetic plumbing timings, not physical-camera passage measurements.** Localization/face models are fixture doubles here; actual body-model timings are measured separately above. The 500 ms initial target remains unverified for real recognized passages. Rendering is outside the analysis measurements. Ordinary OS activity/thermal state was not controlled. MPS is not assumed faster for every stage.

Initial artifacts are retained as `*-initial.*`; the intermediate smoke with mismatched reported OpenCV settings is retained as `passage-smoke-thread-mismatch.*` and is not used as a controlled comparison. The final JSON reports and process receipts identify all exact commands/start times. Two stale explanatory strings in the final raw report were corrected to describe the measured split instrumentation; no timing values were edited.

## Verification evidence

Commands run from repository root; overlapping suites below are not a count of unique tests.

- `PYTHONPATH=tests:. .venv/bin/python -m unittest tests.test_uniform_observation tests.test_uniform_models_integration tests.test_live_pipeline tests.test_live_state tests.test_assessment_candidates tests.test_observation_traces tests.test_live_persistence tests.test_camera_replay -v`: **122 passed**, `core-regressions-verified.log`. Initial fixture/path and failure-callback issues are retained in earlier logs.
- `PYTHONPATH=tests:. .venv/bin/python -m unittest tests.test_uniform_latency tests.test_live_dashboard tests.test_live_notifications tests.test_monitor_session_activity tests.test_camera_switching tests.test_coordinated_monitor tests.test_monitor_workspace tests.test_monitor_performance -v`: **84 passed**, `ui-camera-regressions.log`.
- `PYTHONPATH=tests:. .venv/bin/python -m unittest tests.test_uniform_observation tests.test_uniform_latency tests.test_uniform_models_integration tests.test_live_pipeline tests.test_assessment_candidates tests.test_camera_replay -v`: **88 passed**, `final-safety-tests.log`, including two people/reordered recognition.
- `.venv/bin/python -m unittest tests.test_uniform_observation tests.test_uniform_latency -v`: **20 passed**, `final-evidence-tests.log`, covering the final cancellation/cross-passage/out-of-order guards.
- `.venv/bin/python -m unittest tests.test_uniform_predictor_backends -v`: **2 passed**, `predictor-contract-tests.log` (RGB/float/224/no flip/stable classes; unsupported/unavailable device fails before weights).
- `.venv/bin/python -m unittest tests.test_uniform_backend_benchmark -v`: **9 passed**, sole builder evidence `backend-benchmark-worker.md`. Final lead change only corrected two report description strings; instrumentation unchanged.
- `.venv/bin/python -m unittest tests.test_uniform_observation_ui tests.test_live_monitor_native_ui tests.test_monitor_workspace_native_ui tests.test_camera_transition_native_ui tests.test_scrolling -v`: **21 native macOS Tk tests passed**, `native-ui.log`. Synthetic rows/camera doubles, no physical camera or hand gestures.
- Replay metrics tests: **13 passed**, `replay-metrics-tests.log`; CLI help and all changed Python compilation passed. Real-footage replay extension is not physically evaluated.
- `.venv/bin/python output/uniform-stage4/verify_preservation.py`: **passed**, `preservation-results.json`; `git diff --check` and `git apply --reverse --check output/uniform-stage4/stage4.patch` passed (rollback was not applied).

## Preserved versions / remaining blockers

- Original protected files: 1,772 unchanged; active artifacts: 7 unchanged. The 18 scrolling files outside intentional integration touchpoints and 15 unaffected Stage 3 source files also match their saved hashes.
- Active bundle remains `legacy-9cfbcb7cb159ea7de3d8afa24eba01268b29a8252a311615a185149868a5609b`; no `data/uniform_models/active.json` exists.
- Candidate: `data/uniform_candidate_smoke/645a629ef7034ed8ae06d3a4743ba75d/candidates/ffe234076d3d428eb28c332838b9e5fd`; SHA256 `4eda5fd372adbd80749553f849bc155a3fc4873fa2b660774d413a26bdaaf65a`. Mapping `uniform-garments-v1`, preprocessing `trainer-letterbox-rgb-0-1-v1`, dataset snapshot `5027a6487c75990147b895c094f619f0e820c54acb82b79c3f2f501c4e23439f`. Synthetic weights unchanged.
- Real catalog SHA256 `1e96842d40ab26b65d84650672ae1cb93c6512a2864d745d0e27f0055d205d8d`: 963 pending, 0 approved, 0 excluded. No real candidate or independent evaluation set. Original ZIPs/datasets untouched.
- Still missing: reviewed real training/comparison data, independent labeled passage footage with zone-entry/usable-torso timestamps and camera/lighting coverage, baseline provenance, approved confidence/acceptance criteria, department/day/schedule permission configuration, and the earlier logo-reference path. No labels, negatives, policy outcomes or measurements were fabricated.
- No physical-camera passage tests or physical two-finger gestures were performed. Camera switching/pop-out/current-session alert behavior has software/native fixture evidence and still needs manual use with consented subjects. No commit, push, activation or production-record writes.

## Exact next action / manual checks

1. Follow `docs/UNIFORM_OBSERVATION_WORKFLOW.md` to review an explicit observation config; confirm candidate/backend/synthetic warning and unchanged production activation.
2. Review real dataset crops/labels/associations before approving them; obtain a real candidate through the already implemented approved-data workflow. Supply consented independent passage footage/annotations; then use the documented replay commands on identical CPU/MPS inputs.
3. In an explicitly observation-only manual session, check obscured→clear evidence, departure/re-entry, source switching, preview-only pop-out, scrolling and one logical passage update. Confirm no logs are saved. Do not use the synthetic model to accept accuracy or activate a model.

For Stage 4 code rollback only, stop the observation app/jobs, review later edits, then check `git apply --reverse --check output/uniform-stage4/stage4.patch`. Apply the reverse patch only if rollback is intended and the check succeeds. Otherwise reverse Stage 4 hunks manually using `before/`. Keep checkpoint/audit artifacts, original data, all bundles and previous stages.
