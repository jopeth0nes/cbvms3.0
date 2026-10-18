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
