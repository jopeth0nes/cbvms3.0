# Two-camera monitor workspace — first implementation

Based on `fad3e6e` (alerts update). This implements a primary analyzed stream plus
one companion **preview-only** stream, not two simultaneous analysis pipelines.

## Use

Launch CBVMS normally in the existing environment. In Live Monitor:

1. Select the analyzed camera with the existing Source selector.
2. Choose Split to show the second camera alongside it.
3. Choose the other saved source under Second camera (Front Gate or MacBook).
4. Focus shows the analyzed camera; Other camera shows the companion without
   changing which camera is analyzed.
5. Pop out preview opens a movable window using the same companion capture.
   F11 toggles full screen there; Escape exits full screen. Closing that window
   leaves Split preview running if it is still visible.
6. Retry preview explicitly retries unavailable sources.

The primary uses the existing observation-only/production policy. The companion
never invokes recognition, attendance, discipline, evidence storage or alerts.
The existing activity sidebar describes the primary monitoring pipeline only.
No passwords, addresses or camera settings are embedded in this feature.

Tabs do not restart the primary camera. Focus releases an unused companion;
Split and Other camera share it, as does the pop-out window. Changing the primary
source waits for the companion to release before acquiring the source. Companion
capture open/read/release are owned by one worker; Tk polls only the latest sample.
Preview refresh is capped at 20 UI updates/s; it does not promise that physical FPS.

Navigation outside Live Monitor closes the companion window and stops its preview,
including before Settings can scan cameras. The primary retains its prior lifecycle.
Logout cancels the companion and participates in the dashboard shutdown deadline.

## Validation

Command:

```
python -m unittest tests.test_preview_session tests.test_monitor_workspace tests.test_live_dashboard tests.test_camera_switching tests.test_live_monitor_ui -q
```

86 tests passed. Python compilation and git diff whitespace checks passed.

Native suite attempted: `python -m unittest tests.test_live_monitor_native_ui -q`.
Six tests could not initialize Tk because this environment has no DISPLAY. These
are not passing native checks. No MacBook, physical IP camera, second screen or
multi-camera throughput was accessible here.

## Required MacBook checks

Use the existing disposable test setup or observation-only mode for initial checks.
Do not assume a normal launch disables production writes.

- Front Gate primary + MacBook companion; reverse the sources and repeat.
- Switch Focus/Split/Other camera repeatedly. Verify labels and no wrong-source frames.
- Open pop-out alongside Split: confirm it shows the same companion without another
  camera connection; move it to another screen and test F11/Escape.
- Change the primary to the former companion; verify release before acquisition.
- Disconnect/reconnect the IP camera, then Retry preview. Confirm no UI freezing.
- Navigate to Settings/enrollment and back. Verify companion release and no popup leakage.
- Logout/close while connecting and streaming. Verify all capture workers stop.
- Verify thumbnails, buttons and mirror overlay at the actual available window width.
- Measure primary inference/preview and CPU/memory with companion stopped vs active.

## Scope and rollback

Saved layouts, arbitrary grids, detachable analyzed views and simultaneous analysis
of both sources are not implemented in this first phase. Do not describe this as a
complete multi-camera analytics release. New UI tabs are view presets, not arbitrary
workspace tabs. Streams are not globally shared with separate enrollment tools;
the companion releases on navigation and respects the existing USB lock.

To disable extra capture immediately select Focus and close the pop-out window.
To undo this feature, revert its commit on a clean branch while preserving later
work. No database migration, weights, dependency pins or camera settings changed.

## Local integration — 29 October 2026

Integrated on `integration/monitor-workspace-20261029` from a clean `fad3e6e`.
`git apply --check` passed before application. No merge or push was performed.
The 86-test result above describes patch preparation; local evidence follows.

Integration corrections:

- Cancel the companion and close its window immediately on navigation, before
  constructing another panel; capture release remains asynchronous under the USB lock.
- Include the selected source in the pop-out title and keep **Preview only** visible
  during connection and failure as well as streaming. The primary explicitly says
  **Analysis · observation only** in that mode.
- Allow camera canvases to shrink and give Split tiles equal column allocation.
- Extend the ownership regression check to require both old primary and companion
  completion before starting the new primary.

Local verification on Apple M4, macOS 26.5, repository `.venv`:

```sh
.venv/bin/python -m unittest tests.test_preview_session tests.test_monitor_workspace tests.test_live_dashboard tests.test_camera_switching tests.test_live_monitor_ui tests.test_live_pipeline tests.test_live_persistence -q
.venv/bin/python -m unittest tests.test_monitor_workspace_native_ui tests.test_live_monitor_native_ui -q
```

127 focused tests and 7 native Tk tests passed. Native checks use synthetic frames
and disposable databases. They cover thumbnail/card sizing, workspace tile bounds,
mirror placement/visibility, source/preview labels, and dashboard lifecycle. A
pre-existing CustomTkinter warning about PhotoImage HighDPI scaling remains.

Separately, physical Front Gate and MacBook capture succeeded at 1920×1080 and
1280×720 respectively. The real dashboard was exercised at 1280×800 with real
primary model workers, an empty temporary database, observation-only mode, and
camera-preference saving suppressed. Both primary/companion arrangements worked.
Focus/Split/Other camera, pop-out reuse of the same session, source reversal,
explicit retry, Settings/enrollment and return to Live, and logout passed. No Tk
callback errors occurred. Primary, companion, tracking and analysis workers stopped.
Attendance, attendance events/receipts, and violations remained at zero. Companion
code has no recognizer, persistence or notification entry point. This was not a
registered-student accuracy or production-alert comparison.

Short performance observations (2-second settling, 8-second sample per condition):

| Primary | View | Dashboard preview FPS | Analysis /s | Process CPU % | RSS MB |
|---|---|---:|---:|---:|---:|
| Front Gate | Focus | 19.9 | 1.9 | 114.1 | 1223.6 |
| Front Gate | Split | 19.6 | 1.8 | 127.4 | 993.0 |
| MacBook | Focus | 29.8 | 1.9 | 871.5 | 867.4 |
| MacBook | Split | 29.3 | 1.9 | 864.8 | 853.7 |

CPU is psutil process CPU, where multiple cores can exceed 100%; camera decoder
child CPU is not included. These sequential, brief samples include changing scene
and model/cache conditions, so RSS/CPU differences are not causal estimates.
The final dashboard counters above are not the harness polling rate. This is not
a sustained throughput, thermal, model-accuracy or regression benchmark. Model
weights, thresholds and inference frequency were not changed. Aggregate results
and the one-off harness are retained locally under `output/monitor_workspace/`.

Additional real-stream checks terminated only the test-owned Front Gate decoder,
then verified disconnect status, stale-sample suppression, reconnect in a new
session, and cancellation during connection. This simulates decoder loss; no
physical cable was unplugged and no network settings were changed.

Remaining manual acceptance: physical network outage/recovery, moving the detached
window to a second display, visual full-screen transition inspection, and sustained
performance with representative registered students. F11/Escape events were sent
in the harness, but visual full-screen transitions were not independently observed.
The desktop inspection service timed out; native geometry assertions are not a
visual review of the full dashboard at every display scale. Saved workspaces,
arbitrary grids and simultaneous two-camera analysis remain out of scope.

### Launch for review

From Terminal on this Mac:

```sh
cd /Users/jopethones/Documents/GitHub/cbvms3.0
CBVMS_ASSESSMENT_OBSERVATION_ONLY=1 .venv/bin/python main.py
```

Sign in normally, open Live Monitor, select Front Gate as Source and Split, then
select MacBook under Second camera. Reverse the primary source to test the other
arrangement. Confirm the primary observation-only label and companion preview-only
label. Focus plus closing the pop-out stops the companion.

This normal launch uses the existing application database/account and startup
services; observation-only disables live assessment persistence, not all unrelated
application writes. The automated verification above used a disposable database.
For the isolated native tests, use the second test command above. Keep this branch
for review; do not merge or push until accepted.
