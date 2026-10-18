# Front Gate startup corruption — 2026-10-19 (Asia/Manila)

Changes installed locally; no commit/push. Initial working tree was clean.
Production records, models, credentials and camera settings were not changed.

## Confirmed findings

Read the worker, network capture, camera/IP entry points, saved-camera resolution,
configuration, dashboard lifecycle/analysis path, renderer and regression tests.
The worker forced `fflags;nobuffer|flags;low_delay`. Its framed pipe reads complete
payloads and its bounded latest-only queue drops decoded images, not compressed
reference packets. Dashboard switching already uses cancellation, source tokens,
generation checks and separate model workers. These mechanisms remain unchanged.

Actual saved Front Gate credentials resolved through Keychain outside the sandbox.
The camera reports HEVC/H.265, 1920x1080, 20 FPS; TCP was requested in all attempts.
Three sequential baseline connections followed by three conservative connections
used the same saved URL/profile/transport, eight seconds of reads per attempt, and
contact sheets sampled approximately every 0.5 seconds including the first read.
No GUI, recognition, evidence persistence or production database was involved.

| Attempt | Before first decoded delivery | Before first clean sampled image | After first decoded delivery | After first clean sampled image |
| --- | --- | --- | --- | --- |
| 1 | 2.028 s | None through 9.968 s from open | 1.580 s | 1.581 s |
| 2 | 1.976 s | None through 9.921 s from open | 1.460 s | 1.460 s |
| 3 | 2.009 s | None through 9.912 s from open | 1.482 s | 1.483 s |

All baseline contact-sheet images reproduced the gray/block artifacts in the user
screenshot. Each baseline attempt logged one recognized missing-reference warning.
All updated sampled images showed a coherent full scene, starting with the first
read; no recognized missing-reference/decode-error warnings occurred. Both versions
delivered about 20 FPS. All six decoder processes were reaped and pipe threads stopped.
Maximum observed post-decode delivery age was 16.14 ms before and 71.82 ms after
(two after attempts were below 16.34 ms). This measures application delivery only,
not camera-to-screen latency or compressed-stream buffering. Images were inspected
as contact sheets, not every original-resolution frame or an actual Tk preview.

## Interpretation and limits

The combined option change reproducibly improved this physical HEVC stream. Missing
reference warnings and corruption upstream of Tk support a decoder startup/reference
problem rather than a renderer defect. The experiment did not isolate `nobuffer`
from `low_delay`, measure keyframe interval, inspect packets, or rule out every
encoder/network contribution. No camera-side change is proposed without measurement.

[FFmpeg documents](https://www.ffmpeg.org/ffmpeg-formats.html#Format-Options)
`discardcorrupt` as discarding corrupt packets. It does not certify decoded images.
OpenCV exposes no reliable per-frame corruption flag here. No guessed image-quality
filter, arbitrary startup delay, fixed-frame discard, new decoder dependency, or
suppression based on asynchronous stderr was introduced. Future corrupt frames that
OpenCV reports as successful reads cannot be reliably excluded from analysis with
this API. The diagnostic warning counters are aggregate clues, not frame metadata.
The UI retains its bounded connecting deadline and activates on decoded pixels;
there is no additional hidden synchronization state or claimed quality certification.

## Files

- `core/network_camera_worker.py`: replace aggressive overrides with
  `fflags;discardcorrupt`, retain transport and normal reference decoding. Keep
  freshness enforcement after decoding and existing process cancellation.
- `scripts/diagnose_camera_startup.py`: repeatable saved-camera audit; sanitized
  metadata/warning counts, timing, cleanup; optional explicitly requested contact
  sheets. It does not open models or a database, or change saved preferences.
- `tests/test_network_camera_worker.py`: real worker/pipe with instrumented decoder
  verifies conservative options, TCP/UDP preservation, unchanged gray/black pixels,
  and cleanup. This is synthetic policy coverage, not a HEVC bitstream simulation.
- `diagnostics/camera-startup/{before,after}.jsonl`: sanitized physical measurements.
  Private camera pixels remain only in `/tmp/cbvms-startup-{before,after}/`.

## Validation

- 71 tests passed: `tests.test_network_camera_worker`, `tests.test_network_capture`,
  `tests.test_camera_activation`, `tests.test_camera_switching`,
  `tests.test_live_state`, `tests.test_live_model_safety`.
- 3 tests passed: `tests.test_network_capture_integration` (real local MJPEG and
  stalled RTSP/cancellation/parent EOF) and `tests.test_camera_transition_native_ui`
  (native Tk with simulated cameras/disposable DB). Native cancellation: 21.36 ms.
- 26 tests passed: `tests.test_live_pipeline` (synthetic orchestration and persistence
  doubles, including cancellation/stale work). Total: 100 passing tests.
- Actual HEVC coverage is the six physical connections above, not the MJPEG tests.
- Additional 30-second physical stream check: 604 reads, 20.003 reported delivery
  FPS, first decoded delivery 1.575 s, maximum post-decode delivery age 18.42 ms,
  zero recognized decoder warnings, decoder reaped and pipe threads stopped.
  No images were saved in this sustained check; it is not continuous visual QA.
- Physical-stream lifecycle check: terminating our own decoder caused its cached
  frame to be rejected as `stalled`; a fresh connection returned a frame in 1.549 s.
  Cancellation during another real connection stopped the owner and reaped its
  decoder in 13.49 ms. This simulates decoder loss, not a physical cable outage.
  Sanitized results: `diagnostics/camera-startup/{sustained.jsonl,lifecycle.json}`.
- Hardware GUI attempt: `scripts/verify_camera_switch.py --output-dir
  /tmp/cbvms-startup-switch-after` stalled in `tkinter.Tk.__init__` before the camera
  or model workers were started. Watchdog captured three matching stacks; stopped
  our diagnostic with SIGTERM. This does not establish a camera-switch regression
  or successful hardware GUI test. Evidence remains in that temporary directory.
  A sequential retry after the native test completed, with a PTY, also stalled at
  the same Tk initializer and was stopped. Its evidence is in
  `/tmp/cbvms-startup-switch-sequential/`.

## Remaining physical acceptance / launch

Close the previous app, then from the repository run `.venv/bin/python main.py`.
In Live Monitor select MacBook Camera → front gate → MacBook Camera → front gate.
Confirm the first Front Gate image is clean and the clock/controls stay responsive;
with a person in view, confirm analysis resumes. Actual enrolled-person recognition,
physical dropdown interaction and unplug/replug recovery have not been verified.

For a disposable-database GUI check instead, run
`.venv/bin/python scripts/verify_camera_switch.py --manual` from the user's Terminal.
For another read-only stream audit, choose a new output directory and run
`.venv/bin/python scripts/diagnose_camera_startup.py --output-dir /tmp/cbvms-new-audit`.
Do not claim the old camera-freeze checkpoint's unresolved physical acceptance is
resolved by this startup decoder change.
