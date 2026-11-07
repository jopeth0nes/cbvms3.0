# Trackpad scrolling checkpoint — 29 October 2026

Status: implemented locally; physical trackpad acceptance pending. No commit or push.
Baseline: `ea997f42d5b75b12e58787f1b2691461256a5589` (working tree was clean).

## Cause and runtime evidence

Local runtime: macOS 26.5 arm64, Python 3.12.13, Tcl 9.0.3, Tk 9.0,
CustomTkinter 5.2.2. Installed CTkScrollableFrame binds only MouseWheel and
four global Shift handlers per frame. Tk 9 generates TouchpadScroll for precise
two-finger gestures, with signed X/Y values packed into delta. The existing
components helper and portal host also handled only MouseWheel. The helper
bound only existing descendants, swallowed boundary events, and had no
nested/window routing. CTk's global callbacks also retained destroyed frames.

Verified against installed `tk9.0/tk.tcl`, `text.tcl`, `listbox.tcl`,
`ttk/utils.tcl` and CTk Python source. Upstream specification:
https://core.tcl-lang.org/tips/doc/main/tip/684.md

## Inventory and implementation

| Area | Actual scroll controls / handling |
| --- | --- |
| Live Monitor | LiveAlerts cards use shared ScrollableFrame; camera image canvases excluded |
| Student Management | Enrollment Treeview keeps native handlers; enrollment/details/history forms use shared frames; premises table and term Text keep native handlers |
| Attendance / Unknown Sightings | Attendance Treeview native, filter form shared; unknown cards in LiveAlerts shared |
| Violations / Suspensions | Violation Treeview native; history and overview shared frames; records canvas routed by its declared scroll commands |
| Appeals / Records | Appeal list/detail/dialog shared frames; records trees native and detail frame shared; evidence image canvas excluded |
| Reports / Settings | Report trees native, filters shared; settings and camera configuration list shared |
| Other windows | Registration, checklist dialogs, training controls/thumbnails/horizontal capture strip, notifications and navigation shared; portal page/dialog canvases routed; account and academic-term native tables/text retained |
| Camera views | CameraFeed, login animation, registration/enrollment camera previews, charts and evidence image canvases without scroll commands are ineligible |

`ui/scrolling.py` installs one leading bindtag per mapped widget, using one
additive Map observer per interpreter. No global wheel bindings, polling,
focus changes, database access, inference or panel rebuilding. Future widgets
receive the tag on Map. Pointer coordinates and ancestry select the nearest
eligible canvas/native control, stopping at the toplevel. A modal grab blocks
routing to a different window. At a boundary the outer eligible area receives
the event; an event never moves both inner and outer. Direct native input is
left to existing class bindings; routed canvas movement stops propagation.
A single target handles diagonal movement on its eligible axes.

Tk 9 packed deltas move canvases by pixels. Legacy Aqua wheel values are not
divided by 120; Windows/X11 wheel values are normalized. Fractional movement
accumulates, resets on reversal or target changes, and uses weak widget keys.
Canvas increments of one preserve small movements. Scrollbar commands and
keyboard bindtags are retained. The shared CTk subclass avoids installing
upstream global wheel/Shift handlers and destroys its outer wrapper to release
canvas callbacks. Root-owned commands expire with their Tcl interpreter;
there is no widget registry retaining destroyed panels.

Changed production files:
- New: `ui/scrolling.py`.
- Integration: `ui/window_lifecycle.py`, `ui/portal_scroll.py`, `ui/components.py`.
- CTk class substitutions only: `auth/register.py`, `ui/appeals_panel.py`,
  `ui/attendance_panel.py`, `ui/camera_configuration.py`, `ui/dashboard.py`,
  `ui/enrollment.py`, `ui/live_alerts.py`, `ui/multi_select_checklist.py`,
  `ui/notifications_panel.py`, `ui/records_panel.py`, `ui/reports_panel.py`,
  `ui/settings.py`, `ui/student_management.py`, `ui/suspension_overview.py`,
  `ui/suspensions_panel.py`, `ui/training_panel.py`.
- New tests: `tests/test_scrolling.py` (11 tests).

No capture, source-switch, inference, authorization, persistence, or camera
ownership logic changed. Production records were not used or modified.

## Verification performed

Native macOS tests require desktop/window-server access; sandbox-only launch
aborted before native tests could execute. The successful runs used desktop
access and disposable widgets/databases, with synthetic camera fixtures.

1. `.venv/bin/python -m unittest tests.test_scrolling tests.test_student_management_ui tests.test_camera_source_selector_native tests.test_student_portal_native_ui -v`
   — 24 tests: 20 passed, 3 failures, 1 error. This run included the initial 8
   scrolling tests; all 5 camera-selector tests passed. Portal navigation,
   large-data pagination/scroll/cleanup, dark mode and async tests passed.
2. Four failing tests rerun using the same interpreter against a disposable
   `git archive` of the baseline at `/private/tmp/cbvms-scroll-baseline`:
   all four reproduced identically. Existing failures are enrollment entries
   0 vs 4; missing View Detection Evidence button (StopIteration); unread
   count 1 vs 2; pending violation fixture absent. These were not modified.
3. `.venv/bin/python -m unittest tests.test_scrolling tests.test_camera_transition_native_ui tests.test_camera_view_native_ui tests.test_camera_switching tests.test_camera_freeze_watchdog -v`
   — **39 passed**, including the final 11 scrolling tests, delayed-camera
   heartbeat, pop-out lifecycle, switching ownership and watchdog regressions.
4. `.venv/bin/python -m compileall -q ui auth tests/test_scrolling.py` and
   `git diff --check` passed. Final tracked changes reviewed.

Scrolling tests cover signed packed axes, fractional movement and reversal,
pointer vs delivered widget, children, nested and native-table boundary handoff,
nonoverflow, horizontal/Shift input, real Tk TouchpadScroll dispatch, native
handler called once, overflowing Activity cards, separate dialogs and grabs,
dynamic descendants, repeated destruction/recreation without global-handler
accumulation or retained frames, inert camera canvas, and Windows/Linux delta
normalization.

Limits: generated events are not physical trackpad gestures. No actual camera
capture/inference FPS measurements or physical scrolling-under-load were done.
Windows/Linux normalization was tested on macOS; no native Windows/Linux claim.

## Manual acceptance

1. With monitoring active, overflow Activity & alerts and scroll gently over
   cards/text/thumbnails, then quickly reverse. Confirm smooth movement and
   responsive video; wheel over camera imagery must not change the source.
2. Scroll a long table in Student Management, Attendance, Violations or Records;
   try horizontal gestures/Shift+wheel where columns overflow. Check keyboard
   navigation and scrollbar dragging still work.
3. Scroll Settings and a long form. In a nested table/form, reach each boundary
   and confirm only the nearest movable area moves. Short content should stay put.
4. Open an overflowing student/appeal/portal dialog; scroll without clicking
   first. Ensure the dashboard behind it stays still, including with a modal grab.
5. Navigate away/back and open/close dialogs repeatedly; verify no doubled speed,
   stale scrolling or Tk errors. Recheck camera selection and separate camera view.

## Checkpoint and rollback

`output/scrolling/implementation.patch` contains only this task's Python changes,
including new files. `output/scrolling/manifest.json` records baseline and file
hashes. Keep this report/evidence. Stop the app before rollback. From repository
root, review the patch and check that later work has not changed these hunks:

```sh
git apply --reverse --check output/scrolling/implementation.patch
# Only if the check passes and these are still the intended changes:
git apply --reverse output/scrolling/implementation.patch
```

If the check fails, reverse only the scrolling-specific hunks manually; do not
reset/clean the repository or overwrite later edits. The patch does not touch
camera fixes, databases or existing checkpoint documents. No data rollback or
migration is required. No rollback was executed.
