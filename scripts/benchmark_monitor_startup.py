"""Paired, fresh-process local-model probe, using synthetic pixels and no database.

The login delay is controlled, not a measurement of a human login or native UI.
Application-cold means a new Python process; filesystem caches are NOT flushed.
No camera, student data, business writes, downloads, or accuracy claims.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def baseline_registry(recognizer, detector, trainer, matcher, ref):
    import types
    import numpy as np
    module = types.ModuleType("benchmark_baseline_readiness")
    sys.modules[module.__name__] = module
    source = subprocess.check_output(["git", "show", f"{ref}:core/model_readiness.py"], cwd=ROOT, text=True)
    exec(compile(source, "baseline_readiness", "exec"), module.__dict__)
    registry = module.face_readiness(recognizer)
    source = subprocess.check_output(["git", "show", f"{ref}:ui/dashboard.py"], cwd=ROOT, text=True)
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "CBVMSDashboard")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_prewarm_models")
    namespace = {"np": np}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "baseline_dashboard", "exec"), namespace)
    owner = types.SimpleNamespace(_readiness=registry, _person_detector=detector,
                                  _trainer=trainer, _uniform_matcher=matcher)
    return registry, lambda: namespace["_prewarm_models"](owner)


def child(args):
    started = time.monotonic()
    import importlib.metadata
    import platform
    import resource
    from types import SimpleNamespace
    import cv2
    import numpy as np
    import psutil
    from core.recognizer import FaceRecognizer
    from core.person_detector import PersonDetector
    from core.trainer import ViolationTrainer
    from core.uniform_matcher import UniformColorMatcher
    from core.performance import PERF
    PERF.enabled = True

    # Match dashboard OpenCV policy for both conditions, before any inference.
    cv2.setNumThreads(2)
    recognizer = FaceRecognizer(SimpleNamespace(get_all_students=lambda: [], recognition_capture_encodings=lambda: {}))
    detector, trainer, matcher = PersonDetector(), ViolationTrainer(), UniformColorMatcher()
    if args.policy == "baseline":
        registry, dashboard_start = baseline_registry(recognizer, detector, trainer, matcher, args.baseline_ref)
        start = registry.start
    else:
        from core.monitor_models import MonitorModels
        models = MonitorModels(recognizer, detector, trainer, matcher)
        registry, start, dashboard_start = models.readiness, models.start, models.start
    imported = time.monotonic()
    prepared = imported
    start()
    ready_at = {}
    at_login = None
    login = None
    while time.monotonic() - prepared < 65:
        now = time.monotonic()
        statuses = registry.snapshot()
        for name, status in statuses.items():
            if status.state == "ready":
                ready_at.setdefault(name, now - prepared)
        if login is None and now - prepared >= args.login_delay:
            at_login = {name: status.state for name, status in statuses.items()}
            login = now
            dashboard_start()
        if login is not None and all(s.state in ("ready", "failed") for s in registry.snapshot().values()):
            break
        time.sleep(.005)
    registry.wait(2)
    statuses = registry.snapshot()
    # Re-read final transition before reporting; no native owner is cancelled/retried.
    now = time.monotonic()
    for name, status in statuses.items():
        if status.state == "ready":
            ready_at.setdefault(name, now-prepared)
    stages = {"face": [], "embedding": [], "body": [], "uniform": [], "cycle": []}
    frame = np.zeros((720, 1280, 3), np.uint8)
    crop = np.zeros((224, 224, 3), np.uint8)
    face_crop = np.zeros((112, 112, 3), np.uint8)
    process = psutil.Process()
    rss = [process.memory_info().rss]
    cpu_start = process.cpu_times()
    steady_start = time.monotonic()
    counts = {"faces": 0, "bodies": 0, "cycles": 0}
    while time.monotonic()-steady_start < args.seconds:
        cycle = time.monotonic()
        calls = (("face", "face", lambda: recognizer.detect_faces(frame)),
                 ("embedding", "recognition", lambda: recognizer._ensure_recognition().get_feat(face_crop)),
                 ("body", "body", lambda: detector.detect_persons(frame)),
                 ("uniform", "uniform", lambda: trainer.predict_proba("uniform", crop)))
        for name, component, call in calls:
            if registry.ready(component):
                at = time.monotonic()
                result = call()
                stages[name].append((time.monotonic()-at)*1000)
                if name in ("face", "body"):
                    counts["faces" if name == "face" else "bodies"] += len(result)
        stages["cycle"].append((time.monotonic()-cycle)*1000)
        counts["cycles"] += 1
        rss.append(process.memory_info().rss)
    elapsed = time.monotonic()-steady_start
    cpu_end = process.cpu_times()
    summary = lambda values: {"n": len(values), "p50_ms": round(float(np.percentile(values, 50)), 3),
                              "p95_ms": round(float(np.percentile(values, 95)), 3)} if values else None
    result = dict(policy=args.policy, baseline_ref=args.baseline_ref, process_cold=True, uncached=False,
        synthetic_blank_pixels=True, camera=False, native_ui=False, database=False,
        environment={"python": platform.python_version(), "platform": platform.platform(),
                     "machine": platform.machine(), "processor": platform.processor(),
                     "packages": {name: importlib.metadata.version(name) for name in
                                  ("numpy", "opencv-python", "onnxruntime", "torch", "ultralytics", "insightface")}},
        setup_seconds=round(imported-started, 3), simulated_login_delay=args.login_delay,
        states_at_login=at_login, ready_from_prepare_seconds={k: round(v, 3) for k,v in ready_at.items()},
        remaining_after_login_seconds={k: round(max(0, v-(login-prepared)), 3) for k,v in ready_at.items()} if login else {},
        components={k: {"state": v.state, "load_and_warm_seconds": round(v.elapsed, 3), "error": v.error} for k,v in statuses.items()},
        steady_seconds=round(elapsed, 3), serial_probe_hz=round(counts["cycles"]/elapsed, 3),
        stage_timings={k: summary(v) for k,v in stages.items()},
        rss_start_mb=round(rss[0]/2**20, 2), rss_end_mb=round(rss[-1]/2**20, 2),
        rss_peak_mb=round(max(rss)/2**20, 2), process_peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/2**20, 2),
        cpu_percent_one_core=round(100*((cpu_end.user-cpu_start.user)+(cpu_end.system-cpu_start.system))/elapsed, 1),
        blank_scene_detections=counts, accuracy_evaluated=False, bounded_timings=PERF.snapshot())
    print("RESULT " + json.dumps(result), flush=True)
    return 0 if all(registry.ready(name) for name in ("face", "recognition", "body", "uniform")) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--baseline-ref", default="244d6805ee00c5f8b3bf60f9fecf0d288dc70b7c")
    parser.add_argument("--login-delay", type=float, default=5.)
    parser.add_argument("--seconds", type=float, default=10.)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.seconds <= 0 or args.login_delay < 0 or args.runs < 1:
        parser.error("seconds/runs must be positive; login-delay cannot be negative")
    if args.child:
        return child(args)
    results = []
    for run in range(args.runs):
        completed = subprocess.run([sys.executable, __file__, *sys.argv[1:], "--child"],
                                   cwd=ROOT, text=True, capture_output=True, timeout=100+args.seconds+args.login_delay)
        lines = [line[7:] for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(f".run{run+1}.log").write_text(completed.stdout+completed.stderr)
        if completed.returncode or not lines:
            # Libraries can be noisy; these probes never read personal records.
            raise RuntimeError(f"Probe failed ({completed.returncode}): {completed.stderr[-3000:]} {completed.stdout[-1000:]}")
        result = json.loads(lines[-1])
        results.append(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2)+"\n")
        print(json.dumps({"run": run+1, "policy": args.policy, "ready": result["remaining_after_login_seconds"],
                          "stages": result["stage_timings"]}), flush=True)


if __name__ == "__main__":
    main()
