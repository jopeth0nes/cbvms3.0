# Stage 5 result — 2026-11-05

**Verified: annotation/configuration support and software contracts for the insufficient-data path.**
**Blocked: trained logo verification, enablement and meaningful paired evaluation.**
The application remains torso-only. Logo status is **Not trained · Disabled**. No training,
model activation, production-record writes, commit or push occurred.

Repository: `/Users/jopethones/Documents/GitHub/cbvms3.0`; branch `main`;
HEAD `ea997f42d5b75b12e58787f1b2691461256a5589`. Existing dirty work was preserved.

## Implemented

- Extended the existing dataset/review UI with original-pixel printed-logo boxes,
  five visibility states, native crop previews, source type and independent logo/torso
  association confirmation. Versioned provenance/history ties the annotation to the
  original hash, exact torso, garment category and capture groups. Stale review cannot
  supply eligible logo pixels. Hidden/unreadable never becomes absent.
- Reused the separate reference-art library. Artwork cannot become approved garment
  training data through an unconfirmed pending-source change. No OCR was added.
- Added explicit disabled observation configuration and status. Enablement fails before
  model loading because no trained/evaluated backend exists. Torso/policy results are
  unchanged by missing logo evidence.
- Added a one-slot request/deadline guard for a future backend, with same-ticket freshness,
  original-resolution association and no waiting queue/cache. It is not wired to a live
  logo model and does not terminate blocking inference; that executor remains future work.
- Added paired-export comparison accounting and CLI. It checks identical footage, frames,
  annotation manifest, torso model/preprocessing, policy, execution conditions and explicit
  criteria. Classification errors, policy truth, missed/unassessed cases and paired signed
  latency remain separate. Output never authorizes activation or certifies independence.

See [workflow and collection requirements](../../docs/UNIFORM_LOGO_WORKFLOW.md).

## Verification evidence

All test commands used `.venv/bin/python -m unittest ... -v` through the recorded bounded
wrapper. Exact commands, PIDs, start/completion times and exit codes are in the adjacent
`*-process.json` receipts. Every owned check has exited; the unrelated recovery relay
remains untouched (`final-processes.json`).

| Evidence | Result | Scope |
|---|---:|---|
| `final-data-report-tests.log` | 45 passed | `tests.test_uniform_logo_annotations`, `tests.test_uniform_dataset`, `tests.test_uniform_logo_evaluation` |
| `final-observation-tests.log` | 28 passed | `tests.test_uniform_logo`, `tests.test_uniform_observation`, `tests.test_uniform_latency` |
| `pipeline-camera-regressions.log` | 59 passed | `tests.test_live_pipeline`, `tests.test_camera_switching`, `tests.test_live_persistence` |
| `logo-native-ui.log` | 29 passed | `tests.test_uniform_logo_ui`, `tests.test_uniform_workspace`, `tests.test_uniform_models_ui`, `tests.test_uniform_observation_ui`, `tests.test_scrolling` |
| `final-logo-native.log` | 4 passed | New native reviewer suite repeated after final annotation safety changes; included in the 29 above, not extra independent cases |
| `preservation-verified.log` | passed | Protected files, source pins and unrelated diffs |

The builder's earlier 36-test result and initial 34-test run are retained for recovery;
affected final revisions were tested as above. Final source hashes are in `source-sha256.json`.
`git diff --check`, comparator CLI help, and `git apply --reverse --check
output/uniform-stage5/stage5.patch` passed. The rollback patch was checked, not applied.

The first preservation run failed because the audit omitted the intentionally edited
artwork text in `ui/training_panel.py` from its source-change list. This was an audit-scope
error, corrected explicitly; the initial log remains. Final checks found:

- 1,772 protected dataset/model files and all 7 active artifacts unchanged.
- Catalog SHA256 unchanged: `1e96842d40ab26b65d84650672ae1cb93c6512a2864d745d0e27f0055d205d8d`.
- 18 untouched scrolling files, 12 unaffected Stage 3 source pins, 15 unaffected Stage 4
  source pins and unrelated tracked diffs unchanged.
- No production active pointer; no real candidate bundles. The retained synthetic Stage 2
  candidate SHA256 remains `4eda5fd372adbd80749553f849bc155a3fc4873fa2b660774d413a26bdaaf65a`.

## Data, models and unverified work

`input-audit.json`: 963 pending originals (36 Organizational Shirt, 927 unassigned legacy),
0 approved, 0 logo boxes, all legacy visibility unknown, no reference library files.
The original ZIP and original images were not reimported or altered. Dataset preprocessing
remains `uniform-stage1-raster-v1`; logo annotations use `printed-logo-v1`.

Active artifacts remain under `models/`; the active classifier and prior bundle versions
are recorded in the retained checkpoint. No new logo weights, training manifest or logo
model version exists. Stage 4's candidate preprocessing remains fixed at its existing
version; no smaller-input experiment or CPU/MPS benchmark was repeated.

Software fixtures test contracts/accounting, not garment/logo accuracy or measured logo
latency. Native Tk checks use generated images and events, not physical trackpad fingers
or a camera. No physical-camera session or actual torso-versus-logo comparison was run.
The 100 ms scheduling budget is an operational bound for admission/acceptance of future
results, not an observed inference time or acceptance criterion.

## Manual acceptance and exact next action

1. In Training → Uniform Check → Organizational Shirt, open a real example and review
   its torso/person/category. Draw a logo box on the original and inspect its preview.
2. Set visibility and source honestly; confirm same-torso association and logo review
   separately. Save pending when uncertain. Approve only after garment/quality review;
   verify hidden/unreadable cases do not acquire an absent/negative label.
3. Reopen to check saved coordinates/source, scroll the reviewer and close it during a
   preview. Evaluation & Models should still say **Not trained · Disabled**.

**Next action:** manually review photographed white-print examples and their capture-group
provenance in all garment categories, then collect independent held-out footage and define
classification/policy/unassessed/latency acceptance criteria before any future training or
enablement. Include shared crests on Campus Uniform/Other Clothing and obscured, too-small,
back-view and uncertain cases. The missing colored-reference path is optional artwork input,
not a substitute for these photographs. No automatic next stage is started.

Rollback: review `stage5.patch` against `before/`, stop any app using these modules, and
reverse only Stage 5 hunks/new files if desired. Check the reverse patch again if files have
changed. Preserve checkpoint/evidence, data, previous stage work and model pointers; never
reset/clean or copy whole before-files over newer edits.
