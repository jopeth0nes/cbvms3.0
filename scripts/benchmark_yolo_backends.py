#!/usr/bin/env python3
"""Sequential same-image CPU/MPS body-YOLO execution and placement check.

Uses only the public sample images bundled with installed Ultralytics. It records
counts/timings/device placement, never frames, boxes, identities, or database data.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cbvms-camera-assessment-mpl"))
os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "cbvms-camera-assessment-yolo"))

import cv2
import ultralytics

from core.person_detector import PersonDetector


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * p
    lo = int(index)
    hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (index - lo), 3)


def run(backend, images, repeats):
    detector = PersonDetector(backend=backend)
    timings, counts = [], []
    for repeat in range(repeats + 1):  # first pass warms the loaded model
        for image in images:
            started = time.perf_counter()
            boxes = detector.detect_persons(image)
            elapsed = 1000 * (time.perf_counter() - started)
            if repeat:
                timings.append(elapsed)
                counts.append(len(boxes))
    return {"requested_backend": backend, "status": detector.backend_status(),
            "samples": len(timings), "person_detections": counts,
            "detections_nonempty": bool(counts) and all(count > 0 for count in counts),
            "elapsed_ms": {"p50": percentile(timings, .5), "p95": percentile(timings, .95),
                           "p99": percentile(timings, .99)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("cpu", "mps", "both"), default="both")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 100:
        parser.error("--repeats must be from 1 to 100")
    assets = Path(ultralytics.__file__).resolve().parent / "assets"
    names = ("bus.jpg", "zidane.jpg")
    paths = [assets / name for name in names]
    if not all(path.is_file() for path in paths):
        parser.error("installed Ultralytics public sample images are unavailable")
    images = [cv2.imread(str(path)) for path in paths]
    if not all(image is not None and image.size for image in images):
        parser.error("could not read installed sample images")
    backends = ("cpu", "mps") if args.backend == "both" else (args.backend,)
    results = [run(backend, images, args.repeats) for backend in backends]
    print(json.dumps({"fixture": "installed_ultralytics_bus_and_zidane",
                      "images": len(images), "repeats_per_image": args.repeats,
                      "weights": "existing yolov8n.pt", "settings": {"conf": .25, "iou": .5,
                      "classes": [0], "input": "original BGR image"},
                      "accuracy_evaluated": False, "results": results}, sort_keys=True))


if __name__ == "__main__":
    main()
