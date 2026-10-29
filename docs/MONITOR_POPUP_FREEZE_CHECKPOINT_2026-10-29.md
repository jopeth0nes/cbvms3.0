# Pop-out investigation checkpoint — 29 October 2026

**Latest state: see “Same-session source-handoff investigation — latest” below.
The earlier font deadlock fix remains, but the subsequently reported source-switch /
pop-out-close freeze is NOT resolved. Twenty physical and twenty corrected synthetic
same-session rounds did not reproduce it. Instrumented normal-login launch is ready.**

## State and conclusion

Branch `integration/monitor-workspace-20261029`, HEAD `66b4397`. Changes below are
uncommitted; no reset, branch switch, commit, merge or push. Earlier untracked
`output/monitor_workspace/` artifacts preserved. No task-owned diagnostic remains
running after the completed runs. Existing Python PID 79265 was identified as
`recovery_mail_relay.py`, not a camera reader, and was left untouched.

**Confirmed defect fixed:** `_tick` previously sampled/copied and rendered the
companion even when the pop-out was minimized. It also repeatedly configured
unchanged labels/title every 50 ms. New regressions cover hidden sampling,
Split tile continuity, restore, destroyed-window references and closed timers.
This is **not a proven cause of the user's persistent freeze**.

**Persistent freeze not reproduced** in the bounded synthetic or two physical
arrangements. The ~1.8-second Settings-navigation pause persists. Recovered and
new native samples place main-thread work in Tk idle canvas display and AppKit /
CoreGraphics image painting. This identifies work during the temporary stall,
not the exact cause of a permanent freeze. No captured evidence attributes that
stall to camera open/read, network waits, capture locks or PhotoImage conversion.
Python signal stack files are empty despite registration; native samples succeeded.
Do not present the empty files as Python stack evidence.

## Changes

- `ui/monitor_workspace.py`: only visible, existing consumers render; no companion
  sample copy when none is visible. Minimized windows retain shared capture ownership.
  Hidden render keys are invalidated so restoration renders the latest sample on
  the next 50-ms poll. Unchanged labels/title are not configured repeatedly.
  Pop-out command restores an existing minimized window. Direct destruction clears
  references; normal close clears image references before destroying the widget.
  Closing the popup does not stop an embedded consumer. A pending poll is cancelled
  before replacing it, and closed workspaces cannot reopen or reschedule.
- `tests/test_monitor_workspace.py`: five additional targeted checks relative to HEAD.
- `tests/test_monitor_workspace_native_ui.py`: real PhotoImage release, direct native
  destruction/reopen, continued shared session; existing Other camera/layout checks.
- `scripts/verify_monitor_popup.py`: recovered bounded diagnostic, synthetic default,
  optional physical temporary DB + observation-only + suppressed preference saving;
  50-ms heartbeat, external watchdog, phase/render/configure counters, sanitized
  exception types, 65-second parent deadline. Watchdog samples before timeout cleanup.

No capture/pipeline/model/threshold/database or camera-setting code changed.
Capture open/read/release remain worker-owned; Tk and PhotoImage stay on Tk.
There is one polling timer and no popup-specific render timer or resize queue.

## Evidence and measurements

Durable local evidence: `output/monitor_workspace/popup-freeze-20261029/` includes
summaries, phase timestamps, watchdog events, sanitized native stacks and final
source hashes. Originals remain in `/private/tmp/popup-before-{1,ip,usb}` and
`/private/tmp/popup-resume-{synthetic,ip,usb}`. Earlier integration artifacts remain.
No camera images or credentials were saved by these diagnostics.

| Run | Max heartbeat gap before → after ms | p95 gap before → after ms | Render calls before → after | Total render ms before → after |
|---|---:|---:|---:|---:|
| Synthetic | 639.6 → 648.4 | 55.9 → 56.4 | 346 → 334 | 655.1 → 627.1 |
| Front Gate primary | 1813.3 → 1800.9 | 69.9 → 65.4 | 617 → 636 | 758.8 → 801.1 |
| MacBook primary | 1795.4 → 1807.3 | 82.1 → 72.5 | 710 → 703 | 918.1 → 885.9 |

Same action order/timing and window geometry; separate live scenes, startup/cache
conditions and added diagnostic counters prevent causal performance conclusions.
Physical render totals include the PRIMARY feed. Synthetic minimized Focus phase
has zero render calls after the fix. Max individual measured render call after:
7.73 / 8.17 / 11.03 ms respectively. No workspace configure-event storm observed;
that counter does not cover every nested Tk/AppKit paint or geometry callback.
All completed runs have zero Tk callback errors and clean companion shutdown;
physical runs also report primary and analysis workers stopped. No persistent
freeze or forced termination. The same temporary navigation pause remains.

Native lifecycle actions exercised Focus pop-out, resize, minimize/restore,
fullscreen/exit, close/reopen, Split pop-out, source switch, Settings/back and
logout. These are automated callback/native-event checks, not proof of every
manual mouse interaction. The short final return/source-switch phases did not
always deliver frames before the next scheduled action: successful reconnect
latency after Settings is not accepted from these runs. Other camera transition
and shared ownership are covered by the separate native/unit checks.

## Verification actually run

```
.venv/bin/python -m unittest tests.test_preview_session tests.test_monitor_workspace tests.test_live_dashboard tests.test_camera_switching tests.test_live_monitor_ui -q
```
91 passed. Native suite: 7 passed; after adding image cleanup/direct destruction
assertions, affected `tests.test_monitor_workspace_native_ui` reran and passed.
Existing CustomTkinter PhotoImage HighDPI warning remains. Python compilation and
`git diff --check` passed. No full-suite rerun or multi-camera analysis enabled.

## Reproduce / next action

Run each separately in an active Mac desktop session; use a new output directory:

```sh
cd /Users/jopethones/Documents/GitHub/cbvms3.0
.venv/bin/python scripts/verify_monitor_popup.py --output-dir /private/tmp/popup-check-synthetic
.venv/bin/python scripts/verify_monitor_popup.py --physical ip --output-dir /private/tmp/popup-check-ip
.venv/bin/python scripts/verify_monitor_popup.py --physical usb --output-dir /private/tmp/popup-check-usb
.venv/bin/python -m unittest tests.test_monitor_workspace_native_ui tests.test_live_monitor_native_ui -q
```

Physical commands use a disposable empty database and observation-only analysis;
no production recognition gallery, attendance or violations are used. Close other
camera readers first. The script exits automatically and does not alter saved
camera preferences. Do not rerun indefinitely if it completes without a freeze.

For manual reproduction, launch `CBVMS_ASSESSMENT_OBSERVATION_ONLY=1 .venv/bin/python main.py`,
sign in, select Front Gate primary/MacBook companion, try Pop out from Focus then
Split; repeat with sources reversed. Resize, minimize/restore, F11/Escape,
close/reopen, Other camera, Settings/back; wait for actual frames after each source
change. Note the exact action/source/view that freezes. This normal launch uses
the existing database/startup services; observation-only suppresses live assessment
writes, not all unrelated application writes. Prefer the isolated diagnostic for
initial checks. Next investigation requires the user's precise persistent-freeze
sequence and a bounded diagnostic trace of it; do not label this freeze fixed.

## Repeated-open/close reproduction and fix — latest state

Same branch and HEAD (`integration/monitor-workspace-20261029`, `66b4397`). All
changes remain uncommitted. Previous visibility fixes and artifacts preserved.
No camera diagnostic remains running; unrelated recovery mail relay left alone.

### Confirmed blocking path

Extended the existing harness with 30 bounded cycles. Each cycle opens and waits
for a real rendered frame, invokes the registered WM_DELETE_WINDOW handler,
immediately opens/closes before first render, makes three rapid open requests,
waits for the replacement to render, closes and lets the normal poll request
capture cancellation before the next reopen. No production sleeps or forced GC.

The pre-fix synthetic Focus run deadlocked on **cycle 19** (zero-based 18) after
18 completed cycles. At the stall, window/image/timer/active-worker counts had
not accumulated. Python stacks show:

- Tk main thread: `MonitorWorkspace._tick → PreviewSession.start → Thread.start
  → Event.wait` (waiting for the new thread's started signal).
- New worker bootstrap: `Event.set → Condition.__enter__ → tkinter.font.Font.__del__`.
  Cyclic GC finalized the destroyed pop-out label's default `CTkFont` on that
  worker. Its Tcl font-delete call waits for the Tk thread, completing the deadlock.
- Native sample corroborates the main thread waiting on a Python condition lock,
  not the earlier AppKit image-painting pause.

External watchdog captured at 2.03 seconds and completed its native sample before
parent stopped only its task-owned child after 8 seconds without a heartbeat.
Both Python and native stacks are retained. This is the repeated-open/close freeze
reproduced in this environment, not an inference from the Settings pause.

A deterministic native regression disables automatic collection only within the
test, opens/closes the popup and explicitly collects on a test worker. It records
unsafe Font finalizers rather than allowing their Tcl calls to deadlock the test.
Before correction it failed with the popup's `CTkFont` reaching worker GC; after
correction it passes. Deferred test fonts are finalized on Tk during test cleanup.
Production GC behavior and thread scheduling are unchanged.

### Small correction

`ui/monitor_workspace.py` now supplies the pop-out label a theme-derived scaled
font tuple. It creates no Python-owned Tk font finalizer to survive in destroyed
widget cycles. There is no global monkeypatch, forced GC, sleep, disabled button,
exception suppression or change to capture ownership/analysis.

Close/fullscreen commands now capture a particular popup instance and reject old
instances, so queued old-window commands cannot affect a replacement. The existing
Destroy identity guard distinguishes child/old events. Direct native destruction
also drops the preview PhotoImage on Tk. Normal cleanup remains idempotent and
preserves the visible Split consumer. Existing single polling timer and visibility
fix are retained; there are no application-owned popup render timers to accumulate.

`tests/test_monitor_workspace.py` adds stale-instance callback isolation.
`tests/test_monitor_workspace_native_ui.py` adds the actual font-finalizer regression.
`scripts/verify_monitor_popup.py` adds `--cycles`, `--view`, `--manual`, lifecycle
counts, render timestamps, memory samples, fresh-frame deadlines and bounded parent
supervision. Split cycles finish with minimize/restore, shared-owner checking,
Settings/back and a wait for actual primary STREAMING + companion rendering.
No camera settings, recognition thresholds, production records or second-camera
analysis changed. All physical tests use a temporary empty DB and observation-only.

### Final results

| Condition | Cycles | Max heartbeat gap | p95 gap | Result |
|---|---:|---:|---:|---|
| Before: synthetic Focus | froze on 19 | >8 s, terminated after capture | no final summary | reproduced |
| After: synthetic Focus | 30 | 88.9 ms | 65.2 ms | passed |
| After: Front Gate primary, Focus | 30 | 231.5 ms | 69.5 ms | passed |
| After: MacBook primary, Focus | 30 | 244.2 ms | 73.0 ms | passed |
| After: Front Gate primary, Split + navigation | 30 | 1740.6 ms | 76.2 ms | passed, temporary navigation pause |
| After: MacBook primary, Split + navigation | 30 | 1763.8 ms | 109.2 ms | passed, temporary navigation pause |

The matched synthetic comparison changed the popup implementation; physical runs
validate the integrated dashboard separately, not an identical-scene throughput
benchmark. Every cycle waits for rendered frames before close and after reopen.
All five post-fix runs have zero callback errors, at most one live popup and one
active companion worker, and clean companion shutdown. Primary/analysis shutdown
passed on all physical runs; explicit tracking completion was added after the
Front Gate Focus run and passed in the other three physical runs. Both Split runs
confirmed the same owner survived popup close and actual frames resumed after
Settings/back. Existing native tests also cover Other camera and layout cleanup.

At the rendered stage, synthetic timers/images were exactly 4/16; physical Focus
9/18; physical Split 9/19 across all 30 cycles. No cycle-wise growth in these
counts. In Split there was one companion session during the cycles, with a second
created after navigation; in Focus 30 serial sessions, never overlapping. Sampled
RSS varies with native/model caches: final five synthetic rendered samples roughly
282–284 MB; Front Gate Focus 585–591 MB; MacBook Focus 1074–1084 MB. Split rendered
ranges 791–835 MB and 1037–1109 MB. These bounded runs are not a long-term leak proof.

Final commands/results:

```
.venv/bin/python -m unittest tests.test_monitor_workspace tests.test_preview_session tests.test_camera_switching tests.test_live_dashboard tests.test_live_monitor_ui -q
# 92 passed
.venv/bin/python -m unittest tests.test_monitor_workspace_native_ui tests.test_live_monitor_native_ui -q
# 8 passed; existing CustomTkinter PhotoImage/HighDPI warning
```

Python compilation and diff whitespace checks passed. No unrelated suite reruns.
Final evidence in `output/monitor_workspace/popup-repeat-20261029/`:
`comparison.json`, final source hashes, lifecycle events, watchdog events, sanitized
Python/native stacks, and per-run summaries. Original folders:
`/private/tmp/popup-cycles-synthetic-focus` and
`/private/tmp/popup-cycles-fixed-{synthetic-focus,ip-focus,usb-focus,ip-split,usb-split}`.
Evidence stores no images, frame pixels, database contents or camera credentials.

### Exact reproduction and manual acceptance

From the repository, each command requires a fresh output directory:

```sh
.venv/bin/python scripts/verify_monitor_popup.py --cycles 30 --view Focus --output-dir /private/tmp/popup-review-synthetic
.venv/bin/python scripts/verify_monitor_popup.py --cycles 30 --view Focus --physical ip --output-dir /private/tmp/popup-review-ip-focus
.venv/bin/python scripts/verify_monitor_popup.py --cycles 30 --view Split --physical usb --output-dir /private/tmp/popup-review-usb-split
```

Swap `ip`/`usb` and Focus/Split to exercise the other combinations. The supervisor
captures stalls before stopping only its own test process; do not launch competing
camera readers. No indefinite automated reruns are needed for acceptance.

Instrumented manual dashboard, real cameras, isolated records, 10-minute bound:

```sh
.venv/bin/python scripts/verify_monitor_popup.py --manual --physical ip --view Focus --output-dir /private/tmp/popup-review-manual
```

This is the integrated normal dashboard with an empty temporary database; it bypasses
login and does not use production student data. Click Pop out, wait for video,
close with the red window button, immediately reopen; repeat at least 30 times.
Include a close before video, repeated button clicks and minimize/restore. Repeat
in Split and verify the embedded video keeps moving. Visit Settings and return,
wait for frames, then log out. Repeat with `--physical usb` and a new output folder.
If a freeze recurs, leave it untouched: the watchdog captures evidence and the
supervisor stops this diagnostic after eight seconds of lost heartbeat. Send the
output folder and exact action/cycle. Do not kill unrelated processes.

**Next action:** user manual acceptance of the reproduced-and-fixed sequence.
The separate ~1.8-second Settings painting pause is still present; fullscreen/
second-display/macOS-scale combinations and long-duration use are not exhaustively
covered by these repeated-cycle runs. Simultaneous multi-camera analysis remains
deferred. Do not commit/push until requested.

## Same-session source-handoff investigation — latest

Recovered after account interruption on 29 October. Branch still
`integration/monitor-workspace-20261029`, HEAD `66b4397`; all existing changes and
artifacts preserved, no commit/push/reset. No new product fix in this continuation:
the user-reported **MacBook primary → Front Gate primary → close MacBook pop-out**
freeze remains unconfirmed by a failing automated trace. Do not substitute the
previous font-finalizer diagnosis as proof of this newer source-history failure.

### Recovery and running code

Historical normal-app PID 70706 was sampled read-only while still running; its cwd
was this repository. Sample is `output/monitor_workspace/source-handoff-20261029/
existing-app-native.txt`. It showed Tk event-loop idle/painting during sampling,
not the prior Thread.start/font-finalizer deadlock. It cannot establish which
Python module versions that existing process had loaded. User then confirmed the
app was closed; process check verified it and its network child were gone.
No historical PID was killed. Final current process check must distinguish the
unrelated recovery mail relay (previously PID 79265), which was left alone.

The interrupted physical diagnostic had already completed successfully. Its
`provenance.json` records `.venv/bin/python`, `.venv` prefix, absolute imported
module paths, SHA256 hashes, revision, branch and dirty status. All five recorded
application module hashes still matched the actual checkout on recovery. New
provenance also records `ui.camera_source_selector`. No old checkout was used for
that run. The normal user app's exact in-memory source hashes remain unknowable
from the native sample alone.

### What actually ran

1. Initial integrated synthetic attempt failed `usb_readyTimeout`: the test Capture
   did not cache a first frame during open and lacked last_error. This was a fixture
   defect, not the application freeze; no pass claimed. Corrected the fake capture
   to honor the real CameraCapture initial-frame contract and unique frame tokens.
2. Interrupted physical run: **20 full rounds in ONE dashboard process**, real
   source selector `open()/select()` creating/destroying the actual dropdown,
   MacBook primary initially. Each round includes three pop-out open/frame/close
   sequences, Focus/Split/Other transitions, Front Gate selection, pop-out frame and
   close, immediate open/close, Split open/close, then reverse to MacBook with the
   pop-out already open. Odd rounds also keep a popup open during the forward switch.
   Result: no errors, max heartbeat **404.55 ms**, p95 **88.24 ms**; clean primary,
   companion, analysis and tracking shutdown. 160 serial companion sessions total.
3. Corrected integrated synthetic run: same 20-round history, actual dashboard,
   selectors, model workers and native Tk, fake pixel sources; no errors, max
   heartbeat **694.64 ms**, p95 **72.46 ms**, all workers stopped. 170 serial sessions;
   timing determines cancellation/restart counts. This is not physical-camera FPS.
4. Normal `main.main()` login-path smoke with disposable DB: native login window
   created and automatically closed after 1.5 seconds, 27 heartbeats, maximum gap
   **124.09 ms**, no watchdog capture. This smoke did NOT authenticate or enter the
   dashboard; full manual login/transition acceptance remains outstanding.

Physical evidence audit: 72 successful traced USB lock acquisitions; no overlapping
owner, mismatched release or retained final owner. All traced capture open/release
operations ran off MainThread. At snapshots: pop-outs 0–1, timers exactly 9, Tk
images 17–19, active companion sessions/workers 0–1. RSS 551–1124 MB over changing
model warmup/live scenes; no constant-memory claim. Existing handoff code waits
asynchronously for both prior primary and companion completion before new primary
startup. No observed old callback stopped a new owner. These observations do not
prove every manually timed race is absent.

### Changed diagnostic files

- `scripts/verify_monitor_popup.py`: `--handoffs`, `--synthetic-dashboard`, provenance,
  isolated synthetic capture contract correction, worker cleanup, `--normal-app` /
  hidden `--login-smoke` entry points. Parent still stops only its own child after
  heartbeat loss, giving the external sampler time first. Errors exit nonzero.
- `scripts/monitor_handoff_diagnostics.py`: real selector same-session scenario,
  source IDs/generation/window-instance traces, capture boundaries, USB lock owner
  traces, timer schedule/cancel metadata, revision/module provenance. No URLs or
  secrets, frame pixels or student records recorded.
- `scripts/monitor_normal_diagnostics.py`: real `main.main` login/model-preparation/
  dashboard path, isolated DB; observation-only; cloud synchronization disabled;
  saved preference writes suppressed and initial preference MacBook. Logout ends
  the diagnostic instead of exec-restarting it. Existing production DB untouched.
- This checkpoint and durable evidence copies. Product code from prior fixes is
  unchanged by this continuation; no speculative lifecycle correction added.

Relevant prior 92 focused / 8 native test results are reused because their product
code and assertions are unchanged. New diagnostics compile and `git diff --check`
passes. The 20-round runnable integrated scenario is the history-preserving
regression/reproduction check; it has not produced an application failure to fix.

Evidence retained under `output/monitor_workspace/source-handoff-20261029/` with
subfolders `popup-handoff-before-synthetic`, `popup-handoff-before-physical`,
`popup-handoff-corrected-synthetic`, `popup-normal-login-smoke`, original existing-app
sample and final hashes. Corresponding `/private/tmp/` originals remain. Empty
watchdog/stack files from successful runs are not evidence of a captured freeze.

### Exact next action: manual normal-launch acceptance with watchdog

Close other CBVMS camera readers. From Terminal:

```sh
cd /Users/jopethones/Documents/GitHub/cbvms3.0
.venv/bin/python scripts/verify_monitor_popup.py --normal-app --output-dir /private/tmp/cbvms-normal-handoff-manual
```

Use a new output folder each run. This runs the real login → dashboard flow, with
an empty disposable database, not your production accounts/roster. Sign in using
the repository's disposable seeded account `admin` / `admin123` (or its seeded
superadmin account if reproducing that role). The actual application login code
runs; authentication is not mocked. Cloud sync is disabled for this test DB.

1. Confirm MacBook primary video; repeatedly open/close pop-out.
2. Switch Focus → Split → Other camera → Focus.
3. Use the actual primary Source selector to choose Front Gate; wait for video.
4. Open pop-out and close it, first immediately and then after MacBook video arrives.
5. Repeat in Split, switch back with the popup open, and then log out.

The watchdog is active during login and dashboard, captures a native sample after
2 seconds of lost heartbeat, and the parent stops ONLY this diagnostic after
8 seconds of lost heartbeat. Leave a frozen diagnostic untouched for capture.
A normal manual run is bounded to roughly 10 minutes; logout ends it. Share the
output folder and exact last action if it freezes. No need to repeat successful
physical automation indefinitely.

For reproducible automated checks (unique output folder required):

```sh
.venv/bin/python scripts/verify_monitor_popup.py --handoffs 20 --physical usb --output-dir /private/tmp/handoff-physical-review
.venv/bin/python scripts/verify_monitor_popup.py --handoffs 20 --synthetic-dashboard --output-dir /private/tmp/handoff-synthetic-review
```

Limitations: normal login-to-dashboard manual sequence not yet exercised with a
user; empty test gallery differs from production load; 20 automated rounds cannot
exclude arbitrary timing-dependent failures. The separate ~1.8-second Settings
navigation pause remains outside this sequence. Simultaneous multi-camera analysis
stays deferred. No commit/push until user acceptance.
