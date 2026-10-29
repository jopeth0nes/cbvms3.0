# Open camera view — 2026-10-29

Working baseline: integration/monitor-workspace-20261029, HEAD 66b4397.
The user reports the previous freeze fixes now pass manual testing. Preserve the
font-tuple, visibility, stale-window callbacks and shared capture fixes.
Before feature edits, exact copies of touched existing files and the pre-existing
diff were saved in output/monitor_workspace/open-camera-view-baseline/.
Rollback ONLY this feature: restore those individual files from that directory,
remove new feature files listed in the completed checkpoint. Do not git reset,
clean, or reapply the older Monitor Workspace patch. Preserve diagnostic artifacts.
No commit or push authorized.

## Completed implementation

“Open camera view” replaces the old button. The non-modal chooser uses configured
source names/stable keys, selected camera cards, runtime status, destination,
automatic Focus/Side-by-side preview, validation, Escape/Enter and one-instance
focus. Cancel does not change presentation or capture. The successful selection
prefills the next chooser from `data/camera_view_preference.json`, using the existing
JSON preference helpers; it never changes `camera_preference.json`. Missing keys
are filtered with primary-source fallback. Opening the chooser does not probe devices.
“Available to open · connection not checked” deliberately does not promise connectivity.

Live Monitor accepts either source or both; its existing Activity & alerts remain
unchanged. One reusable separate window accepts either source or both, with labels,
per-role Retry, resizing, F11 fullscreen and Escape exit. The legacy Focus/Split/
Other camera shortcuts remain available. Presentation never selects the analysis
source; only the existing Source control changes that owner.

One companion PreviewSession is retained while an accepted main/window consumer
needs it, including minimized consumers; pixels are not sampled/rendered for hidden
consumers. Primary annotated pixels fan out to visible primary canvases from the
existing dashboard tick; the same tracking/analysis workers keep running even when
that source is hidden. No inference, persistence or notification entry point was
added to the companion. Primary error after worker release permits an independent
companion to remain usable. Invalid third-source subscription combinations are
rejected: this remains one primary plus one preview owner, not arbitrary grids.

The existing asynchronous switch/release barriers and stale generation handling
are retained. Popup handlers are instance-guarded; image references are disposed
on Tk. All transient CTk fonts are tuples. Dialog variables use Tcl names directly
rather than Python Variable objects with thread-sensitive destructors. One existing
workspace timer polls all companion consumers; layout is reconciled only when its
signature changes. No per-window render timer or nested modal loop was introduced.

## Files in this feature

- `ui/camera_view_dialog.py`: chooser, stable preference validation, keyboard controls.
- `ui/monitor_workspace.py`: accepted presentation subscriptions, reusable two-tile
  window, truthful labels, shared ownership, visibility/cleanup and retry handling.
- `ui/dashboard.py`: primary rendering fan-out and freshness flag; hidden primary
  skips pixel drawing while tracking, analysis and alerts still run.
- `api/camera_store.py`: separate allowlisted presentation preference.
- `tests/test_camera_view.py`, `tests/test_camera_view_native_ui.py`: new behavioral
  and native regressions. Existing `test_live_dashboard.py` / `test_camera_switching.py`
  fixtures adapt to the rendering interface; hidden-primary analysis is covered.
- `scripts/monitor_view_diagnostics.py`: actual-controls, same-process 24-step scenario.
  `scripts/verify_monitor_popup.py` exposes `--view-workflow`; diagnostic preference
  writes are suppressed. `scripts/monitor_normal_diagnostics.py` also suppresses the
  new view-preference writes for manual disposable login testing.
- `docs/MONITOR_WORKSPACE.md`: pointer to this current workflow/checkpoint.

Pre-existing edits to `tests/test_monitor_workspace.py` and
`tests/test_monitor_workspace_native_ui.py`, freeze diagnostics and all old
measurement artifacts were preserved. No patch was reapplied, no branch changed.

## Verification actually performed

99 focused tests passed:

```sh
.venv/bin/python -m unittest tests.test_camera_view tests.test_monitor_workspace tests.test_preview_session tests.test_camera_switching tests.test_live_dashboard tests.test_live_monitor_ui -q
```

13 distinct native tests passed across targeted runs (5 chooser, 2 workspace,
6 existing live-monitor). Synthetic frames/disposable databases only:

```sh
.venv/bin/python -m unittest tests.test_camera_view_native_ui tests.test_monitor_workspace_native_ui tests.test_live_monitor_native_ui -q
```

Includes 100%/125% widget scaling and control bounds, actual Escape/Enter events,
cancel, one-instance opening, shared capture, reconfiguration, hidden consumers,
missing sources, offline/retry, label correctness, image cleanup and worker-GC font
finalization regressions. Existing PhotoImage/CTkImage HighDPI warning remains in
unrelated dashboard widgets. Native dialog screenshot inspected and retained as
`output/monitor_workspace/open-camera-view-20261029/synthetic-dialog.png`.
The desktop inspection service timed out; a bounded native screenshot of only the
synthetic dialog supplied visual evidence instead.

The 24-step real dashboard workflow ran once with synthetic capture and twice
with physical cameras (the final physical run uses final product code):

```sh
.venv/bin/python scripts/verify_monitor_popup.py --view-workflow --synthetic-dashboard --output-dir /private/tmp/cbvms-open-view-synthetic
.venv/bin/python scripts/verify_monitor_popup.py --view-workflow --physical usb --output-dir /private/tmp/cbvms-open-view-physical-final
```

Use a NEW output directory to rerun. These runs use an empty temporary database,
observation-only mode, suppressed primary/view preference writes, existing models,
UI heartbeat and external/native watchdog. Physical sequence: MacBook primary,
chooser cancel, primary-only separate, companion-only main, both main/window,
resize/minimize/restore/fullscreen, close, actual Source selector to Front Gate,
MacBook separate/close, both separate, reconfigure one/both, Settings/back, actual
Source selector back to MacBook, both separate/close, logout. All 24 steps completed,
no callback errors, primary/tracking/analysis and all companion workers stopped.

| Run | Heartbeats | p95 gap | Maximum gap | Renders | Total render time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Synthetic | 265 | 90.02 ms | 1701.53 ms | 795 | 608.77 ms |
| Physical initial | 568 | 65.46 ms | 1747.14 ms | 1053 | 905.50 ms |
| Physical final | 565 | 67.74 ms | 1797.23 ms | 1038 | 888.97 ms |

Final maximum individual render: 6.28 ms. Largest heartbeat gaps follow Settings;
next largest follow restore (665.92 ms) and minimize (625.90 ms). No persistent
freeze and no 2-second watchdog trigger occurred. Final per-action rendering totals
are in summary.json. Sampled ownership never exceeded one companion and one camera
window; no captured camera open/release operation ran on MainThread. These short
runs are responsiveness evidence, not sustained FPS or recognition accuracy tests,
and not a matched before/after speedup claim.

Durable sanitized evidence: `output/monitor_workspace/open-camera-view-20261029/`
contains synthetic, physical, physical-final summaries, provenance, JSONL events,
audits, empty successful watchdog/stack outputs, and final source hashes. Original
`/private/tmp/cbvms-open-view-*` directories also remain. Compilation and
`git diff --check` pass. No production database, saved camera configuration,
recognition threshold, model or dependency changes were made.

## Review launch and manual acceptance

Close any other CBVMS instance using the cameras. For disposable, observation-only
manual testing with the real login path:

```sh
cd /Users/jopethones/Documents/GitHub/cbvms3.0
.venv/bin/python scripts/verify_monitor_popup.py --normal-app --output-dir /private/tmp/cbvms-camera-view-manual-review
```

Choose a fresh output directory. Sign in with the disposable seeded account
`admin` / `admin123`. This harness disables cloud sync, suppresses camera/view
preference writes, starts with MacBook primary, and ends on logout (roughly
10-minute maximum). Its camera-view-preference suppression was compiled/reviewed;
this turn's automated dashboard checks bypassed login, using the same isolated
DB/observation policy. Normal login smoke evidence from the prior checkpoint is
retained, not claimed as a new end-to-end manual login pass.

1. Open camera view; check empty-selection validation, Tab/Space, Escape cancel,
   repeat-click focus, then select either/both and each destination.
2. Verify main Activity & alerts stays main-only; only the primary says Analysis
   active; the other stays Preview only. Resize and minimize each window.
3. With MacBook primary, open/close single/both separate views. Use Source to switch
   to Front Gate; open MacBook alone and both separately; switch Source back.
4. Move the separate window to a second physical display; test F11/Escape, labels,
   letterboxing and mirror overlay visually. Try actual network loss/recovery and Retry.
5. Log out and confirm cameras release. In a normal app launch, confirm last accepted
   chooser settings persist; the disposable harness intentionally suppresses writes.

Normal production launch remains `.venv/bin/python main.py`; it uses the normal
application data and behavior. Use the isolated command above for acceptance tests.

Remaining limits: second-display movement and actual cable/network outage were not
physically tested; full dashboard visual inspection at every display scale and
representative populated-gallery/sustained load remain manual. Settings still has
about a 1.8-second synchronous navigation pause; this feature does not claim to fix
it. Simultaneous two-camera analysis, arbitrary grids and named saved workspaces
remain out of scope.

## Rollback / next action

Review and manually accept this uncommitted branch. Do not commit/push until asked.
To roll back ONLY this feature, copy the matching baseline files from
`output/monitor_workspace/open-camera-view-baseline/` back to:
`ui/monitor_workspace.py`, `ui/dashboard.py`, `api/camera_store.py`,
`tests/test_live_dashboard.py`, `tests/test_camera_switching.py`,
`scripts/verify_monitor_popup.py`, `scripts/monitor_normal_diagnostics.py`,
`docs/MONITOR_WORKSPACE.md`.
Remove only the new implementation/test helper files `ui/camera_view_dialog.py`,
`tests/test_camera_view.py`, `tests/test_camera_view_native_ui.py`,
`scripts/monitor_view_diagnostics.py` if reverting. Keep checkpoints, baseline copies,
old scripts and measurement evidence. Do not restore all files from HEAD: that would
lose the accepted uncommitted freeze fixes. The feature preference file, if created
later during normal review, is separate from the primary/camera configuration and
may be retained harmlessly after rollback. No git reset/clean/branch switch required.

## Checkbox selection correction — 29 October 2026

Baseline: clean `main` at `47a9d37`. This correction is uncommitted; do not push
until the user manually accepts it. Earlier baseline/rollback instructions above
refer to the original feature, not this small correction.

Confirmed cause: the REAL Tk Checkbutton's `invoke()` sets unchecked values to the
string `"0"`. `selection()` used Python truthiness, so that unchecked source stayed
selected. Before the fix, both the headless Tcl regression (`value='0'`) and native
`test_real_uncheck_front_gate_only` failed: actual `[USB, IP]`, expected `[IP]`.
This explains the selected borders, Side-by-side preview and wrong submitted IDs.
A second native assertion exposed deferred layout: the initial both-camera window
had no primary tile gridded until the next poll.

Correction:
- `ui/camera_view_dialog.py`: use `self.tk.getboolean(self.getvar(var))` in the
  existing authoritative `selection()` method. Refresh and submit continue to use
  that result for borders, layout text, enabled state, accepted IDs and preferences.
  Named Tcl variables, disposal, fonts and event handling are unchanged.
- `ui/monitor_workspace.py`: reconcile popup selection immediately at creation and
  reconfiguration. Clear old role-canvas images on Tk before mapping selected tiles;
  reconcile role changes before primary rendering as well as during companion polls.
  This avoids old/unselected tiles or pixels lingering for a poll interval. Capture
  subscriptions remain derived from accepted IDs and other consumers; no capture,
  inference, thread, GC or device-ownership changes were introduced.
- `tests/test_camera_view.py`: real Tcl string/integer 0/1 regression, alongside
  existing stale/missing preference checks.
- `tests/test_camera_view_native_ui.py`: real Checkbutton invocation, each single
  choice, both/none, toggling on/off, matching borders/text/submitted and saved IDs,
  Enter-disabled submission, immediate mapping/image cleanup, existing-window
  reconfiguration, cancel preservation and stable IDs through primary role reversal.
- `scripts/monitor_view_diagnostics.py`: use actual checkbox invocation instead of
  assigning integers; assert selection and immediate tile layout; add both → Front
  Gate → MacBook → both reconfiguration to the same-process physical scenario.

Verification:

```sh
.venv/bin/python -m unittest tests.test_camera_view tests.test_monitor_workspace tests.test_preview_session tests.test_camera_switching tests.test_live_dashboard tests.test_live_monitor_ui -q
.venv/bin/python -m unittest tests.test_camera_view_native_ui tests.test_monitor_workspace_native_ui -q
.venv/bin/python scripts/verify_monitor_popup.py --view-workflow --physical usb --output-dir /private/tmp/cbvms-checkbox-physical
```

100 headless/fake-widget tests passed. Nine distinct native tests passed (seven
chooser, two workspace), including the existing worker-GC/font regression. The
native chooser suite was rerun after adding explicit stale-image and cancel checks.
The physical automated run used actual dialog controls, both cameras, temporary DB,
observation-only mode and suppressed preference writes. All 27 steps passed;
MacBook primary → views/reconfiguration → Front Gate primary → MacBook pop-out →
both → Settings/back → MacBook primary → clean logout remained in ONE process.
No callback errors, primary/tracking/analysis stopped, all four companion sessions
stopped. 599 heartbeats, p95 68.66 ms, maximum 1774.41 ms following Settings navigation;
1181 renders, maximum individual render 6.50 ms. No watchdog stall was triggered.
This is automated physical/UI verification, not a claim of user manual acceptance.
The existing Settings navigation pause was not part of this fix.

Evidence: `output/monitor_workspace/checkbox-selection-20261029/` contains summary,
provenance, sanitized events, source hashes and empty successful watchdog/stack
outputs. Originals remain in `/private/tmp/cbvms-checkbox-physical`. `git diff --check`
passes. No commit/push, production writes, camera configuration changes, or new
simultaneous analysis were performed.

Manual acceptance: use the isolated login command above with a fresh output folder.
Open both cameras separately, reopen the chooser, uncheck MacBook: only Front Gate
has a teal border, preview says Focus, and Open replaces the window with one Front
Gate tile. Repeat MacBook only, both, neither (Open and Enter must not submit), and
Cancel. Switch primary via Source and confirm explicit display selections persist.

Next action: user manual verification, then commit/push only if explicitly requested.
To undo only this correction, restore the five implementation/test/script files
listed in this section to their `47a9d37` versions after preserving any later edits.
Keep the evidence/checkpoint; do not apply the earlier feature rollback instructions.
