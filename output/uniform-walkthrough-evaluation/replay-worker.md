# Walkthrough replay scorer worker evidence

Implemented independently labeled all-passage scoring in `core/uniform_walkthrough.py` and connected it to paced replay in `scripts/replay_camera_assessment.py`. Ground-truth rows are validated and joined to detections by face-box IoU (0.25); assessment outputs are associated by presence ID from the tracked `result.task.observations`, never by list position. Correct recognition requires a correct own-torso association and a matching garment label before the explicit annotated exit. Neighbor torso matches, mismatches, unscorable associations, missed passages, late results, policy truth, and the fixed 500 ms torso trial are reported separately. Missing required labels/geometry and passages incomplete at a frame cutoff prevent an accuracy estimate. History is capped at 10,000 rows.

Replay now rejects missing or invalid reported FPS instead of guessing a cadence, reports video FPS/raster size and measured paced decode cadence separately from physical camera FPS, tracks only passages encountered within the processed frame interval, and emits walkthrough results separately from legacy replay metrics. Reports include input/config/model hashes, actual face providers and body backend, and the disabled Stage 5 logo-verification call count (zero because no executor exists). Annotation JSONL requires unique nonnegative frame indices and object rows.

## Verification

- `.venv/bin/python -m unittest tests.test_uniform_walkthrough tests.test_camera_replay -v` — **23 passed** (14 walkthrough, 9 existing replay regressions).
- `.venv/bin/python -m py_compile core/uniform_walkthrough.py scripts/replay_camera_assessment.py` — passed.
- `.venv/bin/python scripts/replay_camera_assessment.py --help` — passed; CLI options render without model inference.
- `git diff --check -- core/uniform_walkthrough.py scripts/replay_camera_assessment.py tests/test_uniform_walkthrough.py tests/test_camera_replay.py` — passed.

Full command output is retained in `replay-tests.log`. No footage, model, camera, or evaluation database was opened for this work. Accuracy and physical capture performance remain unmeasured because no footage was provided.

## Source SHA-256

- `core/uniform_walkthrough.py`: `faa011cb44c38134bc8b480028d35bdeffb3d7cd9ce63d66a1224e1282ab56ff`
- `scripts/replay_camera_assessment.py`: `3cd33938a03f0c863442444864dc9bc813d6b208d8af5fee7431f3759d6c9ac1`
- `tests/test_uniform_walkthrough.py`: `2dce94266f57996334917542f04767452221817b5b9706ac174566bea0af8a89`
- `replay-tests.log`: `33eb8c4e85006739250b04c1a7d7905e6b3f23a5d002f8845cdd4acaa848bf1c`
