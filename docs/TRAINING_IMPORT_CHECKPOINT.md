# Training import checkpoint — Stages 1–4

Updated: 2026-11-15 (Asia/Manila).

**Stage 4 integrated verification is complete on native macOS, including a real one-epoch candidate run. Stages 1–3 are retained.** See the Stage 3 section at the end for current evidence and manual checks. Physical Windows verification remains. No commit was created; all changes are available for review. Earlier sections are historical checkpoints.

## Repository recovery

- Branch: `main`.
- Current/base commit: `526fda3b263b15613f59652c0e6443c80ddac812` (`train1`). This checkpoint describes uncommitted changes on that commit.
- Working tree was clean before this task. Recent commits inspected: `526fda3`, `8de9b67`, `8ce7549`, `d597094`, `decdffe`, `e6fdd7c`.
- Existing automatic import processing was recovered from `526fda3`, including pose-based torso crops, quality filtering, category assignment and related-image grouping. It remains the workflow.
- Both existing GitHub Desktop stashes were inspected by file list/stat; they concern camera and disciplinary work, not this change. Neither was applied or modified.
- Read `AGENTS.md` and `docs/CODEX_AI_WORKFLOW.md`. Ruflo/ToolSearch tools were not exposed by this session's tool catalog. Work stayed direct.

## Implementation and entry path

`TrainingPanel` → `UniformWorkspace` → `CandidateTrainingWindow` → `CandidateService.summary/start` → `validate_config` → background `_prepare_and_spawn` → `_freeze` → `_spawn` → `uniform_candidate_worker.train`.

1. Category upload/folder/ZIP/camera imports retain source-hash deduplication and schedule automatic processing. Quality failures and pending processing remain ineligible.
2. `NORMAL_TRAINING_MIN_PER_CLASS = 5` is the shared normal-training minimum. Dataset snapshot readiness, candidate summaries, new-job defaults, validation, UI text/config and default split allocation use it.
3. `eligible_training_records` selects eligible unique source IDs for dataset counts, candidate counts and freeze. Source IDs are SHA256 hashes. Repeated source occurrences count once; near duplicates remain useful images with existing related-group constraints.
4. Freeze verifies original bytes/hashes, crops, paths and immutable inputs. Holdouts must leave the job's configured minimum in each training category; otherwise the existing all-training fallback applies. Independent evaluation/activation requirements are unchanged.
5. Resume verifies the existing bundle/checkpoint and loads its original `config.json` and manifest. It does not validate against new defaults, refreeze, or replace an explicit stored minimum such as 10. The worker reads that frozen configuration directly.

## Changed files

- `core/uniform_candidate.py`: shared minimum, shared unique-record selection, consistent counts, configured split floor and updated readiness messages.
- `core/uniform_dataset.py`: snapshot readiness/counts use the shared minimum and selection.
- `ui/uniform_candidate_panel.py`: five-photo requirement, unique-photo labels and new-job configuration.
- `tests/test_training_import_minimum.py`: disposable imports → automatic crop processing → eligibility → freeze/launch, duplicate/state exclusions, holdouts and frozen-config resume.
- `tests/test_uniform_candidate.py`: five-photo config and unique-count boundary regressions.
- `tests/test_uniform_candidate_ui.py`: five-photo launch/config expectations.
- `tests/test_uniform_dataset.py`: updated readiness expectations.
- `tests/test_uniform_workspace.py`: updated category counts and requirement text.
- `docs/TRAINING_IMPORT_CHECKPOINT.md`: this checkpoint.

## Verification commands and results

All dataset and candidate writes used temporary directories. No live database/dataset was used. New integration fixtures run real import, quality measurement, crop writing and freeze, with deterministic synthetic images and mocked person/pose detection. Their subprocess launch is mocked; the existing suite separately exercises a real CPU training subprocess and a real background processing subprocess.

Initial regression command, before the implementation:

```sh
.venv/bin/python -m unittest tests.test_uniform_candidate.CandidateSummaryTests.test_normal_minimum_is_five_and_explicit_minimums_are_preserved tests.test_uniform_candidate.CandidateSummaryTests.test_unique_eligible_counts_agree_at_five_photo_boundary -v
```

Result: expected failure (2 tests, 6 failing assertions/subtests): default was 10 and repeated rows inflated the training-ready display count.

After implementation:

```sh
.venv/bin/python -m unittest tests.test_uniform_candidate.CandidateSummaryTests -v
.venv/bin/python -m unittest tests.test_training_import_minimum -v
.venv/bin/python -m unittest tests.test_uniform_candidate tests.test_uniform_dataset -v > /private/tmp/cbvms-training-stage1-core.log 2>&1
.venv/bin/python -m unittest tests.test_uniform_candidate_ui tests.test_uniform_workspace -v > /private/tmp/cbvms-training-stage1-ui.log 2>&1
```

- Summary checks: 12 passed.
- New import minimum integration checks: 6 passed (4.687 s).
- Core/dataset suite: 66 of 68 passed initially. Two real subprocess checks aborted inside the sandbox; OpenMP reported `Can't open SHM` in the training worker log, and the background processor exited with SIGABRT.
- Native UI suite, with desktop permission: 18 passed (12.732 s), including off-Tk launch, original-config resume routing and workspace navigation/messages.

Only the two failed subprocess checks were rerun with permission outside the sandbox:

```sh
.venv/bin/python -m unittest tests.test_uniform_candidate.CandidateFreezeTests.test_real_cpu_worker_trains_validates_saves_best_and_keeps_active_weights tests.test_uniform_dataset.AutomaticProcessingTests.test_real_background_process_recalculates_correction_without_tk_or_approval -v > /private/tmp/cbvms-training-stage1-subprocess-rerun.log 2>&1
git diff --check
```

Result: both subprocess checks passed (11.299 s); diff check passed. Final coverage is **92 distinct passing tests**, reusing the valid 66 core results and rerunning only the two environment-blocked checks. The summary-only run is a subset and is not counted again.

Acceptance evidence:

| Case | Result |
| --- | --- |
| 4 eligible photos in any one required category | Dataset and service readiness false; start fails preparation without launching a worker |
| 5 eligible photos in all three categories | Readiness true; 15 entries frozen; normal worker launch receives the bundle with minimum 5 |
| 6 Campus / 38 Organizational / 7 Wrong Uniform | Readiness true; 51 entries frozen and checksum-verified |
| Queued, processing, failed or quarantined fifth photo | Eligible count remains 4 |
| Repeated identical files under different filenames | Imports report duplicates; four eligible photos remain four |
| Existing job with explicit minimum 10 | Resume retains byte-identical config/manifest, even after current catalog removal and original-weight removal |
| Related groups, crop checks and checkpoint integrity | Existing focused regressions pass |

## Exact manual confirmation with disposable data

From the repository root, open an isolated copy of the real training workspace:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import mkdtemp
from core.uniform_dataset import UniformDataset
from core.uniform_candidate import CandidateService
from ui.uniform_workspace import UniformWorkspace
from ui.window_lifecycle import WorkspaceWindow

scratch = Path(mkdtemp(prefix='cbvms-training-stage1-'))
print('Disposable workspace:', scratch, flush=True)
dataset = UniformDataset(scratch / 'dataset')
service = CandidateService(dataset, scratch / 'candidates')
root = WorkspaceWindow()
root.title('CBVMS Stage 1 — disposable training verification')
root.geometry('1150x850')
view = UniformWorkspace(root, capture=lambda category: None,
                        dataset=dataset, candidate_service=service)
view.pack(fill='both', expand=True)
root.mainloop()
PY
```

This workspace uses the installed local classifier/pose dependencies, real automatic processing and temporary catalog/candidate folders. Use the photo upload controls below; camera capture is not connected in this harness. The printed folder remains available for inspecting frozen artifacts after closing the window.

1. In **Campus Uniform**, **Organizational Shirt** and **Wrong Uniform**, use **Upload Photos** to add distinct, clear garment photographs. Wait for automatic processing; obtain four eligible unique photos in each category. Quarantined/failed images do not count, so replace them with additional suitable photographs if needed.
2. Click **Train Model with Training-Ready Photos**. Select the resource-contention acknowledgment. Confirm the per-category counts are four, the message says `requires 5`, and Start remains disabled.
3. Re-upload exact copies under different filenames. Wait/refresh; the eligible counts must remain four.
4. Add a fifth distinct eligible photograph to Campus Uniform and Organizational Shirt. Wrong Uniform at four must still block. Add a fifth distinct eligible photograph to Wrong Uniform, wait for processing, then refresh. All three counts must be five and the training action becomes enabled when the slot is free and the acknowledgment is selected.
5. For this import verification, stop at the enabled training action; do not start model training. The frozen five-photo configuration and existing-job resume behavior are covered by the disposable service tests.
6. To check the asymmetric case, add eligible photographs until the counts are 6, 38 and 7 respectively. Dataset eligibility must remain true. Pending/failed/quarantined additions must not increase the eligible counts.

The equivalent application navigation is **Training → Uniform Check → Train Model with Training-Ready Photos**. Use the isolated workspace above for verification rather than altering the live training catalog.

## Stage 1 status after recovery

The five-photo minimum and frozen-job configuration remain intact. The Stage 1 integration fixture now uses an ordered category tuple, so the 6/38/7 case explicitly means Campus/Organizational/Wrong Uniform rather than depending on frozenset iteration. No outstanding Stage 1 implementation defect was found. Synthetic checks do not measure real-photo pose accuracy or model accuracy.

## Stage 2 recovery and ownership

- Recovered on `main`, still at `526fda3b263b15613f59652c0e6443c80ddac812`; nothing staged. Recovery found **10 modified tracked files and 8 untracked files**, listed below. All earlier work was preserved.
- The delegated `crack_builder` stopped with a usage-limit error. Its partial implementation was recovered from disk. It was not restarted; the root session became the sole editor.
- Process inventory command: `ps -axo pid,ppid,lstart,command | rg 'uniform_import|uniform_processing|uniform_candidate|unittest|codex|pytest'`. The resumed scan found no import, processing, training or test process to recover. It showed longstanding Codex host processes started October 6, 25 and 27, not task workers; none were stopped or restarted. `ps` required read-only sandbox escalation.
- Recovered `/private/tmp/cbvms-stage2-recovery.log`: 22 passing checks. Recovered `/private/tmp/cbvms-stage2-native.log`: 23 tests with 5 failures/subtest failures. The initial claims described implemented interfaces, not a finished Stage 2: totals/remaining counts, discovery transitions and current filenames still needed fixes.

## Stage 2 final implementation

- All three real import controls start an independent background job after the picker returns. A global Training progress panel immediately starts an animated activity bar with Cancel. The panel survives category navigation.
- File/folder discovery reports growing candidate-file counts with an unknown total. Discovered paths are spooled with a 1 MiB memory threshold rather than retaining an arbitrarily large folder tree. ZIP discovery and complete safety preflight happen before saving archive images. Discovery, reads, hashing, decode and crop work stay off Tk.
- Once discovery completes, the panel switches to processed/total, percentage and current filename. Import counters distinguish newly saved unique photos, exact duplicate occurrences, skipped unsupported/symlink entries and failed files. Corrupt individual photos are counted and skipped in progress mode; archive-wide safety failures still abort before writes.
- Discovery callbacks are throttled to 100 ms, with first-discovery, phase/total, filename and final updates delivered when needed. Jobs retain one latest snapshot, not an accumulating event queue. Tk polls it every 80 ms; job catalog reads are limited to 200 ms during import/processing. Filename changes publish before slow work so the previous file is not left onscreen.
- The same job then tracks only its touched unique source IDs through automatic torso processing. Ready, quarantined, processing-failed, excluded and awaiting states are accounted for separately. Already-ready duplicates do not become newly queued photos. Excluded records count as terminal. A stopped processor reports retained pending photos and a retry message rather than waiting forever.
- Cancel stops discovery or import cooperatively at bounded read/file boundaries. Completed saves remain; an in-progress atomic save may finish. Previously saved photos continue automatic processing, and the panel waits for those outcomes. Cancel is disabled during the shared torso-processing phase. It does not stop another dataset user's shared processor.
- Jobs are scoped by dataset and UUID, independent of widgets. Closing the view requests import cancellation and cancels its Tk polling/animation timers; reopening within the application attaches the same active or latest job. Completed history is bounded. App-process restart does not restore the in-memory progress panel, but saved originals/catalog and resumable automatic processing remain on disk.
- Existing `BackgroundTask` remains responsible for gallery/status work and now explicitly cancels its pending poll when its owner is destroyed.
- `core/file_lock.py` uses POSIX flock or Windows byte-range locking. Catalog locks no longer silently omit cross-process exclusion on Windows; the processor no longer imports POSIX-only fcntl. The candidate module can be imported for readiness on Windows. Candidate training subprocess portability was not expanded or exercised in this import stage.
- Source-hash deduplication, crop quality/category checks, related-image grouping, ZIP traversal/symlink/encryption/ratio validation, decoded-image and byte/count limits, and five-photo eligibility remain effective. Duplicate-choice dialogs remain deferred to Stage 3.

## Full working-tree file list

Modified tracked files (includes preserved Stage 1 changes):

- `core/uniform_candidate.py` — Stage 1 minimum/counting plus portable lock use.
- `core/uniform_dataset.py` — discovery, progress, cancellation, partial accounting and portable catalog lock; Stage 1 readiness retained.
- `core/uniform_processing.py` — portable processor lock.
- `tests/test_uniform_candidate.py` — preserved Stage 1 regressions.
- `tests/test_uniform_candidate_ui.py` — preserved five-photo expectations.
- `tests/test_uniform_dataset.py` — preserved five-photo expectations.
- `tests/test_uniform_workspace.py` — await independent import completion in native fixtures.
- `ui/background_task.py` — owner-scoped timer cleanup.
- `ui/uniform_candidate_panel.py` — preserved Stage 1 messages/config.
- `ui/uniform_workspace.py` — wire import jobs, progress panel, navigation/reopen recovery.

New/untracked files:

- `core/file_lock.py` — portable lock adapter.
- `core/uniform_import.py` — background job and bounded snapshot registry.
- `ui/uniform_import_progress.py` — Tk progress/cancel/summary view.
- `tests/test_uniform_file_lock.py` — native process contention, exception release, Windows adapter simulations.
- `tests/test_uniform_import.py` — progress/cancellation/limits/processing failure regressions.
- `tests/test_uniform_import_ui.py` — actual native controls, animation, thread boundaries, cancellation, limits, navigation/reopen, retry and automatic crop progress.
- `tests/test_training_import_minimum.py` — Stage 1 disposable acceptance tests with explicit category order.
- `docs/TRAINING_IMPORT_CHECKPOINT.md` — this record.

## Stage 2 commands and evidence

All verification wrote disposable datasets/candidate fixtures. The resumed work did not run model training, activate a candidate, benchmark a camera, or edit live photos/database/jobs. Existing passing results were reused where the exercised code remained applicable.

Recovery command from the interrupted session:

```sh
.venv/bin/python -m unittest tests.test_uniform_file_lock tests.test_training_import_minimum tests.test_uniform_candidate.CandidateSummaryTests tests.test_uniform_candidate.CandidateFreezeTests.test_real_child_inherited_flock_blocks_competitor_and_releases_on_exit -v > /private/tmp/cbvms-stage2-recovery.log 2>&1
```

Result: **22 passed**. Reused summary/minimum/inherited-lock evidence; the lock suite was later extended and rerun.

Resumed checks, in order:

```sh
.venv/bin/python -m unittest tests.test_uniform_import tests.test_training_import_minimum -v > /private/tmp/cbvms-stage2-core-resume.log 2>&1
.venv/bin/python -m unittest tests.test_uniform_import_ui tests.test_uniform_workspace -v > /private/tmp/cbvms-stage2-native-resume.log 2>&1
.venv/bin/python -m unittest tests.test_uniform_import tests.test_uniform_file_lock -v > /private/tmp/cbvms-stage2-core-final.log 2>&1
.venv/bin/python -m unittest tests.test_uniform_dataset -v > /private/tmp/cbvms-stage2-dataset-final.log 2>&1
.venv/bin/python -m unittest tests.test_uniform_import_ui.UniformImportUITests.test_discovery_read_hash_and_tk_delivery_thread_boundaries tests.test_uniform_import_ui.UniformImportUITests.test_real_upload_folder_zip_controls_animate_and_report_counts -v > /private/tmp/cbvms-stage2-native-final.log 2>&1
.venv/bin/python -m unittest tests.test_uniform_import_ui.UniformImportUITests.test_real_upload_folder_zip_controls_animate_and_report_counts -v > /private/tmp/cbvms-stage2-filename-rerun.log 2>&1
.venv/bin/python -m unittest tests.test_uniform_import -v > /private/tmp/cbvms-stage2-core-affected.log 2>&1
git diff --check
```

| Log/check | Result and interpretation |
| --- | --- |
| `core-resume` | 11 run; all 6 Stage 1 tests passed. Two new import-fixture assertions needed correction: cancellation cannot depend on receiving every throttled callback, and one pending photo has processing total 1, not 0. Fixtures corrected without weakening acceptance. |
| `native-resume` | **18 passed**, 15.307 s: seven import-control checks plus eleven workspace checks. Supplied picker selections exercise real native buttons/widgets with actual disposable file/folder/ZIP reads. |
| `core-final` | **12 passed**, 0.260 s: eight import/coordinator checks and four lock checks. Includes stopped processor/scheduling exception, bounded progress, hostile ZIP, retry, separate-process contention and exception release. |
| `dataset-final` | **39 passed**, 1.206 s, outside sandbox: existing import/ZIP/quality/grouping regressions and the real automatic-processing subprocess. No training. |
| `native-final` | Thread-boundary check passed. Added current-filename assertion exposed stale `notes.txt` while a photo save was paused; downstream route failures followed the interrupted fixture. Fixed filename transitions to publish before slow work. |
| `filename-rerun` | **1 passed**, 1.980 s; parameterized actual Upload Photos, Import Folder and Import ZIP controls, immediate/running animation, correct current filename, final unique/duplicate/skipped/failed/awaiting counts. |
| `core-affected` | **8 passed**, 0.166 s after the filename-publication fix. Reused unaffected native lifecycle, dataset, lock and Stage 1 results. |
| `git diff --check` | Passed. |

The earlier seven candidate UI tests in `cbvms-stage2-native.log` also passed and their code remains unchanged; they were not rerun. Tests above overlap; do not add run counts as distinct tests.

Coverage against Stage 2 acceptance:

1. Real native button invocations immediately start animation after supplied picker results; animation visibly advances while a worker save is gated.
2. Folder discovery reports a positive unknown-total count with indeterminate mode and supports cancellation. ZIP discovery also supports cancellation; known file totals, percentage and filenames are checked across all three controls.
3. A fixture of five distinct photos, one exact copy, one corrupt PNG and one text file yields 5 new, 1 duplicate, 1 failed and 1 skipped on first import; subsequent folder/ZIP retries yield 0 new and 6 duplicate occurrences. Awaiting count uses five unique photos.
4. A real crop/quality-processing worker with a deterministic detector fixture transitions from 5 awaiting to 5 training-ready while the animation stays active. The separate existing subprocess test verifies automatic processing using the installed runtime.
5. Cancel after the first save retains one photo and reports four unprocessed; retry saves four and counts one duplicate. Count limit 2 on five files retains two and reports three unprocessed; retry deduplicates those two.
6. Category navigation, view destruction/reopening, corrupt archive, partial limit and retry preserve job identity and discard old callbacks. Poll/animation timers stop. Completed summary remains visible.
7. Native instrumentation confirms `os.scandir`, `Path.open` and SHA256 calls run off the main thread, and progress rendering runs on the main thread.
8. Separate native processes contend on a held lock and acquire it after release. Catalog and processor exceptions release their locks. Simulated Windows locking checks cover first-byte selection, busy retry, nonblocking errors and unlock.
9. Stage 1 4/5/6-38-7, duplicate and frozen-resume checks pass; source processing, category and quality regressions pass.

## Platform coverage and remaining limits

- Actual test platform: **macOS 26.5 (25F71), Apple Silicon arm64**, native Tk/CustomTkinter. Native tests required desktop permission; the real processing subprocess required shared-memory access outside the sandbox.
- File-picker return values were supplied by tests, while buttons, widgets, event loops, animation, files/folders/ZIPs and background workers were real. Native picker interaction itself remains in the manual steps below.
- Windows byte-lock calls were **simulated on macOS**, not executed on physical Windows. A physical Windows smoke of native pickers, cancellation/navigation, filesystem locking and automatic subprocess completion remains necessary before claiming Windows runtime coverage.
- No unresolved Stage 2 implementation/test failure is known. No model accuracy, camera performance or Windows hardware behavior is inferred from synthetic tests.

## Exact next action: manual Stage 2 smoke, then review the diff

Use the disposable launcher in the Stage 1 section for photo/folder/ZIP tests. For a close/reopen test in the same process, replace its final `view = ...` / `view.pack(...)` lines with:

```python
import customtkinter as ctk
holder = {'view': None}
def close_training():
    if holder['view'] is not None:
        holder['view'].destroy()
        holder['view'] = None
def open_training():
    if holder['view'] is None:
        holder['view'] = UniformWorkspace(root, capture=lambda category: None,
                                         dataset=dataset, candidate_service=service)
        holder['view'].pack(fill='both', expand=True)
controls = ctk.CTkFrame(root)
controls.pack(fill='x')
ctk.CTkButton(controls, text='Close Training view', command=close_training).pack(side='left')
ctk.CTkButton(controls, text='Open Training view', command=open_training).pack(side='left')
open_training()
```

Keep `root.mainloop()` after that code. On Windows, save the launcher as a temporary `.py` file and run it with the checkout's `.venv\Scripts\python.exe`; on macOS use `.venv/bin/python` as shown above.

1. Prepare a temporary folder of non-sensitive test photographs, an exact copy under another filename, a text file, and a `.png` containing invalid bytes. ZIP that folder too. Use copied photos only; keep the originals untouched.
2. Click **Upload Photos**, select the test images in the native picker, and confirm animation and Cancel appear on return. Watch processed/total, percentage and the current filename. The invalid PNG must count as failed and the exact copy as a duplicate.
3. Click **Import Folder**, select the temporary folder, then repeat with **Import ZIP**. Watch “Finding photos…” before the known total. The text file must count as skipped. Reimported originals must count as duplicates without increasing the unique catalog count. Small discovery phases may finish too quickly to see; a larger temporary folder makes the transition visible.
4. After file import finishes, confirm **Processing torso crops…** remains visible while photos are queued/processing. Wait for training-ready, quarantined or processing-failed outcomes and an accurate final summary.
5. Start a sufficiently large disposable import and click **Cancel import** after a few saves. Confirm saved photos remain, unprocessed counts are reported (unknown if discovery was cancelled), and their torso processing continues. Retry the same selection and confirm saved photos are duplicates.
6. During an import, switch category tabs; progress must still name the original category. Use **Close Training view**, then **Open Training view**: the same job/partial result must return without old messages overwriting it. A corrupt ZIP must stop with an error and permit a subsequent valid import.
7. For the file-count limit, select more than 500 small test image files: the summary must retain the first bounded batch and report the rest. Retry the remaining files in a smaller selection. Oversized/unsafe ZIPs should be rejected before saving any entry.
8. Confirm five eligible unique photos per category enable normal training eligibility, but **do not start training** for this smoke. Review `git diff` and the untracked files. Leave duplicate-choice dialogs for Stage 3.


## Stage 3 — consolidated duplicate decisions (2026-11-10)

Base remains `main` at `526fda3b263b15613f59652c0e6443c80ddac812` (`train1`); no staged changes or new commit. Existing Stage 1/2 edits were preserved. Read AGENTS.md, this checkpoint, actual tracked/untracked code, import/processing/progress paths and test fixtures before editing. Ruflo tools were not exposed. One existing Codex on Crack worker owned only the new duplicate-review view and its tests; root owned core integration, workspace correction, other tests and final review. No live photos, database, jobs, active model, camera or training run were used.

### Implementation

- `core/uniform_duplicates.py` (new): bounded disposable staging reuses existing path/folder/ZIP discovery, byte/image bounds and complete ZIP validation. SHA256 exact and shared dHash similarity detection cover existing catalog plus the current batch. Groups carry incoming occurrences, sources, categories and matching previews. Staged bytes are removed on completion/cancel. Imports wait for one consolidated decision set before catalog writes.
- `core/uniform_import.py`: window-independent review rows/choices, match-type application, cooperative waiting/cancel, actual save counters, source-reference counters and unchanged automatic processing monitoring. A failed processor launch still counts a completed save. Temporary cleanup failure cannot leave a job active forever.
- `core/uniform_dataset.py`: optional reviewed-save policy rechecks exact category/existence and newly appearing similarity matches under the catalog process lock. Unreviewed catalog changes fail the affected occurrence and require retry rather than silently applying an outdated choice. Legacy non-job import calls retain their compatibility behavior. Additional sources reuse the canonical SHA image and deduplicate stable source identity. Skipping performs no catalog mutation. Explicit category changes audit old/new category, invalidate eligibility/logo review and queue torso reprocessing while preserving exclusion, quality and artwork protections.
- `ui/uniform_duplicate_review.py` (new): grouped, paginated native review with worker-decoded thumbnails, match selection, requested defaults/options, category/source information, similarity-estimate explanation, apply-to-type and occurrence-weighted decision summary. Closing only this window preserves the waiting job/choices; the progress panel reopens it.
- `ui/uniform_import_progress.py`: checking/waiting states, reopen-review control, per-job window ownership and accurate duplicate/source/skip/failure summary. Existing animation, cancellation, file/torso separation and Tk polling continue.
- `ui/uniform_workspace.py`: existing inspection now permits marking a suggested pair as not visually related. This stores symmetric suppression, removes similarity edges and recomputes duplicate weights/groups; capture/provenance split isolation remains.
- New tests: `tests/test_uniform_duplicates.py`, `tests/test_uniform_duplicate_ui.py`. Updated staging hooks and wording in `tests/test_uniform_import.py`, `tests/test_uniform_import_ui.py`, `tests/test_uniform_workspace.py`. Earlier Stage 1/2 changed files listed above remain present.

### Focused check → fix → rerun evidence

All fixtures used temporary catalogs and images. Native picker return values were supplied by tests; real buttons, CustomTkinter widgets/event loops, file/folder/ZIP reads, animation and worker execution were exercised on macOS.

1. `.venv/bin/python -m unittest tests.test_uniform_duplicates tests.test_uniform_import tests.test_training_import_minimum` — initial 20-test check found two failures: staging copied an instance test hook and wrote prematurely, and an older job test needed to answer the newly introduced review. Fixed staging method isolation and updated the test interaction. Six Stage 1 integration tests passed in that run.
2. `.venv/bin/python -m unittest tests.test_uniform_duplicates tests.test_uniform_import` — **16 passed**, final `/private/tmp/cbvms-stage3-core-final.log`. Covers every decision, defaults, within-batch/existing matches, source-idempotent reimport, folder/ZIP, similar grouping and false-match correction, explicit category reprocessing, concurrent category mutation, partial failure/retry, cancellation before/after saves, launch failure accounting, and four eligible photos plus eight added source copies still counting as four.
3. `.venv/bin/python -m unittest tests.test_uniform_dataset tests.test_training_import_minimum` — **45 passed**, `/private/tmp/cbvms-stage3-dataset.log`. Reused unchanged passing paths after subsequent isolated reviewed-save launch-error handling. Includes five-per-category, 6/38/7, state exclusions, automatic processing and frozen configuration resume; no model training was run.
4. `.venv/bin/python -m unittest tests.test_uniform_import_ui tests.test_uniform_workspace` — initial **19 tests**, three test methods failed (four reported failures including folder and ZIP subcases). Found/fixed a real stale review window across successive jobs. Other failures were old import hooks and summary wording. Passing animation/thread-boundary/processing/cancel/navigation/inspection results retained. Log: `/private/tmp/cbvms-stage3-native.log`.
5. `.venv/bin/python -m unittest tests.test_uniform_import_ui.UniformImportUITests.test_real_upload_folder_zip_controls_animate_and_report_counts tests.test_uniform_workspace.UniformWorkspaceTests.test_import_and_decode_stay_off_tk_thread tests.test_uniform_workspace.UniformWorkspaceTests.test_limit_failure_shows_progress_and_refreshes_partial_gallery` — **3 passed**, including Upload/Folder/ZIP subcases; `/private/tmp/cbvms-stage3-native-rerun.log`.
6. `.venv/bin/python -m unittest tests.test_uniform_import_ui.UniformImportUITests.test_duplicate_wait_close_reopen_navigation_and_cancel` — **1 passed**, `/private/tmp/cbvms-stage3-wait-ui.log`. Actual workspace waiting state, review close/reopen, category navigation, cancel and unchanged existing catalog.
7. `.venv/bin/python -m unittest tests.test_uniform_import_ui.UniformImportUITests.test_folder_discovery_is_indeterminate_and_can_be_cancelled` — **1 passed** after final cancellation accounting change; `/private/tmp/cbvms-stage3-cancel-ui.log`.

### Platform limits and exact next action

- Native platform remains macOS 26.5 arm64. Windows behavior is implemented using the existing portable Stage 2 paths/locks, but **no physical Windows Stage 3 run occurred**. Stage 2 separate-process lock evidence remains valid; no lock implementation changed in Stage 3.
- Picker interaction itself and a physical Windows smoke remain manual. Jobs and pending choices survive view changes within the same process, not app termination. Closing the entire Training view cancels outstanding import work as in Stage 2; reopening recovers that job's partial result.
- If an expected within-batch canonical photo was skipped or failed, a later “Add source anyway” cannot attach to it; the occurrence fails safely and can be retried against the resulting catalog. Concurrent catalog changes likewise require a fresh import/review. Completed saves remain intact.

Use the disposable launcher already provided above, with copies of non-sensitive test photos. Do not use the live catalog or start training.

1. **Upload Photos**: choose a photo plus two byte-for-byte copies. Expect a single grouped exact-match review, default **Skip**, before saves. Continue: one unique photo is saved and duplicate occurrences are skipped.
2. Reimport copies via **Import Folder**, then **Import ZIP**. Choose **Add source anyway**, use **Apply to this match type**, and continue. Source references increase for new sources, unique eligibility stays unchanged. Repeat the identical import: no extra source references are added.
3. Import a similar but nonidentical photo. Default **Keep both** must keep separate records with related-image grouping. Retry another similar image with **Skip incoming**. For a false estimate, open **Inspect original & crop**, select **Not visually related…**, then **Save correction & reprocess**.
4. Import an exact photo under another category. Confirm both affected categories are displayed. Test **Skip incoming**, **Use existing category**, then **Change category for this image** on separate attempts. Only the explicit change relabels the canonical image and queues torso reprocessing.
5. Close the duplicate-review window while it waits, switch category tabs, then click **Review duplicates**. Choices should persist for the same job. Cancel: existing photos stay unchanged. Retry and continue; wait for torso-processing completion and final unique/duplicate/source/skipped/failed counts.
6. Include a corrupt PNG and unsupported text file. Exercise a bounded partial import and retry; completed saves must remain, duplicate defaults must reappear, and totals must agree. Review the diff/untracked files. Repeat steps 1–5 on physical Windows before claiming Windows runtime coverage. No additional feature stage is proposed.


## Stage 3 final recovery and verification — 2026-11-15

**Complete on available native macOS; physical Windows verification remains. Stage 4 has not started.** Current branch/base is still `main` / `526fda3b263b15613f59652c0e6443c80ddac812`. Nothing staged or committed. Existing Stage 1/2/3 working changes remain available for review.

### Recovery findings and final edits

- Re-read AGENTS.md, this checkpoint, actual code/diffs, recent commits and relevant untracked files. The prior `/root/import_core` worker was errored at the usage limit, not editing. Root completed the remaining small fixes directly.
- Inspected processes with `ps -axo pid,lstart,command`: no surviving test, uniform-import or processing worker. Existing Codex/IDE services were not touched. The unrelated recovery mail relay (PID 79265, command `scripts/recovery_mail_relay.py`, started October 27) was left running.
- The November 10 `/private/tmp/cbvms-stage3-*.log` files, including requested native/dataset logs, were absent. Their earlier checkpoint entries remain historical reports, not recovered log evidence. Focused current-code verification below regenerates that evidence. Existing staging-method isolation and different-job review-window disposal fixes were confirmed in code and tests.
- An interrupted UI edit had accidentally placed thumbnail decoding after `_source_name`'s return, so previews always returned `None`. Restored decoding in `_thumbnail`, off the Tk thread. Source labels now retain bounded folder/archive/member context and wrap; they no longer substitute staging paths for meaningful source information.
- Final UI tests operate real option-menu callbacks for every choice, toggle the actual apply-to-type checkbox, verify other visible choices refresh, select a match using its real menu callback, and verify worker decoding/UI-thread image attachment. Close/reopen test cleanup now tracks the replacement window. No core behavior required changes in this recovery turn.

### Current evidence (disposable data only)

Commands were run from the repository using `.venv/bin/python`; native GUI and the automatic processing subprocess ran with desktop/shared-memory access outside the sandbox. No training, camera benchmark, production photos, database, jobs or active model were used.

| Command | Result | Retained log |
| --- | --- | --- |
| `.venv/bin/python -m unittest tests.test_uniform_duplicates tests.test_uniform_import tests.test_uniform_dataset tests.test_training_import_minimum -v` | **61 passed**, 7.999 s | `output/training-import-stage3/2026-11-15/core.log` |
| `.venv/bin/python -m unittest tests.test_uniform_duplicate_ui -v` | Four passed; checkbox test used nonexistent `invoke()` and errored. Test corrected to the installed CTk checkbox's `toggle()` callback API. | `output/training-import-stage3/2026-11-15/review-ui.log` |
| `.venv/bin/python -m unittest tests.test_uniform_duplicate_ui.UniformDuplicateReviewUITests.test_defaults_choices_apply_type_and_occurrence_weighted_summary tests.test_uniform_import_ui tests.test_uniform_workspace -v` | **21 passed**, 20.537 s; includes the corrected test plus all 20 affected workspace/import tests | `output/training-import-stage3/2026-11-15/native.log` |
| `git diff --check` | Passed | No whitespace errors |

The four unaffected passing duplicate-window checks were reused; together the final native evidence covers **25 passing native test methods**, plus 61 core/dataset/minimum tests. No known failing Stage 3 check remains. Current tested file hashes are saved in `output/training-import-stage3/2026-11-15/source-sha256.json` so a later recovery can check whether results still match the code. The output directory is local evidence; the checkpoint records exact commands/results even if temporary logs expire.

Coverage confirms:

1. Upload, folder and ZIP controls stage/review existing and within-batch matches; exact default Skip and Add source anyway preserve one canonical image and idempotent provenance.
2. Similar default Keep both and Skip incoming, related-image groups/weights, and false-similarity inspection correction work. Category-conflict choices are shown before changes; only the explicit change relabels and queues reprocessing.
3. Save-time category/catalog rechecks reject outdated decisions. Partial failures and retry preserve completed saves. Four eligible photos plus eight additional exact sources still count as **four**. Five in each category, 6/38/7, excluded processing states and frozen job configuration checks pass.
4. Waiting review closes/reopens with choices retained; tab navigation, next-job window replacement, cancellation, limits and retry keep correct job state. File/progress counters and persisted results agree. Existing unfinished processing can resume even when an exact reimport is skipped, without adding a source.
5. Native animation continues during paused saves and automatic torso processing; scans, reads, hashes and thumbnail decoding run in workers, while widget/image updates run on Tk's thread. Destroyed windows stop their task timers and stale callbacks.

### Platform coverage, remaining verification, and Stage 4 handoff

Actual platform: **macOS 26.5 (25F71), arm64**. Real Tk controls/event loops/files and processing were tested; native picker return values were supplied by fixtures. Physical picker interaction remains a manual smoke. No tests ran on physical Windows. Earlier Windows lock checks were simulations on macOS, and no portable-lock code changed during this recovery.

Use the six manual Stage 3 steps immediately above with the disposable launcher: exercise Upload → Folder → ZIP, exact/similar/conflicting-category choices, apply-to-type, waiting close/reopen, cancel/retry, and final torso-processing/count results. Repeat on physical Windows before claiming Windows runtime coverage. Keep the documented safe failure/retry behavior when a within-batch canonical image was skipped/failed or another operation changed the catalog.

**Next action for Stage 4:** review the Stage 3 working diff and this handoff, then obtain the intended *training-import* Stage 4 requirements and acceptance criteria before implementation. No training-import Stage 4 specification was supplied; do not infer it from the separate uniform-observation/ML Stage 4 documents. Do not start additional features in this task.


## Stage 4 — integrated import-to-candidate verification (2026-11-15)

**Completed on the MacBook's native macOS environment. No production model was activated.** Branch/base remains `main` / `526fda3b263b15613f59652c0e6443c80ddac812`; all changes are uncommitted. Read AGENTS.md, this checkpoint, current diffs, UI/import/candidate dependencies and retained evidence. Every file in the Stage 3 source fingerprint inventory still matched its passing verification. Reused those results instead of repeating unrelated suites.

### New work and focused verification

Added `tests/test_training_import_integrated_ui.py`, an opt-in integration scenario using the existing disposable Tk harness and the normal Upload/Folder/ZIP, duplicate review, Training window, acknowledgment and Start controls. It does not replace candidate worker startup, freeze, optimizer steps or persisted checkpoints with mocks. No application defect was found during Stage 4; application code remains unchanged from the verified Stage 3 state.

The first run found a fixture defect: a one-shot test processor failed to drain new images queued while it was running (4 saved but only 1 processed). Corrected the fixture to use the actual coalescing scheduler. The second run used an internal legacy category name for a nonexistent visible tab; corrected the fixture to invoke the real **Evaluation & Models** tab button. Both failed-run logs are retained. The affected integrated sequence was rerun after each correction.

Exact successful command:

```sh
CBVMS_RUN_REAL_TRAINING_SMOKE=1 CBVMS_STAGE4_EVIDENCE="$PWD/output/training-import-stage4" .venv/bin/python -m unittest tests.test_training_import_integrated_ui.IntegratedImportUITests.test_import_to_candidate -v
```

**1 integrated test passed in 26.626 seconds**, covering the entire sequence below. Log: `output/training-import-stage4/integration-test.log`.

The new test reuses harness methods without rediscovering the Stage 3 tests. Its ordinary module invocation is deliberately safe:

```sh
.venv/bin/python -m unittest tests.test_training_import_integrated_ui -v
```

**1 test discovered, skipped as designed without explicit real-training opt-in**, in `output/training-import-stage4/opt-in-guard.log`. `git diff --check` also passed. Stop here; no unrelated suites or additional training runs were needed.

### Integrated acceptance results

1. Imported four distinct eligible images into **each** category through Upload Photos. Automatic queued torso crops and quality checks completed without approving photos. The Training window showed four per category and kept Start disabled even with acknowledgment selected.
2. Imported mixed inputs through **Upload Photos → Campus Uniform**, **Import Folder → Organizational Shirt**, and **Import ZIP → Wrong Uniform**. These included exact existing copies, a within-batch exact copy, new unique images, one nonidentical same-dHash view kept separately, and one corrupt PNG. Each route showed the duplicate review before catalog changes.
3. Chose **Skip** for one existing exact image and the within-batch copy, **Add source anyway** for another exact image, and **Keep both** for the useful similar view. Each category then had exactly **5 image records and 6 source references**. Eligibility was five; added sources did not increase it. Similar-image related grouping/weights remain covered by unchanged, passing Stage 3 tests.
4. Animated progress remained visible while saves were deliberately paused. Processed/total/filename display was checked. Final discovered/processed counts were 5/5 for Upload and 4/4 for Folder/ZIP. Each route reported **1 new unique, 3 duplicate occurrences, 1 added source, 2 skips**; Upload additionally reported **1 failed corrupt file**. Unknown-total growing discovery and its cancellation are covered by the retained native Stage 3 test `test_folder_discovery_is_indeterminate_and_can_be_cancelled`, whose code/dependencies still match the verified fingerprints.
5. Navigated to **Evaluation & Models** while waiting for choices, closed/reopened duplicate review, and continued with the same job/choices. Cancelled after a source save: **1 source retained, 1 unprocessed**. Injected a source-save failure: **0 new sources, 1 failure**. Retried: **1 new source, 0 failures**, without duplicating the previously saved source. Real Cancel-button interaction and stopped poll/animation timers are additionally covered by unchanged Stage 3 native controls tests.
6. Resolved a canonical-image category conflict by explicitly changing category, waited for automatic reprocessing, then restored its original category through another explicit review. Reopened the whole Training workspace. Final catalog: **15 unique images, 20 source references, exactly 5 eligible per category**, without photo approval. Imported records and source counts matched the summaries.
7. Opened **Train Model with Training-Ready Photos** via the workspace control. Start was disabled until the existing acknowledgment was toggled, then enabled. Selected **1 epoch, batch 2, CPU threads 1**, left acceleration off, and invoked Start. The normal config used `min_per_class=5`, `synthetic_smoke=false` and the actual local classifier weights.
8. The real candidate worker started as **PID 1327**, run **`5d427072416d4e2cbbda541510f72c80`**, froze 15 inputs and completed epoch **1/1**. Persisted status is **`completed`**, device **CPU**, `nondeployable=true`, with `candidate.pt` and a recovery checkpoint created in the disposable bundle. The worker exited and the training slot released. Candidate polling/task timers were cleared on close; no Tk callback exceptions occurred.

### Retained artifacts and isolation

`output/training-import-stage4/` contains:

- `integration-test.log`, `initial-fixture-failure.log`, `navigation-fixture-failure.log`, `opt-in-guard.log`;
- `integration.json`: stage counters, before/after protected-file hashes, initial/final worker status and final catalog counts;
- `status.json`, `config.json`, `dataset_manifest.json`, `train.log`, `metrics.json`, `last_checkpoint.json`, `limitations.txt`: copies of persisted disposable-job evidence.

The temporary dataset and full training bundle were removed after successful completion; the evidence copies remain. No reusable production candidate is being offered from synthetic fixtures. Hashes of `yolov8n-cls.pt`, `models/uniform_cls.pt` and the active uniform registry path were unchanged (absence also checked). No active-model publication, activation or replacement operation was called.

### Coverage limits and next action

Actual platform: **macOS 26.5 (25F71), Apple Silicon arm64**, native Tk/CustomTkinter, PyTorch 2.2.2 and Ultralytics 8.3.27. No missing dependency blocked the real candidate run. No downloads were needed.

The integrated inputs were synthetic disposable raster images; person/torso detections were deterministic fixtures. The real coalescing scheduler, crop/quality/catalog pipeline, eligibility, UI, candidate subprocess, model, loss/optimizer and checkpoint persistence were exercised. The installed automatic-processing subprocess also passed the retained Stage 3 dataset check. This verifies workflow and isolation, not person-detection performance or model accuracy. All unknown-provenance images remain grouped, so independent evaluation was unavailable, as correctly recorded in the candidate artifacts.

Native picker return values were supplied by tests. A manual physical-picker smoke on the MacBook remains useful. **No physical Windows test ran**; prior Windows file-lock evidence was simulated on macOS. Stage 4 has no known unresolved Mac implementation/test failure. Production activation remains a separate explicit user action after appropriate evaluation; training completion does not perform or authorize activation.

### Concise MacBook manual check

Use the disposable launcher earlier in this checkpoint with copies of non-sensitive photos, never the live catalog.

1. Import four usable torso photos per category and wait for automatic processing. Open Training: Start must remain disabled at four.
2. Use Upload Photos, Import Folder and Import ZIP for new photos plus exact copies, useful similar views and one corrupt PNG. Watch discovery/progress; choose Skip, Add source anyway and Keep both. Check the final counts and retained unique/source records.
3. While review waits, switch tabs, close/reopen review, cancel an import and retry. Import an existing image under another category; confirm only an explicit category change relabels it and queues processing.
4. Reopen Training with five eligible unique photos in each category. Select the acknowledgment. To explicitly start a candidate smoke, choose **1 epoch / batch 2 / CPU threads 1**, leave acceleration off and click **Train Model with Training-Ready Photos**. Watch persisted status reach Completed, or retain the exact worker error if a local dependency differs.
5. Stop at candidate completion. **Do not activate a production model as part of this smoke.** Activation, if later desired, is a separate explicit action and evaluation workflow.

Next action: review the Stage 4 evidence and uncommitted diff, then perform the short physical-picker check above. No additional feature stage or production activation was started.
