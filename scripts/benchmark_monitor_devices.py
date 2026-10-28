"""Opt-in device benchmark for installed monitor YOLO weights.

Uses fixed synthetic pixels only. Each model/device trial runs in a fresh child
process; no cameras, database, student records, embeddings, downloads, or writes
to model assets are involved. Synthetic outcomes are not an accuracy measure.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _version(package):
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _environment_probe():
    machine = {"system": platform.system(), "release": platform.release(),
               "machine": platform.machine(), "processor": platform.processor(),
               "python": platform.python_version()}
    if platform.system() == "Darwin":
        for key, query in (("cpu_brand", "machdep.cpu.brand_string"), ("memory_bytes", "hw.memsize")):
            try:
                value = subprocess.run(["sysctl", "-n", query], check=True,
                    capture_output=True, text=True, timeout=2).stdout.strip()
                machine[key] = int(value) if key == "memory_bytes" else value
            except (OSError, subprocess.SubprocessError, ValueError):
                machine[key] = None
    info = {
        "machine": machine,
        "packages": {name: _version(name) for name in
                     ("torch", "ultralytics", "onnxruntime", "numpy", "opencv-python")},
        "torch": {"available": False, "cuda_available": False,
                  "mps_built": False, "mps_available": False},
        "onnxruntime": {"available": False, "installed_providers": [],
                        "local_sessions": [], "coreml": {"state": "unavailable",
                            "execution_note": "Provider registration does not prove per-node GPU or ANE execution"}},
    }
    try:
        import torch
        info["torch"] = {
            "available": True,
            "version": torch.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "mps_built": bool(torch.backends.mps.is_built()),
            "mps_available": bool(torch.backends.mps.is_available()),
        }
    except Exception as exc:
        info["torch"]["error"] = f"{type(exc).__name__}: {exc}"

    try:
        import onnxruntime as ort
        installed = list(ort.get_available_providers())
        info["onnxruntime"] = {
            "available": True,
            "version": ort.__version__,
            "installed_providers": installed,
            "local_sessions": [],
            "coreml": {"state": "installed" if "CoreMLExecutionProvider" in installed
                       else "unavailable",
                       "reason": None if "CoreMLExecutionProvider" in installed else
                       "CoreMLExecutionProvider is not installed",
                       "execution_note": "Provider registration does not prove per-node GPU or ANE execution"},
        }
    except Exception as exc:
        info["onnxruntime"]["error"] = f"{type(exc).__name__}: {exc}"
        info["onnxruntime"]["coreml"] = {
            "state": "unavailable", "reason": "onnxruntime could not be probed",
            "execution_note": "Provider registration does not prove per-node GPU or ANE execution"}
    return info


def _available_devices(environment):
    # Include the actual Ultralytics default selection separately from an
    # explicit CPU control; they are compared from observed predictor devices.
    devices = ["cpu", "default"]
    torch_info = environment["torch"]
    if torch_info.get("cuda_available"):
        devices.append("cuda")
    if torch_info.get("mps_available"):
        devices.append("mps")
    return devices


def _percentiles(values):
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    def percentile(p):
        at = (len(ordered)-1)*p
        low, high = math.floor(at), math.ceil(at)
        return ordered[low] if low == high else ordered[low]*(high-at)+ordered[high]*(at-low)
    return {"n": len(ordered), "p50_ms": round(percentile(.50), 3),
            "p95_ms": round(percentile(.95), 3), "p99_ms": round(percentile(.99), 3)}


def _device_matches(expected, observed):
    if observed is None:
        return False
    value = str(observed).lower().split(":", 1)[0]
    return value == expected.lower()


def _synchronize_accelerators(torch):
    """Wait for queued work on available accelerators before timing ends."""
    cuda = getattr(torch, "cuda", None)
    if cuda is not None and cuda.is_available():
        cuda.synchronize()
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        torch.mps.synchronize()


def _embedding_comparison(left, right):
    """Return scalar-only comparison of normalized recognition embeddings."""
    import numpy as np
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape or not a.size or not np.isfinite(a).all() or not np.isfinite(b).all():
        return {"same_shape": a.shape == b.shape, "cosine_similarity": None,
                "max_abs_normalized_delta": None}
    norm_a, norm_b = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return {"same_shape": True, "cosine_similarity": None,
                "max_abs_normalized_delta": None}
    normalized_a, normalized_b = a / norm_a, b / norm_b
    return {"same_shape": True,
            "cosine_similarity": round(float(np.dot(normalized_a, normalized_b)), 10),
            "max_abs_normalized_delta": round(float(np.max(np.abs(normalized_a-normalized_b))), 10)}


def _child(args):
    import numpy as np
    import torch
    from core.person_detector import PersonDetector
    from core.trainer import MODULES, ViolationTrainer, _letterbox

    try:
        start_load = time.perf_counter()
        if args.module == "body":
            owner = PersonDetector()
            model = owner._ensure_model()
            if model is None:
                raise RuntimeError(owner.last_error or "Body weights did not load")
            source = Path(owner._MODEL_PATH)
        elif args.module == "uniform":
            owner = ViolationTrainer()
            model = owner._get_model("uniform")
            if model is None:
                raise RuntimeError(owner.last_error.get("uniform") or "Uniform weights did not load")
            source = ROOT / MODULES["uniform"]["model_out"]
        else:
            raise ValueError(f"Unsupported module: {args.module}")
        cold_load_ms = (time.perf_counter()-start_load)*1000
        if not source.is_file() or source.stat().st_size < 1024:
            raise RuntimeError(f"Missing/incomplete local weights: {source}")

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        classifier_input = _letterbox(np.zeros((224, 224, 3), dtype=np.uint8))

        def predict(active_device):
            if args.module == "body":
                kwargs = {"conf": PersonDetector._CONF, "iou": PersonDetector._IOU,
                          "classes": [0], "verbose": False}
                if active_device != "default":
                    kwargs["device"] = active_device
                results = model.predict(frame, **kwargs)
                boxes = results[0].boxes if results else None
                if boxes is None:
                    return {"shape": [0, 4], "finite": True, "count": 0,
                            "classes": [], "top1": None, "probs": None}
                xyxy = boxes.xyxy.detach().cpu().numpy()
                classes = sorted({int(v) for v in boxes.cls.detach().cpu().tolist()})
                names = results[0].names
                return {"shape": list(xyxy.shape), "finite": bool(np.isfinite(xyxy).all()),
                        "count": int(xyxy.shape[0]),
                        "classes": [str(names.get(i, i)) for i in classes],
                        "top1": None, "probs": None}
            kwargs = {"verbose": False}
            if active_device != "default":
                kwargs["device"] = active_device
            results = model.predict(classifier_input, **kwargs)
            if not results or getattr(results[0], "probs", None) is None:
                return {"shape": None, "finite": False, "count": 0,
                        "classes": [], "top1": None, "probs": None}
            result = results[0]
            tensor = result.probs.data
            values = tensor.detach().cpu().numpy() if hasattr(tensor, "detach") else np.asarray(tensor)
            names = result.names
            label_names = [names[i] for i in range(len(values))]
            top = int(np.argmax(values))
            return {"shape": list(values.shape), "finite": bool(np.isfinite(values).all()),
                    "count": int(values.size), "classes": label_names,
                    "top1": str(names[top]), "probs": values.astype(float).tolist()}

        _synchronize_accelerators(torch)
        first_started = time.perf_counter()
        first = predict(args.device)
        _synchronize_accelerators(torch)
        first_call_ms = (time.perf_counter()-first_started)*1000
        predictor = getattr(model, "predictor", None)
        backend = getattr(predictor, "model", None)
        backend_device = getattr(backend, "device", None)
        parameter_device = None
        try:
            parameter_device = str(next(backend.model.parameters()).device)
        except (AttributeError, StopIteration, TypeError):
            try:
                parameter_device = str(next(backend.parameters()).device)
            except (AttributeError, StopIteration, TypeError):
                pass
        observed_device = str(parameter_device or backend_device) if (parameter_device or backend_device) else None

        timings = []
        last = first
        for _ in range(args.repeats):
            _synchronize_accelerators(torch)
            started = time.perf_counter()
            last = predict(args.device)
            _synchronize_accelerators(torch)
            timings.append((time.perf_counter()-started)*1000)

        comparison = first
        reference_source = "same_cpu_trial"
        reference_error = None
        if args.device != "cpu":
            try:
                if args.module == "body":
                    ref_owner = PersonDetector()
                    reference_model = ref_owner._ensure_model()
                    if reference_model is None:
                        raise RuntimeError(ref_owner.last_error or "CPU reference weights did not load")
                    original_model = model
                    model = reference_model
                    comparison = predict("cpu")
                    model = original_model
                else:
                    ref_owner = ViolationTrainer()
                    reference_model = ref_owner._get_model("uniform")
                    if reference_model is None:
                        raise RuntimeError(ref_owner.last_error.get("uniform") or "CPU reference weights did not load")
                    original_model = model
                    model = reference_model
                    comparison = predict("cpu")
                    model = original_model
                reference_source = "fresh_cpu_model_same_process"
            except Exception as exc:
                reference_error = f"{type(exc).__name__}: {exc}"

        delta = None
        same_outcome = None
        if args.module == "uniform" and comparison.get("probs") is not None and first.get("probs") is not None:
            if len(comparison["probs"]) == len(first["probs"]):
                delta = round(max(abs(a-b) for a,b in zip(comparison["probs"], first["probs"])), 8)
                same_outcome = comparison.get("top1") == first.get("top1")
        elif args.module == "body" and comparison.get("classes") is not None:
            same_outcome = comparison.get("classes") == first.get("classes")
        actual_matches = (bool(parameter_device or backend_device) if args.device == "default"
                          else _device_matches(args.device, parameter_device or backend_device))
        state = "ready" if (first.get("finite") and actual_matches and reference_error is None) else "failed"
        result = {
            "state": state,
            "module": args.module,
            "requested_device": args.device,
            "actual_device": {"predictor_backend": str(backend_device) if backend_device is not None else None,
                              "parameter": parameter_device,
                              "matches_request": actual_matches},
            "weights": {"name": source.name, "bytes": source.stat().st_size},
            "cold_load_ms": round(cold_load_ms, 3),
            "first_call_ms": round(first_call_ms, 3),
            "steady_state": _percentiles(timings),
            "output": {"shape": first["shape"], "finite": first["finite"],
                       "count": first["count"], "synthetic_classes": first["classes"],
                       "synthetic_top1": first["top1"],
                       "same_outcome_as_cpu": same_outcome,
                       "max_abs_probability_delta_vs_cpu": delta,
                       "cpu_reference": reference_source,
                       "cpu_reference_error": reference_error},
        }
        print("RESULT " + json.dumps(result, allow_nan=False), flush=True)
        return 0 if state == "ready" else 2
    except Exception as exc:
        print("RESULT " + json.dumps({"state": "failed", "module": args.module,
              "requested_device": args.device, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 2


def _run_child(script_args, timeout):
    completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child",
        "--module", script_args["module"], "--device", script_args["device"],
        "--repeats", str(script_args["repeats"])],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    results = [line[len("RESULT "):] for line in completed.stdout.splitlines()
               if line.startswith("RESULT ")]
    if not results:
        return {"state": "failed", **script_args,
                "error": (completed.stderr or completed.stdout)[-3000:] or
                         f"Child exited {completed.returncode} without a result"}
    try:
        result = json.loads(results[-1])
    except json.JSONDecodeError as exc:
        return {"state": "failed", **script_args, "error": f"Invalid child result: {exc}"}
    result.update({key: value for key, value in script_args.items() if key not in result})
    if completed.returncode and result.get("state") == "ready":
        result["state"] = "failed"
        result["error"] = f"Child exit code {completed.returncode}"
    return result


def _face_model_paths():
    model_dir = Path(os.environ.get(
        "CBVMS_FACE_MODEL_DIR", "~/.insightface/models/buffalo_l")).expanduser()
    return {"face_detection": model_dir / "det_10g.onnx",
            "face_recognition": model_dir / "w600k_r50.onnx"}


def _face_child(args):
    """Compare CPU prepare reset/no-reset and optional CoreML on local ONNX models."""
    import cv2
    import numpy as np
    import onnxruntime as ort
    from insightface.model_zoo.model_zoo import ModelRouter

    path = Path(args.face_model)
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError("Local face ONNX weights are missing or incomplete")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    options.log_severity_level = 3
    is_detection = args.face_kind == "face_detection"
    synthetic = (np.zeros((320, 320, 3), dtype=np.uint8) if is_detection
                 else np.zeros((112, 112, 3), dtype=np.uint8))

    def open_model(providers):
        started = time.perf_counter()
        model = ModelRouter(str(path)).get_model(providers=providers, sess_options=options)
        load_ms = (time.perf_counter()-started)*1000
        if model is None:
            raise RuntimeError(f"ModelRouter could not classify {path.name}")
        return model, load_ms

    def prepare(model, ctx_id):
        started = time.perf_counter()
        if is_detection:
            model.prepare(ctx_id=ctx_id, input_size=(640, 640), det_thresh=.5)
        else:
            model.prepare(ctx_id=ctx_id)
        return (time.perf_counter()-started)*1000

    def infer(model):
        if is_detection:
            boxes, _keypoints = model.detect(synthetic, input_size=(320, 320))
            values = np.asarray(boxes, dtype=np.float32)
            return values.reshape((-1, values.shape[-1] if values.ndim > 1 else 1))
        return np.asarray(model.get_feat(synthetic), dtype=np.float32)

    def run_condition(condition, providers):
        model, router_load_ms = open_model(providers)
        ctx_id = -1 if condition == "cpu_reset" else 0
        prepare_ms = prepare(model, ctx_id)
        first_started = time.perf_counter()
        first_output = infer(model)
        first_call_ms = (time.perf_counter()-first_started)*1000
        steady = []
        last_output = first_output
        for _ in range(args.repeats):
            started = time.perf_counter()
            last_output = infer(model)
            steady.append((time.perf_counter()-started)*1000)
        actual_providers = list(model.session.get_providers())
        return {
            "condition": condition,
            "router_load_ms": round(router_load_ms, 3),
            "prepare_ms": round(prepare_ms, 3),
            "first_inference_ms": round(first_call_ms, 3),
            "steady_state": _percentiles(steady),
            "actual_model_class": type(model).__name__,
            "actual_providers_after_prepare_and_inference": actual_providers,
            "output_shape": list(first_output.shape),
            "output_finite": bool(np.isfinite(first_output).all()),
            "output_count": int(first_output.shape[0]) if first_output.ndim else int(first_output.size),
            "same_as_last_repeat": bool(first_output.shape == last_output.shape and
                np.allclose(first_output, last_output, rtol=0, atol=0, equal_nan=False)),
            "_output_internal": first_output,
        }

    condition = args.face_condition
    if condition in ("cpu_reset", "cpu_no_reset"):
        trial = run_condition(condition, ["CPUExecutionProvider"])
        reference_condition = "cpu_no_reset" if condition == "cpu_reset" else "cpu_reset"
        reference = run_condition(reference_condition, ["CPUExecutionProvider"])
    elif condition == "coreml":
        available = ort.get_available_providers()
        if "CoreMLExecutionProvider" not in available:
            raise RuntimeError("CoreMLExecutionProvider is not installed")
        trial = run_condition(condition, ["CoreMLExecutionProvider", "CPUExecutionProvider"])
        reference = run_condition("cpu_reset", ["CPUExecutionProvider"])
    else:
        raise ValueError(f"Unknown face condition: {condition}")
    trial_values, ref_values = trial.pop("_output_internal"), reference.pop("_output_internal")
    same_shape = trial_values.shape == ref_values.shape
    finite_both = bool(np.isfinite(trial_values).all() and np.isfinite(ref_values).all())
    same = bool(same_shape and finite_both and
                np.allclose(trial_values, ref_values, rtol=0, atol=0, equal_nan=False))
    delta = (float(np.max(np.abs(trial_values-ref_values)))
             if same_shape and finite_both and trial_values.size else
             (0. if same_shape and finite_both else None))
    trial["comparison_to_reference"] = {
        "same_shape": same_shape,
        "same_output": same,
        "max_abs_delta": round(delta, 8) if delta is not None else None,
        "reference_condition": reference["condition"],
    }
    if args.face_kind == "face_recognition":
        trial["comparison_to_reference"].update(_embedding_comparison(trial_values, ref_values))
    providers_match = ("CPUExecutionProvider" in trial["actual_providers_after_prepare_and_inference"]
        if condition != "coreml" else
        "CoreMLExecutionProvider" in trial["actual_providers_after_prepare_and_inference"])
    result = {"state": "ready" if trial["output_finite"] and providers_match else "failed",
              "model": args.face_kind, "weights": path.name,
              "condition": condition, "trial": trial,
              "provider_execution_note": ("CoreML provider registration does not prove per-node GPU or ANE execution"
                  if condition == "coreml" else None)}
    print("RESULT " + json.dumps(result, allow_nan=False), flush=True)
    return 0 if result["state"] == "ready" else 2


def _run_face_child(model_name, path, condition, repeats, timeout):
    completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--face-child",
        "--face-kind", model_name, "--face-model", str(path),
        "--face-condition", condition, "--repeats", str(repeats)],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    lines = [line[len("RESULT "):] for line in completed.stdout.splitlines()
             if line.startswith("RESULT ")]
    if not lines:
        return {"state": "failed", "model": model_name, "weights": path.name,
                "condition": condition,
                "error": (completed.stderr or completed.stdout)[-3000:] or
                         f"Child exited {completed.returncode} without a result"}
    try:
        result = json.loads(lines[-1])
        if completed.returncode and result.get("state") == "ready":
            result["state"] = "failed"
            result["error"] = f"Child exit code {completed.returncode}"
        return result
    except json.JSONDecodeError as exc:
        return {"state": "failed", "model": model_name, "weights": path.name,
                "condition": condition, "error": f"Invalid child result: {exc}"}


def _local_weight_status():
    from core.person_detector import PersonDetector
    from core.trainer import MODULES
    paths = {"body": Path(PersonDetector._MODEL_PATH),
             "uniform": ROOT / MODULES["uniform"]["model_out"],
             "earring": ROOT / MODULES["earring"]["model_out"],
             **_face_model_paths()}
    return {name: ({"state": "available", "name": path.name, "bytes": path.stat().st_size}
                   if path.is_file() and path.stat().st_size >= 1024 else
                   {"state": "unavailable", "reason": f"Local weights missing or incomplete: {path.name}"})
            for name, path in paths.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="Aggregate JSON output path")
    parser.add_argument("--repeats", type=int, default=25,
                        help="Steady-state calls per module/device trial (default: 25)")
    parser.add_argument("--timeout", type=int, default=180,
                        help="Per fresh-process trial timeout in seconds")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--module", choices=("body", "uniform"), help=argparse.SUPPRESS)
    parser.add_argument("--device", choices=("cpu", "default", "cuda", "mps"), help=argparse.SUPPRESS)
    parser.add_argument("--face-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--face-kind", choices=("face_detection", "face_recognition"), help=argparse.SUPPRESS)
    parser.add_argument("--face-model", help=argparse.SUPPRESS)
    parser.add_argument("--face-condition", choices=("cpu_reset", "cpu_no_reset", "coreml"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.face_child:
        try:
            return _face_child(args)
        except Exception as exc:
            print("RESULT " + json.dumps({"state": "failed", "model": args.face_kind,
                "condition": args.face_condition,
                "error": f"{type(exc).__name__}: {exc}"}), flush=True)
            return 2
    if args.child:
        return _child(args)
    if args.output is None:
        parser.error("--output is required")
    if args.repeats < 1 or args.timeout < 1:
        parser.error("repeats and timeout must be positive")
    environment = _environment_probe()
    devices = _available_devices(environment)
    weights = _local_weight_status()
    trials = []
    for module in ("body", "uniform"):
        if weights[module]["state"] != "available":
            trials.append({"state": "unavailable", "module": module,
                           "reason": weights[module]["reason"]})
            continue
        for device in devices:
            config = {"module": module, "device": device, "repeats": args.repeats}
            try:
                result = _run_child(config, args.timeout)
            except subprocess.TimeoutExpired:
                result = {"state": "failed", **config,
                          "error": f"Fresh-process trial timed out after {args.timeout}s"}
            trials.append(result)
        cpu_result = next((trial for trial in trials if trial.get("module") == module
                           and trial.get("requested_device") == "cpu"), None)
        default_result = next((trial for trial in trials if trial.get("module") == module
                               and trial.get("requested_device") == "default"), None)
        cpu_actual = None
        default_actual = None
        if cpu_result:
            actual = cpu_result.get("actual_device", {})
            cpu_actual = actual.get("parameter") or actual.get("predictor_backend")
        if default_result:
            actual = default_result.get("actual_device", {})
            default_actual = actual.get("parameter") or actual.get("predictor_backend")
            default_result["default_device_comparison"] = {
                "reference": "fresh explicit CPU trial for this module",
                "explicit_cpu_actual_device": cpu_actual,
                "default_actual_device": default_actual,
                "matches_explicit_cpu": (_device_matches(
                    str(cpu_actual).split(":", 1)[0], default_actual)
                    if cpu_actual is not None and default_actual is not None else False),
            }
    for device in ("mps", "cuda"):
        if device not in devices:
            trials.append({"state": "skipped", "device": device,
                "reason": "torch reports this device unavailable; no fallback trial was substituted"})
    face_trials = []
    face_paths = _face_model_paths()
    ort_info = environment["onnxruntime"]
    coreml_installed = "CoreMLExecutionProvider" in ort_info.get("installed_providers", [])
    for model_name, model_path in face_paths.items():
        if not model_path.is_file() or model_path.stat().st_size < 1024:
            face_trials.append({"state": "unavailable", "model": model_name,
                "reason": f"Local ONNX weights missing: {model_path.name}"})
            continue
        for condition in ("cpu_reset", "cpu_no_reset"):
            try:
                face_trials.append(_run_face_child(model_name, model_path, condition,
                                                   args.repeats, args.timeout))
            except subprocess.TimeoutExpired:
                face_trials.append({"state": "failed", "model": model_name,
                    "condition": condition, "error": f"Face provider trial timed out after {args.timeout}s"})
        if coreml_installed:
            try:
                face_trials.append(_run_face_child(model_name, model_path, "coreml",
                                                   args.repeats, args.timeout))
            except subprocess.TimeoutExpired:
                face_trials.append({"state": "failed", "model": model_name,
                    "condition": "coreml", "error": f"CoreML trial timed out after {args.timeout}s"})
        else:
            reason = "CoreMLExecutionProvider is not installed"
            face_trials.append({"state": "skipped", "model": model_name,
                                "condition": "coreml", "reason": reason})
    report = {
        "probe": "monitor_model_device_benchmark",
        "created_at_local": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "synthetic_inputs_only": True,
        "camera": False, "database": False, "student_data": False,
        "downloads": False, "accuracy_evaluated": False,
        "machine_and_providers": environment,
        "local_weights": weights,
        "trials": trials,
        "face_provider_trials": face_trials,
        "limits": ["Synthetic class outcomes are not model accuracy.",
                   "Fresh processes do not clear filesystem caches.",
                   "Only installed local weights are opened."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"output": str(args.output), "machine": environment["machine"],
        "providers": environment["onnxruntime"], "trials": [
            {key: trial.get(key) for key in ("module", "device", "requested_device", "state", "steady_state")}
            for trial in trials], "face_provider_trials": [
            {key: trial.get(key) for key in ("model", "condition", "state", "trial")}
            for trial in face_trials]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
