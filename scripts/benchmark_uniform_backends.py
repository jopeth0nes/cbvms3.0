"""Read-only bounded CPU/MPS timings for an existing uniform candidate bundle.

No camera, training, activation, data writes, face inference, or model downloads.
Input images are consumed in memory and only their content hashes/dimensions are
written to the output report.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cbvms-uniform-benchmark-mpl"))
os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "cbvms-uniform-benchmark-yolo"))

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

MAX_INPUTS = 100
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_TOTAL_INPUT_PIXELS = 80_000_000
MAX_IMAGE_DIMENSION = 12_000
MAX_REPEATS = 100
MAX_WARMUP = 20
BENCHMARK_THREADS = 2
BODY_PREDICTOR_STAGES = ("preprocess", "inference", "postprocess")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="Existing completed Stage 2 candidate bundle directory")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--images", nargs="+", help="Explicit image paths; inputs are not copied or logged by filename")
    source.add_argument("--synthetic-fixture", action="store_true", help="Use a deterministic generated raster; report is synthetic")
    parser.add_argument("--backend", choices=("cpu", "mps", "both"), default="cpu")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--output", required=True, help="Output JSON report path")
    return parser


def _load_input_images(paths: list[str] | None, *, synthetic_fixture: bool) -> tuple[list[dict], list[dict]]:
    if synthetic_fixture:
        # Deterministic synthetic raster pixels; no person identity or source photos.
        height, width = 480, 640
        y, x = np.indices((height, width), dtype=np.uint16)
        raster = np.stack(((3 * x + y) % 256, (x + 5 * y) % 256,
                           (7 * x + 2 * y) % 256), axis=2).astype(np.uint8)
        payload = cv2.imencode(".png", raster)[1].tobytes()
        return [raster], [{"sha256": hashlib.sha256(payload).hexdigest(),
                            "width": width, "height": height}]
    if not paths:
        raise ValueError("Provide one or more --images or choose --synthetic-fixture")
    if len(paths) > MAX_INPUTS:
        raise ValueError(f"Input count exceeds bounded limit of {MAX_INPUTS}")
    images, metadata = [], []
    total_pixels = 0
    for source in paths:
        path = Path(source)
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Every input must be a regular non-symlink image file")
            size = path.stat().st_size
        except OSError:
            raise ValueError("Unable to inspect an input image") from None
        if size <= 0 or size > MAX_IMAGE_BYTES:
            raise ValueError("Input image exceeds bounded file-size limit")
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            raise ValueError("Unable to read an input image") from None
        if len(data) != size or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Input image changed during bounded read")
        try:
            with Image.open(io.BytesIO(data)) as header:
                width, height = header.size
                image_format = (header.format or "").upper()
                frame_count = getattr(header, "n_frames", 1)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError("Input is not a readable supported image") from None
        if image_format not in {"JPEG", "PNG", "WEBP", "BMP", "TIFF"} or frame_count != 1:
            raise ValueError("Input image format is unsupported or animated")
        if (width < 16 or height < 16 or width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS):
            raise ValueError("Input image dimensions exceed supported bounds")
        total_pixels += width * height
        if total_pixels > MAX_TOTAL_INPUT_PIXELS:
            raise ValueError("Combined input pixel count exceeds the retained-memory budget")
        try:
            raster = cv2.imdecode(np.frombuffer(data, dtype=np.uint8),
                                  cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        except Exception:
            raise ValueError("Unable to decode an input image") from None
        if raster is None or raster.ndim != 3 or raster.shape[2] != 3:
            raise ValueError("Input is not a supported color raster image")
        if raster.shape[:2] != (height, width):
            raise ValueError("Decoded raster dimensions disagree with the bounded image header")
        images.append(raster)
        metadata.append({"sha256": hashlib.sha256(data).hexdigest(),
                         "width": width, "height": height})
    return images, metadata


def _percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), 95, method="linear"))


def _timing_summary(values: list[float]) -> dict:
    return {"count": len(values),
            "median_ms": float(statistics.median(values)) if values else None,
            "p95_ms": _percentile95(values)}


def _safe_error(exc: BaseException, secret_paths: list[str]) -> dict:
    message = " ".join(str(exc).split())
    for path in secret_paths:
        if path:
            message = message.replace(path, "[path]")
            message = message.replace(Path(path).name, "[file]")
    return {"type": type(exc).__name__, "message": message[:500]}


def _runtime_environment() -> dict:
    def sysctl_value(name: str) -> str | None:
        try:
            result = subprocess.run(["sysctl", "-n", name], check=True, capture_output=True,
                                    text=True, timeout=1.0)
            value = result.stdout.strip()
            return value or None
        except (OSError, subprocess.SubprocessError):
            return None
    info = {"platform": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor() or None, "cpu_count": os.cpu_count(),
            "macos_version": platform.mac_ver()[0] or None,
            "hardware_model": sysctl_value("hw.model"),
            "cpu_model": sysctl_value("machdep.cpu.brand_string"),
            "python_version": platform.python_version(), "numpy_version": np.__version__,
            "opencv_version": cv2.__version__}
    try:
        import torch
        info["torch_version"] = torch.__version__
        info["mps_available"] = bool(getattr(getattr(torch.backends, "mps", None), "is_available", lambda: False)())
    except Exception as exc:
        info["torch_version"] = None
        info["mps_available"] = False
        info["torch_probe_error"] = type(exc).__name__
    try:
        import ultralytics
        info["ultralytics_version"] = getattr(ultralytics, "__version__", None)
    except Exception:
        info["ultralytics_version"] = None
    return info


def _set_benchmark_threads() -> dict:
    """Apply the same bounded Torch/OpenCV CPU thread settings for every pass."""
    import torch
    torch.set_num_threads(BENCHMARK_THREADS)
    cv2.setNumThreads(BENCHMARK_THREADS)
    return _thread_counts()


def _thread_counts() -> dict:
    import torch
    return {"requested": BENCHMARK_THREADS, "torch": int(torch.get_num_threads()),
            "opencv": int(cv2.getNumThreads())}


def _instrument_predictor_stages(model, backend_status: Callable[[], dict], *,
                                 perf_counter: Callable[[], float] = time.perf_counter,
                                 synchronize: Callable[[], None] | None = None) -> dict:
    """Measure existing Ultralytics predictor stages on this detector instance only."""
    predictor = getattr(model, "predictor", None)
    if predictor is None:
        return {"sample_values": {name: [] for name in BODY_PREDICTOR_STAGES},
                "missing": list(BODY_PREDICTOR_STAGES),
                "reason": "PersonDetector model has no initialized Ultralytics predictor after warmup."}
    samples = {name: [] for name in BODY_PREDICTOR_STAGES}
    missing = []
    if synchronize is None:
        def synchronize():
            import torch
            torch.mps.synchronize()
    for name in BODY_PREDICTOR_STAGES:
        original = getattr(predictor, name, None)
        if not callable(original):
            missing.append(name)
            continue

        def measured(*args, _method=original, _name=name, **kwargs):
            before = backend_status()
            on_mps_before = before.get("active") == "mps"
            if on_mps_before:
                synchronize()
            started = perf_counter()
            try:
                return _method(*args, **kwargs)
            finally:
                after = backend_status()
                if on_mps_before or after.get("active") == "mps":
                    synchronize()
                elapsed = (perf_counter() - started) * 1000.0
                samples[_name].append(elapsed)

        try:
            setattr(predictor, name, measured)
        except Exception:
            missing.append(name)
    return {"sample_values": samples, "missing": missing,
            "reason": ("Unavailable predictor methods: " + ", ".join(missing)) if missing else None}


def _finish_predictor_stage_timings(instrumentation: dict, repeats: int) -> dict:
    missing = set(instrumentation["missing"])
    timing_rows = {}
    partial_reasons = []
    if instrumentation.get("reason"):
        partial_reasons.append(instrumentation["reason"])
    for name in BODY_PREDICTOR_STAGES:
        values = instrumentation["sample_values"][name]
        if name in missing:
            timing_rows[name] = None
        else:
            timing_rows[name] = {"count": len(values), "samples_ms": list(values),
                                 "median_ms": float(statistics.median(values)) if values else None,
                                 "p95_ms": _percentile95(values)}
            if len(values) != repeats:
                partial_reasons.append(f"Predictor {name} ran {len(values)} times for {repeats} measured detector calls.")
    return {"status": "partial" if partial_reasons else "available",
            "timings_ms": timing_rows,
            "reason": "; ".join(partial_reasons) or None}


def _atomic_output(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_benchmark(candidate_path: str | Path, *, images: list[np.ndarray], image_metadata: list[dict],
                  backend: str = "cpu", repeats: int = 10, warmup: int = 2,
                  synthetic: bool = False,
                  descriptor_fn: Callable | None = None,
                  detector_factory: Callable | None = None,
                  predictor_factory: Callable | None = None,
                  clock: Callable[[], float] = time.perf_counter) -> dict:
    if backend not in {"cpu", "mps", "both"}:
        raise ValueError("backend must be cpu, mps, or both")
    if type(repeats) is not int or not 1 <= repeats <= MAX_REPEATS:
        raise ValueError(f"repeats must be an integer from 1 to {MAX_REPEATS}")
    if type(warmup) is not int or not 0 <= warmup <= MAX_WARMUP:
        raise ValueError(f"warmup must be an integer from 0 to {MAX_WARMUP}")
    if not images or len(images) != len(image_metadata) or len(images) > MAX_INPUTS:
        raise ValueError("A bounded nonempty image list and matching metadata are required")

    candidate = Path(candidate_path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("Candidate path must be an existing non-symlink bundle directory")
    body_weights = ROOT / "yolov8n.pt"
    classifier_weights = ROOT / "yolov8n-cls.pt"
    for path in (body_weights, classifier_weights):
        if path.is_symlink() or not path.is_file() or path.stat().st_size < 1024:
            raise ValueError("Required trusted local model weights are missing or incomplete; downloads are disabled")

    # Validate the immutable Stage 2 bundle before constructing any model loader.
    if descriptor_fn is None:
        from core.uniform_model_registry import candidate_descriptor
        descriptor_fn = candidate_descriptor
    descriptor = descriptor_fn(candidate)
    if not isinstance(descriptor, dict) or not descriptor.get("id"):
        raise ValueError("Candidate validation did not return a stable model ID")

    if detector_factory is None:
        from core.person_detector import PersonDetector
        detector_factory = PersonDetector
    if predictor_factory is None:
        from core.uniform_model_registry import CandidatePredictor
        predictor_factory = CandidatePredictor

    requests = ["cpu", "mps"] if backend == "both" else [backend]
    backend_reports = []
    secret_paths = [str(candidate.resolve()), *(str(item) for item in image_metadata if "path" in item)]
    for requested in requests:
        stage = {"requested_backend": requested,
                 "candidate": {"status": "not_started"}, "body_detector": {"status": "not_started"}}
        predictor = None
        try:
            started = clock()
            predictor = predictor_factory(candidate, backend=requested)
            stage["candidate"]["cold_load_ms"] = (clock() - started) * 1000.0
            candidate_warmups = 0
            candidate_warmup_threads = []
            candidate_measured_threads = []
            for index in range(warmup):
                _set_benchmark_threads()
                result = predictor.timed_probabilities(images[index % len(images)])
                if (not isinstance(result, dict) or not isinstance(result.get("timings"), dict)
                        or not isinstance(result.get("probabilities"), dict)):
                    raise ValueError("Candidate timed_probabilities returned an invalid timing object")
                candidate_warmups += 1
                candidate_warmup_threads.append(_thread_counts())
            samples: dict[str, list[float]] = {"preprocessing_ms": [], "inference_ms": [], "total_ms": []}
            placements = []
            for index in range(repeats):
                _set_benchmark_threads()
                result = predictor.timed_probabilities(images[index % len(images)])
                timings = result["timings"]
                if not isinstance(result.get("probabilities"), dict):
                    raise ValueError("Candidate timed_probabilities returned invalid probabilities")
                for key in samples:
                    value = timings.get(key)
                    if type(value) not in {int, float} or not np.isfinite(value) or value < 0:
                        raise ValueError(f"Candidate returned invalid {key} timing")
                    samples[key].append(float(value))
                if isinstance(result.get("backend"), dict):
                    placements.append(result["backend"])
                candidate_measured_threads.append(_thread_counts())
            candidate_status = predictor.backend_status()
            if not isinstance(candidate_status, dict):
                candidate_status = placements[-1] if placements else {}
            stage["candidate"].update(status="completed", warmup_count=candidate_warmups,
                                      timings={key: _timing_summary(values) for key, values in samples.items()},
                                      backend_status=candidate_status,
                                      placement_observations=_unique_statuses(placements),
                                      thread_settings={"requested": BENCHMARK_THREADS,
                                                       "warmup_observations": _unique_statuses(candidate_warmup_threads),
                                                       "measured_observations": _unique_statuses(candidate_measured_threads),
                                                       "measured_sample_count": len(candidate_measured_threads)})
        except Exception as exc:
            stage["candidate"].update(status="error", error=_safe_error(exc, secret_paths))
            if predictor is not None:
                try:
                    stage["candidate"]["backend_status"] = predictor.backend_status()
                except Exception:
                    pass

        # PersonDetector detects bodies only here; no face or pose model is called.
        detector = None
        try:
            detector = detector_factory(backend=requested)
            started = clock()
            body_model = detector._ensure_model()  # explicit cold-load timing; local weights were preflighted above
            body_cold_load = (clock() - started) * 1000.0
            initial_status = detector.backend_status()
            if body_model is None:
                raise RuntimeError("Body detector local model failed to load")
            if initial_status.get("active") == "unavailable" or initial_status.get("requested") != requested:
                raise RuntimeError("Body detector backend initialization failed or changed the request")
            body_warmup_threads = []
            for index in range(warmup):
                _set_benchmark_threads()
                detector.detect_persons(images[index % len(images)])
                if detector.last_error:
                    raise RuntimeError("Body detector warmup inference failed")
                body_warmup_threads.append(_thread_counts())
            body_threads = []
            predictor_stage_instrumentation = _instrument_predictor_stages(
                detector._model, detector.backend_status)
            body_samples = []
            detection_counts = []
            for index in range(repeats):
                _set_benchmark_threads()
                started = clock()
                boxes = detector.detect_persons(images[index % len(images)])
                body_samples.append((clock() - started) * 1000.0)
                detection_counts.append(len(boxes))
                body_threads.append(_thread_counts())
                if detector.last_error:
                    raise RuntimeError("Body detector measured inference failed")
            predictor_stages = _finish_predictor_stage_timings(predictor_stage_instrumentation, repeats)
            stage["body_detector"].update(status="completed", cold_load_ms=body_cold_load,
                warmup_count=warmup, timings={"total_ms": _timing_summary(body_samples)},
                predictor_stages=predictor_stages,
                thread_settings={"requested": BENCHMARK_THREADS,
                                 "warmup_observations": _unique_statuses(body_warmup_threads),
                                 "measured_observations": _unique_statuses(body_threads),
                                 "measured_sample_count": len(body_threads)},
                detections={"sample_count": len(detection_counts),
                            "median_per_frame": float(statistics.median(detection_counts)),
                            "counts": detection_counts},
                measurement_scope="end_to_end_detect_persons_call plus synchronized predictor stage wrappers",
                backend_status=detector.backend_status())
        except Exception as exc:
            stage["body_detector"].update(status="error", error=_safe_error(exc, secret_paths),
                                           backend_status=(detector.backend_status() if detector is not None else None))
        backend_reports.append(stage)

    return {"schema_version": 1, "benchmark_kind": "raw_model_stage_timings",
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "synthetic_inputs": bool(synthetic), "accuracy_evaluation": False,
            "passage_metrics": {"available": False,
                                "reason": "Raw detector/classifier stage timing only; no passage or attendance stream was run."},
            "input_count": len(images), "inputs": image_metadata,
            "candidate": {"id": descriptor["id"],
                          "preprocessing_version": descriptor.get("preprocessing_version"),
                          "dataset_version": descriptor.get("dataset_version"),
                          "synthetic_candidate": bool(descriptor.get("synthetic"))},
            "configuration": {"requested_backend": backend, "backends": requests,
                              "repeats": repeats, "warmup": warmup,
                              "image_size": "original raster for body; predictor's fixed validated preprocessing for classifier"},
            "environment": _runtime_environment(), "results": backend_reports,
            "limitations": ["Timing results are not accuracy, policy, passage, or deployment evidence.",
                            "No smaller input size or alternate preprocessing was evaluated.",
                            "Body inference reports requested and actual placement; a CPU fallback remains explicit.",
                            "Candidate preprocessing/inference use synchronized timings; body stage wrappers synchronize and add instrumentation overhead.",
                            "No queue-delay value is reported because this benchmark does not measure queueing.",
                            "Images were processed in memory; filenames and pixels are not included in this report."]}


def _unique_statuses(values: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for value in values:
        key = json.dumps(value, sort_keys=True, default=str)
        if key not in seen:
            unique.append(value)
            seen.add(key)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= MAX_REPEATS:
        parser.error(f"--repeats must be between 1 and {MAX_REPEATS}")
    if not 0 <= args.warmup <= MAX_WARMUP:
        parser.error(f"--warmup must be between 0 and {MAX_WARMUP}")
    try:
        images, metadata = _load_input_images(args.images, synthetic_fixture=args.synthetic_fixture)
        report = run_benchmark(args.candidate, images=images, image_metadata=metadata,
                               backend=args.backend, repeats=args.repeats, warmup=args.warmup,
                               synthetic=args.synthetic_fixture)
        output = Path(args.output)
        if output.is_symlink():
            raise ValueError("Output path cannot be a symlink")
        resolved_output = output.resolve()
        candidate_root = Path(args.candidate).resolve()
        if candidate_root == resolved_output or candidate_root in resolved_output.parents:
            raise ValueError("Output JSON must be outside the immutable candidate bundle")
        for source in (args.images or []):
            if Path(source).resolve() == resolved_output:
                raise ValueError("Output JSON cannot overwrite an input image")
        _atomic_output(output, report)
    except Exception as exc:
        safe = _safe_error(exc, [args.candidate, *(args.images or [])])
        print(f"benchmark error: {safe['type']}: {safe['message']}", file=sys.stderr)
        return 2
    print(f"Wrote benchmark report: {output}")
    failed = any(stage.get(part, {}).get("status") != "completed"
                 for stage in report.get("results", [])
                 for part in ("candidate", "body_detector"))
    if failed:
        print("One or more backend stages failed; partial results were written.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
