# Live detection sounds

The existing monitor now supplies confirmed assessments to an audio-only consumer.
It does not change recognition thresholds, face/body ownership, tracking,
attendance, notification/database cooldowns, violation acceptance, appeals,
strikes, or suspension policy. Existing attendance and unknown-sighting work in
the working tree was preserved.

## Selection and confirmation

`LiveAssessment.uniform_assessment` is an enum (`UNASSESSED`, `CORRECT`, `WRONG`)
set only where `LiveState` already accepts evidence. `active_suspension` is a
boolean from `get_active_suspension`; display text and `suspension_tag` are never
parsed for audio.

For a reliably identified student, priority is active suspension, then an accepted
supported violation (`wrong_uniform` or `earring`), then an accepted correct
uniform with no accepted category. A suspended student with correct uniform gets
only the suspension alert for that assessment. Confirmed identity can qualify for
a suspension alert independently of torso/uniform availability. Unknown or
ambiguous identity cannot qualify. Missing/disabled models, hidden torsos, pending
analysis, and an empty violation list do not imply a correct uniform.

Immediately before playback, the audio worker queries the authoritative active
suspension state again. The existing database query excludes future, expired, and
lifted records. If the current priority differs or the read fails, the queued sound
is discarded; a subsequent confirmed transition can submit the new condition.
Audio invokes only this read, never a database write or notification creation.

## Queue and lifecycle

One lazy, serial playback worker handles detection audio, settings previews, and
legacy notifier alerts. Default notifiers and the legacy `play_alert()` helper
share the application master switch and dispatcher. There are at most 24 pending sounds; low-priority pending
items are evicted first. Duplicate encounter sounds coalesce, and urgent queued
sounds take the next slot, including arrivals during a slow suspension read.
An already playing short clip is not mixed with another clip.

Accepted-state transitions, rather than frames or periodic timers, request audio.
A separate 15-second per-student cooldown within the camera/monitor session also
prevents track changes from repeating sounds. Higher-priority transitions bypass
lower-priority cooldowns. Encounter/state and cooldown caches are bounded at 4096
entries. An unchanged held condition never repeats just because time passes.
Positives expire after 1.5 seconds in the queue; other sounds after 3 seconds.
Dropped, muted, and failed sounds are not replayed automatically in a later burst.

Before playback the worker rechecks cancellation, master/type switches, monitor
and camera generations, current camera freshness, same-person motion, and the
latest accepted encounter state. During playback it polls inexpensive freshness,
cancellation and settings guards every 20 ms; motion projection and suspension
queries are not run in that polling loop. Camera changes, leaving live monitoring,
worker stop, logout and application close discard pending work and cancel active
playback. Shutdown does not wait for a slow audio device on the UI thread.

Live persistence notifications use `play_sound=False`, retaining history and
listeners while preventing a second generic beep. Unrelated `notify(...)` callers
keep generic alerts by default; unknown alert behavior remains separate. The
bundled generic tone replaces platform-specific system sounds so it can share the
same controlled playback path.

## Settings and deployment

Settings → Notifications retains **Sound alerts** as the master switch and adds
**Correct uniform chime**, **Accepted violation tone**, and **Active suspension
alert**, each with a **Preview** button. Previews use only the audio dispatcher;
they create no detections, notifications, attendance or disciplinary records.
Previews respect both switches. Like the existing notification controls, these
settings apply for the current application session.

Assets and their generation/license details are in `assets/sounds/README.md`.
No additional Python audio dependency is required:

- macOS: local `afplay` executable.
- Windows: standard-library `winsound.PlaySound`, with filename, asynchronous and
  no-default-sound flags. The dispatcher waits for the known WAV duration and
  stops playback on cancellation. Windows does not provide completion/device
  feedback through this API; verify the selected output by listening.
- Linux: `paplay`, falling back to `aplay` when `paplay` is absent. Install the
  appropriate PulseAudio or ALSA utilities and configure a working output device.
  An installed player that reports a device/service failure is diagnosed rather
  than repeatedly trying other devices.

Sources: [Python winsound documentation](https://docs.python.org/3/library/winsound.html)
and [PulseAudio troubleshooting](https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/Users/Troubleshooting/).
No terminal bell, system siren or network fallback is used. Missing/corrupt assets,
missing players, nonzero player exits, timeouts and device errors emit a warning
through `cbvms.audio`; the latest detail is also in `notifier.audio.last_error`.
Warnings are rate-limited to one every ten seconds. Monitoring and visual history
continue after audio failure. Speaker gain and OS output routing remain under the
computer's control.

## Verification

Automated tests use mock audio backends, synthetic frames and disposable SQLite
fixtures. They cover mappings, confirmation/abstention, suspension precedence and
current-state rechecks, duplicate frames, crowded bounded queues, higher-priority
transitions, mute/type changes, previews, stale/cancelled work, native settings
buttons, shutdown, missing assets, platform command selection, and device failure.
The WAV checks measure duration, peak amplitude, endpoints and sample continuity.

Final verification on this macOS workspace: **560 tests passed across 51 modules**
in **172.487 seconds**, running each module in its own Python process. This covers
the existing enrollment preview, recognition, attendance, notifications, appeals,
strikes, suspensions, camera lifecycle and unknown-sighting regressions, plus
30 new audio tests, one native settings test and the notifier compatibility checks.
`git diff --check` and compilation of the changed Python files passed.

A prior single-process run passed 557 of 559 tests. Two native UI tests reported
missing event arguments in Tk callbacks: appeal inbox scroll restoration and the
appeal evidence workflow. Their modules passed unchanged when rerun separately
(11 and 16 tests), and both passed in the final isolated-module run. No appeal/UI
production code was changed to suppress those failures. One additional notifier
compatibility test accounts for the final total of 560.

Commands:

```sh
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python scripts/benchmark_detection_audio.py --native-tk --seconds 5
```

To reproduce the isolated-module run from the repository root:

```sh
.venv/bin/python - <<'PYTEST'
from pathlib import Path
import subprocess
import sys
for module in sorted(Path("tests").glob("test_*.py")):
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                    "-p", module.name, "-q"], check=True)
PYTEST
```

Native UI tests require a working desktop session. Machine-readable final test
and benchmark results are in `assets/sounds/verification.json`.

The macOS native Tk benchmark used a synthetic 720p/30 Hz source, five rendered
people, controlled 200 ms analysis work, and a silent backend simulating one-second
playback. Each condition ran for five seconds:

| Measurement | Muted | Slow mock audio |
| --- | ---: | ---: |
| Unique rendered preview FPS | 27.75 | 27.34 |
| Preview frame gap, p95 | 35.574 ms | 35.624 ms |
| Render tick, p95 | 23.350 ms | 25.383 ms |
| Audio preview submission, p95 | 0.034 ms | 0.039 ms |
| Maximum concurrent audio playback | 0 | 1 |
| Maximum queued audio | 0 | 3 |

The audio and analysis workers stopped successfully. These short synthetic results
show that mock device latency stayed off the preview path; they do not establish
physical-camera FPS, recognition accuracy, real audio quality, Windows/Linux device
behavior, or prolonged crowded-scene performance.

## Deployment listening/checklist

1. Start at a low system volume. Open Settings → Notifications and preview each
   enabled sound once. Check that the chime is pleasant, the violation tone is
   distinct, and the suspension pulses attract attention without sounding harsh.
2. Adjust the deployment computer's output volume for the room. Check both ends
   of every clip for clicks or sudden peaks. Confirm the intended speaker is used.
3. Turn each type off and test its Preview button; then turn master sound off and
   test all three. Confirm visual alerts/history still work while muted.
4. In an approved test environment with test students, confirm one sound for a
   correct uniform, an accepted violation, and an active suspension. Test correct
   uniform plus suspension together: only suspension should sound. Verify a future,
   expired or lifted suspension does not produce the suspension alert.
5. Check unknown identity, ambiguous matches, obscured torso, unavailable/disabled
   uniform model and pending evidence: none should produce a positive chime.
   Check that existing unknown alerts remain separate.
6. Hold a student in view, then introduce a higher-priority accepted condition.
   Check no frame-by-frame repetition and prompt priority handling. Try several
   test people: clips should remain serial, and delayed positives should disappear.
7. Queue several previews/detections, then stop monitoring, switch cameras, log out
   and close the app. Confirm pending detection audio does not carry into the next
   session. A clip already sent to the OS may have up to a polling interval plus
   device buffering before cancellation is audible.
8. With an intentionally unavailable output device, verify a useful audio warning,
   responsive preview, and intact visual history. Restore the device and use
   Preview to retry. Do not use real disciplinary records for deployment testing.
