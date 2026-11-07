# Stage 4 backend benchmark worker report

Status: **ready for lead review**. Added a read-only bounded CLI that accepts an existing Stage 2 candidate bundle and either explicit raster paths or a deterministic synthetic raster fixture. It validates the candidate with `candidate_descriptor` before constructing a predictor, preflights the trusted local body/classifier weights to prevent downloads, and benchmarks candidate classification and `PersonDetector.detect_persons` on the same raster inputs for CPU, MPS, or both.

Defaults are 10 measured repeats and 2 warmups, with bounds of 1–100 repeats, 0–20 warmups, at most 100 images, 40 MP per raster, and 80 MP retained total. Image format/dimensions and combined pixel budget are checked from headers before OpenCV decode. Per-backend output separates cold load and warmup from measured timings. Torch and OpenCV thread counts are set to 2 after each loader setup and before every warmup/measured inference call; actual observed counts are included per stage. Candidate `CandidatePredictor.timed_probabilities` values report preprocessing, inference, and total median/p95/count with actual backend status. Body detector timing includes total `detect_persons` call time plus per-call Ultralytics `preprocess`, `inference`, and `postprocess` measurements when those methods are available. The stage wrappers synchronize MPS before/after each wrapped method when the detector's actual placement is MPS. Missing predictor-stage APIs produce null stage timings with a reason; no timing is inferred. Placement and any CPU fallback are recorded. No queue-delay field is fabricated. Passage metrics are explicitly unavailable. Reports include input SHA256/dimensions, candidate ID/preprocessing and dataset versions, synthetic flag, platform/runtime/hardware model, and limitations; image filenames and pixels are omitted. The script prepends the repository root to `sys.path`, sets temporary Matplotlib/Ultralytics config defaults before local runtime imports, and writes partial JSON results before returning exit code 2 if any backend stage fails.

Verification:

- `.venv/bin/python -m py_compile scripts/benchmark_uniform_backends.py tests/test_uniform_backend_benchmark.py` — passed.
- `.venv/bin/python -m unittest tests.test_uniform_backend_benchmark -v` — **9 passed**. Mocked tests verify same-input CPU/MPS calls, timing aggregation/output serialization, identical thread settings, per-call predictor-stage instrumentation and MPS synchronization, MPS error and body fallback reporting, deterministic synthetic input, preflight ordering, path rejection, argument bounds, header/pixel-budget rejection before decode, isolated CLI import setup, and partial-result/nonzero exit behavior.
- `.venv/bin/python scripts/benchmark_uniform_backends.py --help` — passed.
- `git diff --check -- scripts/benchmark_uniform_backends.py tests/test_uniform_backend_benchmark.py` — passed.

No real candidate, real image, model inference, camera, or MPS device benchmark was run. The real timing run remains for the lead, as requested. The tests patch both model runners and do not load a model.

Owned file SHA-256:

- `scripts/benchmark_uniform_backends.py`: `f1b6b9465d5e3fa780e168139252bbafb2acb717a2f3235c75dffb6e1dbe1676`
- `tests/test_uniform_backend_benchmark.py`: `d9ad3cebb6946415c8926445dbabcdb4fef434a66c61d27f6b63b4136c819f59`
