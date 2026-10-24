# CBVMS camera-switch investigation — current checkpoint

Updated 2026-10-24 (Asia/Manila). **Incomplete: physical freeze root cause remains
unconfirmed. No application-behavior fix. Do not commit/push.**

## Current checkout and recovered evidence

- Branch `main`, HEAD `b489989` (cam boost), predecessor `244d680`. Working tree
  initially clean; no newer local commits. Remote was not refreshed this session.
- Recovered the October 18/19 conversation and all five requested checkpoints/reports.
  `d04f550` (despite its “Switch Cam Freeze fix” title) added diagnostics only.
  Earlier physical popup acceptance was never established. October 19 decoder
  corruption evidence concerns a separate, already implemented option change.
- Reviewed the requested `244d680..b489989` application diff and current camera,
  menu, readiness/locks, audio cancellation, network decoder and credential paths.
  Camera open/read/release, Keychain resolution and decoder waits are worker-owned.
  No stack/timing evidence yet attributes the reported freeze to the optimization.

## Current local changes

- `scripts/verify_camera_switch.py`: import-safe CLI; temporary DB and empty gallery;
  same shared MonitorModels warmup entry as main; simulated login Tk root destroyed
  before dashboard construction; admin/superadmin presentation selectable. Actual
  authentication is not exercised. All business persistence is suppressed, including
  unknown snapshots; installed detection/recognition still execute. Camera preferences
  remain mocked, existing credentials resolved in memory on the camera worker.
- `scripts/camera_switch_diagnostics.py`, `tests/test_camera_switch_diagnostics.py`:
  safe failure artifacts, Tk callback boundaries, generation/source/freshness checks.
  Traces enclose popup/selection, camera lifecycle, model-lock waits, GC and decoder
  cleanup. Independent watchdog is preserved, never kills the GUI. Console logging
  is restricted to milestones; detailed records stay in bounded-duration JSONL.
- Review fixed diagnostic defects: old-root nested handoff, missing callback coverage,
  undefined close-error variable, missing final writer/model cleanup, lost rapid-switch
  sequence, and invalid analysis acceptance. Expected unknown/empty-scene assessments
  count as completed analysis; `Assessment unavailable` does not.
- No product source, model, threshold, data, credentials, preference, deployment or
  synchronization changes. Builder completed initial harness; hit usage limit during
  corrections, which lead completed directly. No replacement worker.

## Completed verification (reuse; do not rerun without a reason)

Evidence: `diagnostics/camera-freeze/20261024-investigation/`.

1. `headless-baseline.txt`: **115 passed, 3.514 s**. Command:
   `.venv/bin/python -m unittest tests.test_camera_activation tests.test_camera_switching tests.test_network_capture tests.test_network_camera_worker tests.test_camera_freeze_watchdog tests.test_live_pipeline tests.test_live_state tests.test_live_model_safety tests.test_monitor_startup tests.test_monitor_performance -v`
2. `native-decoder-baseline.txt`: **6 passed, 10.520 s**, desktop execution:
   `.venv/bin/python -m unittest tests.test_camera_transition_native_ui tests.test_live_monitor_native_ui tests.test_network_capture_integration -v`
   Simulated native captures; real local MJPEG ~19 FPS, first frame 257.51 ms;
   HTTP 401 rejection, decoder cleanup, stalled native-open cancel 21.063 ms.
3. `perf-enabled.txt`: **55 passed, 1.390 s**:
   `CBVMS_PERF=1 .venv/bin/python -m unittest tests.test_camera_activation tests.test_camera_switching tests.test_live_model_safety tests.test_monitor_startup tests.test_monitor_performance tests.test_live_pipeline.LiveWorkerTests -v`
4. `native-perf-enabled.txt`: **2 passed, 5.241 s**, desktop execution:
   `CBVMS_PERF=1 .venv/bin/python -m unittest tests.test_camera_transition_native_ui tests.test_live_monitor_native_ui.DashboardNativeLifecycleTests -v`
5. Diagnostic/watchdog helpers: **8 passed** after lead corrections:
   `.venv/bin/python -m unittest tests.test_camera_switch_diagnostics tests.test_camera_freeze_watchdog -v`
   CLI help and `git diff --check` pass. No confirmed product cause to regress yet.
6. `gil-watchdog-selftest/`: disposable C function held Python's GIL via ctypes.PyDLL.
   Independent supervisor signalled stacks at 0.550 s; native sample returned 0 and
   shows hold_gil/nanosleep. Python stack identifies the held call. No camera used.
   Exploratory first run sampled library loading; corrected final run preloaded it.

## Current physical-device evidence (NOT physical dropdown acceptance)

- Automatic run command (desktop):
  `.venv/bin/python scripts/verify_camera_switch.py --output-dir diagnostics/camera-freeze/20261024-current-auto`
- Run finished cleanly in 72.35 s including startup. No watchdog captures. Maximum
  Tk heartbeat gap **227.657 ms**. MacBook delivery ~30 FPS; streaming-preview median
  26.20 FPS, peak analysis 1.87/s. Returning to MacBook produced first frames in
  **0.841–1.012 s** across three returns. Repeated/rapid requests and navigation ran.
- Front Gate delivered **no frames** in this environment; three attempts reached
  recoverable ERROR states with responsive Tk. Keychain resolution completed off Tk;
  first decoder open failed in 6.38 s. This run did not capture the public error code.
  Later diagnostic revision logs only allowlisted error codes, never URLs/messages.
- All three started network decoder processes reaped; pipe threads stopped. Final
  camera, tracking, analysis, persistence, attendance and unknown workers stopped;
  no remaining preparation threads. Summary: `automatic-summary.json`.
- This exploratory automatic run had an overstrict first-analysis diagnostic filter,
  so exact first-valid-analysis times cannot be recovered from it. Streaming metrics
  prove analysis resumed on MacBook. Filter corrected before the manual run.
- Missing optional earring weights is the expected recorded model failure; face,
  recognition, body and uniform preparation succeeded. Empty gallery does not verify
  enrolled-person identity or uniform accuracy.

## First unfinished step / running manual session

- Current desktop process is the isolated diagnostic, **tool session 68669**:
  `CBVMS_PERF=1 .venv/bin/python scripts/verify_camera_switch.py --manual --role superadmin --login-delay 0 --output-dir diagnostics/camera-freeze/20261024-manual`
- It uses zero simulated-login delay to exercise shared model preparation while the
  dashboard opens. MacBook first frame 4.105 s from launch; first valid analysis
  4.758 s. These are instrumented observations, not a performance comparison.
- Output: `diagnostics/camera-freeze/20261024-manual/`; console:
  `/private/tmp/cbvms-camera-switch-manual.log`. Manual window lasts up to 600 s.
- User has been asked to physically choose Source → Front Gate after moving MacBook
  preview and nonzero Analysis, leave any persistent freeze untouched for 25 s,
  then return to MacBook if responsive; report menu/clock/controls/video/analysis.
  **No user physical-interaction report received at checkpoint time.**
- Read new timings/watchdog/stacks/native samples before any fix. Distinguish a held
  native popup from persistent freeze after dismissal. Do not kill before capture.
  If the window expired, rerun `.venv/bin/python scripts/verify_camera_switch.py --manual`
  (new unique output folder), with other camera readers closed.

## Remaining acceptance / rollback

Actual mouse selection, successful Front Gate preview/analysis, warmup-time/rapid
physical changes, navigation/logout/close during real pending/reconnect, enrolled
recognition and absence of stale side effects remain unverified. Controlled tests
cover many lifecycle guards; they are not substitute physical evidence. Unreachable
Front Gate prevents successful round-trip acceptance until connectivity is restored.
Do not change its saved network configuration or credentials to force a connection.

Product startup/steady-state performance is unchanged because no product code changed.
Diagnostic logging adds overhead; measurements above are not an optimization benchmark.
Rollback only this diagnostic update after preserving later edits: restore
`scripts/verify_camera_switch.py` from `b489989`, remove the two newly added helper/test
files if no later work depends on them; retain evidence/checkpoints. Do not roll back
cam boost or the previous decoder fix. Nothing has been committed or deployed.

---

## Historical checkpoint (2026-10-19; retained below)

# CBVMS physical camera-dropdown freeze checkpoint

Updated 2026-10-19 (Asia/Manila). Task incomplete. Do not commit/push.

## Recovered state and diagnosis

- Initial working tree clean; HEAD `2535189908f2c633ddf4cdd8a7ddb137442171e5`.
  Read-only `git ls-remote origin HEAD refs/heads/main` confirms both remote refs
  match. No reset, merge, stash modification, or application-data changes.
- Reviewed camera configuration, dashboard lifecycle, rendering/window teardown,
  capture owner, isolated decoder/pipe cleanup, Keychain resolution, camera API/store,
  IP stream entry points, existing native transition test and diagnostic script.
- Actual physical dropdown freeze has NOT been reproduced in this resumed session.
  No desktop-control tool is available. User screenshot shows the menu, but cannot
  establish the blocked operation. No new physical-interaction report received yet.
- Recovered `/private/tmp/cbvms-switch-audit/` automated hardware evidence:
  final max heartbeat gap 525.3966 ms, updated 426.9310 ms, resolved baseline
  407.0046 ms. Zero hang events; all four recovered stack files empty. No native
  process sample of the reported freeze exists in those runs.
- Therefore root cause remains UNCONFIRMED. Do not attribute it to network timeout,
  menu teardown, GC/Tk finalization, inference, or a lock without the missing stacks.
- Existing application callback uses after(1); camera open/read/release and Keychain
  work already run off Tk. Native CTk popup and pre-command widget updates were not
  enclosed by the previous traces. Native post can pause heartbeat while held open;
  persistence after selection/dismissal must be distinguished from normal menu use.
- The old diagnostic watchdog was a Python thread and immediately os._exit'ed after
  dumping stacks. It could miss a GIL-held native stall and collected no native sample.

## Changes in this session

- `scripts/verify_camera_switch.py`: diagnostic-only instrumentation, durable unique
  output directory; UTC timestamps; native popup/CTk callback traces, generation,
  device-lock waits, credential timing, camera/process open/release, state/rate and
  worker-cleanup evidence. Starts an independent supervisor and signal stack dump.
  Manual responsive session lasts ten minutes. Saved preferences remain mocked;
  database is disposable; installed models still load. No production gallery copied.
- `scripts/camera_freeze_watchdog.py`: separate process, five-second heartbeat-stall
  threshold, up to three Python/native captures separated by at least 15 seconds;
  15-minute supervisor limit. Native sample retains call graph only; no process
  header/arguments/environment/binary image paths, locals, pixels, or URLs logged.
  Does not kill the GUI. Exits after parent exit or finished marker.
- `tests/test_camera_freeze_watchdog.py`: stack-output filtering and stalled-heartbeat
  signal/sample regression tests.
- `LIVE_MONITOR_RELIABILITY.md`: clarifies historical claims/status, new manual command,
  diagnostic limits and remaining physical acceptance.
- `diagnostics/camera-freeze/`: sanitized recovered summary, test results and isolated
  supervisor self-test evidence. These are NOT evidence of a camera freeze.
- No application-behavior fix implemented: missing physical evidence precludes an
  evidence-based root-cause change. No model, threshold, credentials, settings,
  enrollment, production DB, or disciplinary behavior modified.

## Completed verification (resume)

1. `.venv/bin/python -m unittest tests.test_camera_activation tests.test_camera_switching tests.test_network_capture tests.test_live_state -v`
   **64 tests passed in 1.399 seconds**. Synthetic/backend lifecycle regressions,
   not physical UI evidence. Full transient console: `/tmp/cbvms-resume-focused.log`.
2. `.venv/bin/python -m unittest tests.test_camera_freeze_watchdog -v`
   **2 tests passed in 0.002 seconds** (initial run).
3. Independent supervisor self-test: disposable Python process deliberately stopped
   heartbeat while sleeping. Detected 0.491-second gap at test-only 0.3-second
   threshold; Python stacks captured; `/usr/bin/sample` returned 0 and native call
   graph captured. Desktop-enabled execution required. First sandbox attempt returned
   255; a repeat initially used stale self-test heartbeat, corrected by deleting that
   test heartbeat before launching. Final files represent the successful fresh run.
   This verifies diagnostic collection ONLY, not GIL-deadlock or camera behavior.
4. Python compilation and `git diff --check` passed.

## Durable sanitized evidence

- `diagnostics/camera-freeze/recovered-summary.json`
- `diagnostics/camera-freeze/regression-results.txt`
- `diagnostics/camera-freeze/supervisor-selftest/watchdog.jsonl`
- `diagnostics/camera-freeze/supervisor-selftest/stacks.txt`
- `diagnostics/camera-freeze/supervisor-selftest/native-sample-1.txt`
- Each new manual run: `diagnostics/camera-freeze/<UTC timestamp>/` (printed at launch).
  The self-test's heartbeat file is not a live application session.

## First unfinished step / exact next commands

User has been asked via chat to close other camera readers and run from repo root:

```sh
.venv/bin/python scripts/verify_camera_switch.py --manual
```

Wait for moving MacBook preview AND nonzero Analysis. Physically open Source and
click front gate. Do not invoke _dropdown_callback programmatically. If frozen,
leave untouched 25 seconds; then report printed output directory, whether menu
closed, and whether clock/controls/video stopped. Force Quit the diagnostic Python
process after capture if needed. If responsive, physically return to MacBook.
The diagnostic has an empty enrollment gallery: model processing is active, but
identity accuracy and enrolled-person recognition are not verified.

Next agent: inspect that run's timings.jsonl, watchdog.jsonl, stacks.txt and
native-sample-*.txt. Correlate last entered/unreturned menu and camera callbacks,
generation, lock waits, first frame, event-loop gaps and CPU. Determine whether the
stall precedes application selection, occurs in Tk/native menu work, a worker's
Tk finalizer, camera ownership, or model/cleanup locking. Do not infer from this
list. Apply smallest supported fix, add a focused root-cause regression, then test.

Remaining physical acceptance: both source directions; repeated switching with
inference; rapid supersession; isolated unreachable/auth-rejected source; navigation
during connect; close during connect/reconnect; fresh-frame source attribution,
stale-result rejection, owner/decoder cleanup and analysis continuing after switch.
Existing callback/mock/local-stream tests do NOT satisfy physical acceptance.
Do not rerun old full suites merely to substitute for this missing evidence.
