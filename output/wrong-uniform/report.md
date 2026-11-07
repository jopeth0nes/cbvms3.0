# Wrong Uniform gallery and Earring Check retirement

Completed 2026-11-07 (Asia/Manila), on main; no commit/push, full training or activation.

Recovered the Nov6 implementation and 114 passing live/startup/state/persistence/dashboard integration tests. Finished import progress reporting, pre-run approved counts/prerequisites, display compatibility and native workflow verification. Earlier Stage1–5 and scrolling edits remain intact.

## Changes
- Wrong Uniform reuses `other_clothing`, class2, mapping `uniform-garments-v1`. Gallery/reviewer/capture/training/evaluation/live display terminology is consistent; immutable old artifacts remain readable.
- Upload Photos, Import Folder, Import ZIP and cached-camera capture remain available. Imports stay pending. Explicit category/crop/quality review allows approval, exclusion or relabeling to Campus Uniform/Organizational Shirt. Clothing category is separate from day-based policy.
- Import reports remain visible after refresh and state imported, duplicate and unprocessed counts. Errors identify observed/configured limits. Existing limits remain:500 files/archive entries,200 MiB total/expanded/compressed archive,20 MiB per file, ratio200; raster/path/encryption/symlink checks retained. Oversized compressed ZIPs are rejected before opening, so remaining count is explicitly unknown. A retry of the same photos does not inflate unique counts. The original screenshot's generic error cannot identify retrospectively which of its two limits was exceeded without the original selected input batch.
- Candidate window shows all three approved/eligible counts and specific missing data or local-weight prerequisites. Existing shared trainer remains experimental train-only. Independent evaluation rejects training hash/group/near-duplicate overlap. No policy labels are inferred from gallery labels.
- Earring Training tab/actions/settings toggles removed/retired. Old tab selections open Uniform Check. No earring model startup/readiness/inference/confirmation/new DB alert/audio. Shared face, torso, uniform, logo/reference code, historical records and datasets preserved.

## Verification
- Reused114 runtime tests (`live-tests.log`); no unnecessary full rerun.
- 62 data/candidate/evaluation checks passed after correcting one stale message assertion (`data-final-tests.log`, `data-correction-tests.log`); one added missing-base-weight check passed (`prerequisite-test.log`).
- 24 native Tk checks passed after correcting two identified failures (`native-final-tests.log`, `native-correction-tests.log`); two final reviewer/readiness checks passed (`final-reviewer-tests.log`). Original failed logs retained. Full dashboard1280×800 layout, navigation, all four controls, generated TouchpadScroll, both existing clothing categories, DYCI references and saved-tab fallback verified.
- 31 audio tests passed (`audio-final-tests.log`), including silent old earring results.
- Two-process real native TrainingPanel workflow passed (`restart-review.log`, `restart-reopen.log`): folder import3; crop preview and approve1; exclude1; pending1; retry0 new/3 duplicates; restart preserves decisions/crop.29 preapproved synthetic fixtures supplied unchanged minimum coverage. Actual frozen manifest has30 approved entries, class2/train for the newly approved Wrong Uniform; pending/excluded absent. Retained `verified-dataset-manifest.json`, `verified-class-map.json`, `restart-workflow-result.json`. No training worker launched; base file was a fixture placeholder used only for manifest freezing.
- Nov6 actual disposable dashboard reached MacBook camera STREAMING and face/recognition/body/uniform READY with no earring registry/warning (`native-state.json`). Computer-control connection timed out; no manual visual/click or physical two-finger gesture claim. ONNX errors appeared at forced process shutdown, so no sustained inference/performance claim. Reused startup evidence without opening the physical camera again.
- 2751 protected dataset/model/catalog files unchanged; no missing files or unrelated tracked changes (`preservation-final.json`). Compile and diff checks pass; scoped reverse patch checked, never applied. Final source pins in `source-sha256.json`.

## Real-data readiness
976 real photos remain pending:6 Campus Uniform,43 Organizational Shirt,927 unassigned legacy. Zero approved/excluded and zero Wrong Uniform. Real data is insufficient to train; no real approval or model change was made.

## Exact clicks
1. Sign in with the existing authorized superadmin account. Click **Training → Uniform Check → Wrong Uniform**.
2. Click **Upload Photos**, **Import Folder**, or **Import ZIP**, and choose your images. For camera photos, use **Capture from Camera**, capture/review the buffer, then **Save changes**; they enter the selected gallery pending review.
3. Click **Review original & crop**. Inspect the original, set/drag the torso crop, and click **Preview corrected crop**. Correct the garment category if needed. Review warnings and check **I checked the category, crop, association and quality warnings.** Click **Approve example** only when justified. Otherwise use **Save pending review** or **Exclude example**.
4. Review eligible photos in all three galleries. Click **Train candidate with approved datasets**. Click **Refresh**, inspect approved/eligible counts and resolve the named prerequisites (minimum10 eligible examples/category).
5. Choose the bounded epoch/batch/thread options, acknowledge CPU contention, then click **Train candidate with approved datasets** in the dialog. Review the candidate in **Evaluation & Models**. Training keeps the active model unchanged; activation remains an authorized review action.

No owned app/test/training process remains. The unrelated recovery relay is untouched. Physical manual scrolling/visual acceptance remains a user check.
