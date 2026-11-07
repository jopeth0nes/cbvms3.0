# Stage 2 candidate backend report

The isolated candidate service freezes eligible Stage 1 approvals into an immutable, train-only bundle under `data/uniform_candidates` (or a caller-supplied candidate root). The UI-facing API is `CandidateService(dataset, candidates_root=...)` with `summary()`, `start(config)`, `resume(run_id)`, `cancel(run_id)`, `status(run_id=None)`, `list_runs()`, and nonblocking `is_busy()`. Candidate IDs are UUID hex strings. Summary separates approved category counts from eligible clean counts and exposes `experimental_training_eligible` plus reasons; evaluation and activation remain false.

The bundle uses the local trusted YOLOv8n classification checkpoint, replaces its classifier head with the fixed `uniform-garments-v1` 3-class mapping, and trains through a child CPU worker. Inputs are frozen tensors prepared with the existing 224-pixel `core.trainer._letterbox` convention, BGR→RGB and float32 `[0,1]`; there is no flip or augmentation. Training is experimental and train-only. It produces no validation/evaluation metrics, is marked nondeployable, and the service has no activation or active-model write path. Each new run directory is owner-only (`0700`). The manifest/config retain eligibility counts and provenance coverage. The worker checks YOLOv8n architecture and requires trainable backbone and new-head parameters, recording the actual inventory in `architecture.json`. Resume verifies frozen inputs and checkpoint SHA metadata before loading checkpoint state. Cancellation preserves the last complete epoch.

## Verification

- `.venv/bin/python -m py_compile core/uniform_candidate.py core/uniform_candidate_worker.py tests/test_uniform_candidate.py scripts/smoke_uniform_candidate.py` — passed.
- `.venv/bin/python -m unittest tests.test_uniform_candidate -v` — 16 tests passed. Tests exercise approved-only/quality-filtered counts and shared unknown-group accounting; missing-category readiness; config bounds; letterbox tensor equivalence; frozen-manifest/tensor tamper rejection; symlink source/run-directory rejection; owner-only run permissions; async worker handoff, prepare cancellation and real subprocess flock overlap/release; stale-status/latest-run recovery; class-map and checkpoint tamper rejection; checkpoint integrity; and a small synthetic CPU CrossEntropy update.
- `.venv/bin/python -c 'import scripts.smoke_uniform_candidate'` — passed; import is inert and does not launch training.
- `.venv/bin/python -m scripts.smoke_uniform_candidate` — prepared but intentionally not run. When authorized, it retains the complete run under `data/uniform_candidate_smoke/<uuid>`, incrementally writes `output/uniform-stage2/smoke-results.json`, launches CPU training using six synthetic fixtures, tests cancel/resume through a newly constructed service instance, verifies class map/preprocessing/checkpoints/full bundle checksums and trainable architecture, and compares all seven protected artifact hashes with the Stage 1 final audit. On failure it requests cancellation and waits up to 60 seconds, preserving the bundle/log for recovery.

No real Stage 1 catalog or active trainer/model was accessed during these checks. No candidate training job was launched.

## Remaining verification / risks

The repository's trusted local `yolov8n-cls.pt` architecture and full subprocess training, cancellation, resume and active-artifact fingerprints remain unverified until the opt-in smoke is authorized. The bundle is train-only and cannot support accuracy or deployment claims.

## Lead acceptance (supersedes the earlier pending-smoke note)

Lead corrected invalid approved crop exclusion to match summary counts and added
its regression; the final backend suite passed **17 tests**. Actual synthetic CPU
smoke then passed cancellation at epoch 1 and resume through a new service to the
same frozen run's completion (10 tiny synthetic epochs, batch2/thread1). The first
3-epoch smoke finished before its 250ms cancellation poll; failed assertion and
bundle retained. Final smoke polls at 10ms. See `smoke-results.json`, `smoke.log`,
`weights-consistency.json`, `preservation-results.json` and the final checkpoint.
All model artifacts/catalog/protected data remained unchanged. No real-data
candidate training, accuracy evaluation or activation was performed. Final code
hashes: `output/uniform-stage2/code-manifest.json`.
