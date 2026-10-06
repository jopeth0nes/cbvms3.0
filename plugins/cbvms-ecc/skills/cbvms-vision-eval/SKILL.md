---
name: cbvms-vision-eval
description: "Evaluate CBVMS camera, face recognition, uniform inference, cooldown or preview-performance changes. Specialist/on-demand; skip unrelated portal, database or tooling work."
---

# Reproducible vision evidence

Inspect affected owners in `core/camera.py`, `core/live_pipeline.py`, `core/live_state.py`, `core/recognizer.py`, `core/uniform_matcher.py`, `ui/camera_feed.py` and `ui/dashboard.py`. Preserve latest-only queues, cancellation/stale-frame guards, serialized analysis, worker-thread inference and responsive Tk scheduling.

Select relevant labeled cases: registered frontal/profile face recognized; unknown remains unknown; compliant/wrong uniform classified appropriately; suspended student alert; unknown-person sighting; repeated detection respects cooldown/deduplication; compliant student creates no false violation. Use consented local test data or synthetic fixtures; never commit face images or embeddings.

Use existing pipeline/recognizer/camera-switching/persistence tests first. Inspect `scripts/benchmark_live_monitor.py` and enrollment/registration preview benchmarks before measuring. Record hardware, source/resolution, model versions, thresholds, warm-up, duration, analysis load and dropped frames. Compare before/after under the same conditions; synthetic render throughput is not physical-camera FPS or recognition accuracy.

Threshold changes require false-positive/false-negative evidence where labeled data exists. Do not optimize inference frequency at the expense of preview without measured justification. Report exact reproducible measurements and unavailable cases; never fabricate accuracy/FPS. Astra independently examines performance and regression evidence, with extra checks only for remaining risk.
