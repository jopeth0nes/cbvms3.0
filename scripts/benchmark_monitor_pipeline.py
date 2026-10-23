"""Same-input handoff/empty-scene probes and a sustained synthetic preview run.

These isolate orchestration costs; they do not measure model accuracy or physical
camera performance. The empty scene uses real SQLite in a temporary directory.
"""
from dataclasses import replace
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import live_pipeline as current
from core.live_state import FrameContext
from database.db_manager import CBVMSDatabase


def load_baseline(ref):
    module = ModuleType("monitor_pipeline_baseline")
    sys.modules[module.__name__] = module
    source = subprocess.check_output(["git", "show", f"{ref}:core/live_pipeline.py"], cwd=ROOT, text=True)
    exec(compile(source, "baseline_live_pipeline", "exec"), module.__dict__)
    return module


def summary(samples):
    return {"n": len(samples), "p50_ms": round(float(np.percentile(samples, 50)), 4),
            "p95_ms": round(float(np.percentile(samples, 95)), 4)}


def probe(module, policy, db, iterations):
    frame = np.zeros((720, 1280, 3), np.uint8)
    task = module.MonitorTask(FrameContext(1, ("synthetic", 0), time.monotonic()),
                              frame, time.time(), threading.Event())
    handoffs = []
    for _ in range(iterations):
        started = time.perf_counter()
        value = replace(task, observations=()) if policy == "baseline" else task.with_observations(())
        handoffs.append((time.perf_counter()-started)*1000)
    shared_pixels = value.frame is task.frame
    processor = module.LiveProcessor(db, SimpleNamespace(recognize_faces=lambda *a, **k: [], last_error=None),
        None, SimpleNamespace(is_trained=lambda _: False), None, None)
    costs = []
    for index in range(iterations):
        task = module.MonitorTask(FrameContext(1, ("synthetic", index), time.monotonic()),
                                  frame, time.time(), threading.Event())
        started = time.perf_counter()
        result = processor.analyze(task)
        costs.append((time.perf_counter()-started)*1000)
        assert result is not None and not result.assessments
    return {"policy": policy, "handoff": summary(handoffs), "handoff_shares_owned_pixels": shared_pixels,
            "empty_scene_analysis": summary(costs), "resolution": [1280, 720]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="244d6805ee00c5f8b3bf60f9fecf0d288dc70b7c")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--sustained-seconds", type=float, default=60.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 1 or args.iterations < 1 or args.sustained_seconds <= 0:
        parser.error("runs, iterations and sustained-seconds must be positive")
    baseline = load_baseline(args.baseline_ref)
    runs = []
    with tempfile.TemporaryDirectory(prefix="cbvms_monitor_benchmark_") as folder:
        db = CBVMSDatabase(Path(folder)/"disposable.db")
        db.initialize(process_deadlines=False)
        for run in range(args.runs):
            # Alternate order to limit warm-cache/order bias.
            conditions = [(baseline, "baseline"), (current, "candidate")]
            if run % 2:
                conditions.reverse()
            for module, policy in conditions:
                result = probe(module, policy, db, args.iterations)
                runs.append(result)
                print(json.dumps(result), flush=True)
    import psutil
    import cv2
    from core.performance import PERF
    from scripts.benchmark_live_monitor import run as preview
    PERF.enabled = True
    cv2.setNumThreads(2)  # Same preview policy as the application.
    process = psutil.Process()
    memory = []
    stop_sampling = threading.Event()
    def sample_memory():
        while not stop_sampling.wait(1):
            memory.append(round(process.memory_info().rss/2**20, 2))
    sampler = threading.Thread(target=sample_memory, daemon=True)
    sampler.start()
    rss_before = process.memory_info().rss/2**20
    cpu_before = process.cpu_times()
    try:
        sustained = preview(args.sustained_seconds, 5, True, "after", (None, None, None), 200)
    finally:
        stop_sampling.set()
        sampler.join(2)
    cpu_after = process.cpu_times()
    sustained.update(rss_before_mb=round(rss_before, 2), rss_after_mb=round(process.memory_info().rss/2**20, 2),
        cpu_percent_one_core=round(100*((cpu_after.user-cpu_before.user)+(cpu_after.system-cpu_before.system))/sustained["elapsed_seconds"], 2),
        rss_sampled_mb=memory,
        bounded_timings=PERF.snapshot())
    results = {"kind": "controlled_synthetic_no_camera", "baseline_ref": args.baseline_ref,
               "accuracy_evaluated": False, "comparisons": runs, "sustained_preview": sustained}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2)+"\n")
    print(json.dumps(sustained), flush=True)


if __name__ == "__main__":
    main()
