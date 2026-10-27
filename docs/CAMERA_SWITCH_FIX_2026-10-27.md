# Camera selection freeze investigation — 2026-10-27

The Live Monitor source chooser now uses an in-window list instead of macOS's
native menu tracking loop. Real desktop keyboard selection of **front gate**
displayed its video and ran the normal tracking/assessment pipeline. Switching
back to the MacBook camera, selecting front gate again, and closing the workspace
with the chooser open all completed without a watchdog hang. Changes are local
and uncommitted.

## Address and stream verification

The saved front-gate record and its existing credential-resolved stream both
point to **192.168.1.108**, not the requested **192.168.1.10**. Bounded TCP checks
found `.108` accepting ports 554 and 80, while `.10` refused both. Camera addresses,
credentials and saved preferences were not changed; this fix does not make `.10`
serve video. The address clarification remains unanswered.

An isolated real-stream check opened the saved RTSP/TCP source in 2.110 seconds,
reported 1920×1080 HEVC at a nominal 20 FPS, delivered 94 frames in a five-second
window (reported rolling rate about 18 FPS), and reaped its decoder cleanly.
Credentials were resolved with the existing worker-only mechanism and were never
printed, put in process arguments, or written to the validation artifacts.

## What was established

Camera connection, reads and release already ran on a worker, and network decoding
already ran in an isolated process. The old source chooser still called
`CTkOptionMenu` → `DropdownMenu.open()` → `tkinter.Menu.post()`. On this Mac,
`post()` enters a nested native menu tracking loop rather than returning to the
ordinary callback immediately. Deferring camera connection by one millisecond
does not remove that nested loop.

A small reproduction with synthetic camera names entered `post()` at 0.611 s.
A scheduled selection callback ran at 1.611 s, but scheduled invoke/unpost and root
destruction did not unwind the native menu. A six-second traceback remained in
`Menu.post`; the enclosing process exceeded its 12-second deadline. Heartbeat
timers ran while the menu was posted, so this is specifically evidence of the
native modal/teardown boundary. It is **not** proof that every physical menu
selection freezes, nor a captured stack from the user's original freeze. Mouse
automation failed with `noWindowsAvailable`, and the user could not interact at
that time. The exact original physical-mouse hang remains unverified.

The baseline full-dashboard test that called the selection callback without
opening the native menu successfully switched between the real cameras. This
explains why prior callback-only regression checks could pass without covering
the interaction reported by the user. It also establishes that this camera was
reachable independently of the selector replacement.

## Implementation

- `ui/camera_source_selector.py`: an ordinary Tk list inside the existing workspace,
  styled by a CustomTkinter button. Opening it returns immediately. There is no
  native Menu, extra window, grab, wait loop or network call. It supports mouse
  selection, Up/Down/Return/Space, Escape/outside-click dismissal, scrolling for
  longer source lists, resize dismissal and lifecycle cleanup. Selection closes
  the chooser before invoking the existing asynchronous dashboard callback.
- `ui/dashboard.py`: use the new selector and its source-list update method.
  Existing source identity, cancellation, generation checks, first-frame activation,
  worker ownership, retry logic and evidence rules remain in place. The unrelated
  pre-existing sidebar/minimum-height edits were preserved.
- `tests/test_camera_source_selector_native.py`: five native regressions cover
  heartbeat while open, root destruction while open, selection/dismissal, actual
  arrow/Return event dispatch, repeated lifecycle cleanup and callback order.
- `tests/test_camera_transition_native_ui.py`: exercise opening the chooser before
  the existing delayed-I/O switch/navigation test.
- `tests/test_camera_switching.py`: adapt the source-discovery fixture to the new
  widget API; retain its original discovered-camera and selected-label assertions.
- `scripts/verify_camera_switch.py`: scheduled checks now open the chooser before
  selection, and diagnostics trace its open/select/close calls. Manual mode remains
  available for desktop input.

Actual desktop keyboard verification caught an initial arrow-key issue in the
replacement: the list's active row and selected row could diverge. Explicit arrow
bindings now move both, and a Tk event-dispatch regression plus a fresh desktop
run confirmed the highlighted front-gate row is selected correctly.

## Real-camera results

Validation ran on the existing Apple M4/macOS/Python virtual environment, using
the installed models and real cameras. The dashboard diagnostic uses a disposable
database, an empty enrolled gallery and suppressed business writers. It does not
start cloud sync or persist source preferences. These are delivery/latency checks,
not enrolled-person accuracy or disciplinary-record checks.

The updated scheduled run lasted about 72 seconds including startup/cleanup and
covered repeated USB/IP switching, rapid opposing selections, and navigation
during a pending selection. Three established front-gate sessions reached:

- First displayed frame after selection: **2.098 / 2.166 / 2.442 seconds**.
- First current, valid analysis: **2.249 / 2.308 / 2.578 seconds**.
- Median front-gate preview rate per session: **19.49 / 19.28 / 19.64 FPS**.
- Median tracking: approximately **7.1–7.3/s**; assessment: **1.77–1.83/s**.
- No independent watchdog capture. Maximum observed heartbeat gap across startup,
  navigation and switching was **642 ms**. This is not a zero-stall guarantee;
  synchronous layout callbacks elsewhere in the dashboard still account for
  subsecond pauses. It is not claimed as a controlled speedup versus baseline.
- Every established network decoder was reaped and its pipe threads stopped.
  Camera, tracking, analysis and persistence-related workers completed cleanup.

The final **superadmin** diagnostic was operated with actual desktop keys:
Tab/Return to open, Down/Return to select front gate, then Up/Return to return to
MacBook, and Down/Return to select front gate again. Video was visibly inspected
in the dashboard, including moving people and detection/assessment overlays.
The chooser was left open while the preview and clock continued updating, then
the native window close control was used. Cleanup completed without a hang.

In that run the two front-gate selections reached video in **2.536 / 2.071 s**
and valid analysis in **2.681 / 2.524 s**. Source callbacks returned in
**0.045–0.110 ms**; chooser selection returned in **1.755–2.217 ms**. Opening the
chooser returned in **0.822–1.202 ms** for those three selections. Native mouse
selection was not independently exercised; its release handler is covered by the
native widget tests.

Both camera diagnostics continued to report the already-missing optional earring
weights. No other model-readiness or callback failure occurred. The empty-gallery
diagnostic cannot establish registered-person recognition or uniform accuracy.
No screenshots, face images, embeddings, attendance, sightings or violations were
saved by the diagnostic; the computer-use tool inspected the live display only.

Aggregate milestones, callback timings and cleanup evidence:
[`results.json`](validation/camera_switch_2026-10-27/results.json).
The minimal reproducer and its limits are preserved alongside that file.

## Verification

All **86 tests** passed in the final affected runs:

```sh
# 74 passed
.venv/bin/python -m unittest tests.test_camera_switching tests.test_camera_activation tests.test_camera_violation_integration tests.test_network_capture tests.test_network_camera_worker tests.test_camera_switch_diagnostics tests.test_camera_freeze_watchdog tests.test_live_dashboard -v

# 10 passed; native desktop session required
.venv/bin/python -m unittest tests.test_camera_source_selector_native tests.test_camera_transition_native_ui tests.test_live_monitor_native_ui tests.test_dashboard_sidebar -v

# 2 passed; real decoder against synthetic loopback network fixtures
.venv/bin/python -m unittest tests.test_network_capture_integration -v
```

The decoder checks include successful MJPEG delivery, rejected authentication,
bounded cancellation of a stalled native open, parent-EOF termination and cleanup.
Native cancellation completed in approximately 32 ms in that run. No assertions
were weakened. During development the tests caught and resolved a Tk inherited
method-name conflict, a renamed callback, and the keyboard-selection issue noted
above. Final test summaries are preserved in the validation directory.

Full real-camera diagnostic commands:

```sh
.venv/bin/python scripts/verify_camera_switch.py --duration 68 --output-dir /private/tmp/cbvms-camera-20261027-auto-after
.venv/bin/python scripts/verify_camera_switch.py --manual --role superadmin --duration 120 --output-dir /private/tmp/cbvms-camera-20261027-final-keyboard
```

Use a new output directory for a new run. Manual mode needs desktop interaction;
automated mode opens/selects the chooser on scheduled callbacks. Neither claims to
reproduce every physical mouse interaction. No additional camera stream tuning,
model/provider changes, database changes, deployments or commits were made.

## Rollback

Close the application and preserve any later edits. The implementation-only patch
excludes the unrelated edits present when this task began, including the sidebar
changes in `ui/dashboard.py`. Check before applying its reverse:

```sh
git apply --reverse --check docs/validation/camera_switch_2026-10-27/implementation.patch
git apply --reverse docs/validation/camera_switch_2026-10-27/implementation.patch
```

If later edits prevent the check, reconcile the affected hunks instead of resetting
the working tree. Rollback was checked but not executed. Camera data and credentials
need no rollback because they were not changed.
