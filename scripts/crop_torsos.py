#!/usr/bin/env python3
"""Pre-crop training photos to the SAME torso region the classifier sees at inference.

Root cause of the "Wrong uniform 77%" false positive: the uniform classifier was
trained on full-body / full-room frames (data/training/uniform/<class>/*.jpg, stored
letterboxed 224x224 by ViolationTrainer.add_sample), but the live pipeline feeds it a
tight torso crop. Train and inference distributions therefore disagree, and the model
latches onto background context (bright tiled rooms vs dark outdoor scenes) instead of
the shirt.

This script rebuilds the dataset so training matches inference EXACTLY:

  for each data/training/<module>/<class>/*.jpg
      person box  = PersonDetector.detect_persons()[0]      # largest person (COCO cls 0)
      torso crop  = person_box[0.20 .. 0.65 * ph] slice     # PLAN.md section 4 geometry
      reject if   < 32x32 px (same as PersonDetector._MIN_CROP_PX)
      save        _letterbox(crop, 224)  ->  data/training_cropped/<module>/<class>/

Geometry, BGR handling and the 224 letterbox are taken from the SAME code paths used at
inference (PersonDetector.get_torso_crop + core.trainer._letterbox), so there is one
source of truth. We do NOT touch the inference crop geometry — we move the training data
onto it.

Note on resolution: the stored source frames are already letterboxed to 224x224, so the
extracted torsos are small (often 32-90 px) and get upscaled by the letterbox, exactly as
a distant live crop would be. Re-collecting full-resolution source photos would yield
crisper crops, but cropping the existing frames already fixes the dominant problems
(distribution mismatch + background shortcut).

Usage:
    python scripts/crop_torsos.py                 # uniform module, default dirs
    python scripts/crop_torsos.py --module uniform --clean
    python scripts/crop_torsos.py --src data/training --dst data/training_cropped
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Single source of truth for geometry + preprocessing — imported from the live code.
from core.person_detector import (  # noqa: E402
    PersonDetector,
    _MIN_CROP_PX,
    _TORSO_TOP_FRAC,
    _TORSO_BOT_FRAC,
)
from core.trainer import IMG_SIZE, MODULES, _letterbox          # noqa: E402

_EXTS = (".jpg", ".jpeg", ".png")


def crop_class(
    detector: PersonDetector,
    src_dir: Path,
    dst_dir: Path,
    *,
    min_px: int,
) -> dict[str, int]:
    """Crop every image in one class folder. Returns count stats."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in src_dir.iterdir() if f.suffix.lower() in _EXTS)

    stats = {"total": len(files), "written": 0, "no_person": 0, "too_small": 0, "unreadable": 0}
    for f in files:
        img = cv2.imread(str(f))  # BGR, exactly like ViolationTrainer.predict() input
        if img is None:
            stats["unreadable"] += 1
            print(f"  [unreadable] {f.name}")
            continue

        boxes = detector.detect_persons(img)
        if not boxes:
            stats["no_person"] += 1
            print(f"  [no person ] {f.name}")
            continue

        # Identical 0.20-0.65 * ph torso slice from the largest person box (PLAN.md s.4).
        # get_torso_crop already clamps to frame and returns None when < 32x32.
        crop = detector.get_torso_crop(img, boxes[0])
        if crop is None or crop.size == 0:
            stats["too_small"] += 1
            print(f"  [crop<{min_px}px] {f.name}")
            continue

        # Letterbox to 224 EXACTLY as ViolationTrainer.predict() does to its live crop,
        # so a stored training image == the tensor produced at inference time.
        out = _letterbox(crop, IMG_SIZE)
        cv2.imwrite(str(dst_dir / f"{f.stem}.jpg"), out, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        stats["written"] += 1

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-crop training photos to the inference torso region.")
    ap.add_argument("--module", default="uniform", choices=sorted(MODULES), help="module to crop")
    ap.add_argument("--src", default="data/training", help="source dataset root (<root>/<module>/<class>)")
    ap.add_argument("--dst", default="data/training_cropped", help="output dataset root")
    ap.add_argument("--min-px", type=int, default=_MIN_CROP_PX, help="reject crops smaller than this (px)")
    ap.add_argument("--clean", action="store_true", help="wipe the module's output folder first")
    args = ap.parse_args()

    module = args.module
    labels = MODULES[module]["labels"]
    src_root = (ROOT / args.src / module).resolve()
    dst_root = (ROOT / args.dst / module).resolve()

    if not src_root.exists():
        print(f"ERROR: source not found: {src_root}")
        return 1
    if args.clean and dst_root.exists():
        shutil.rmtree(dst_root, ignore_errors=True)

    print(f"Cropping module '{module}'  {src_root}  ->  {dst_root}")
    print(f"Torso geometry: {_TORSO_TOP_FRAC}-{_TORSO_BOT_FRAC} * person-box height "
          f"(matches inference) | letterbox -> {IMG_SIZE}px | min {args.min_px}px\n")

    detector = PersonDetector()
    grand = {"total": 0, "written": 0, "no_person": 0, "too_small": 0, "unreadable": 0}
    for label in labels:
        src_dir = src_root / label
        if not src_dir.exists():
            print(f"[skip] missing class folder: {src_dir}")
            continue
        print(f"== {label} ==")
        s = crop_class(detector, src_dir, dst_root / label, min_px=args.min_px)
        no_crop = s["no_person"] + s["too_small"] + s["unreadable"]
        print(f"   total {s['total']}  written {s['written']}  "
              f"NO-VALID-CROP {no_crop}  (no_person {s['no_person']}, "
              f"too_small {s['too_small']}, unreadable {s['unreadable']})\n")
        for k in grand:
            grand[k] += s[k]

    no_crop = grand["no_person"] + grand["too_small"] + grand["unreadable"]
    print("=" * 60)
    print(f"DONE  {grand['written']}/{grand['total']} images cropped.")
    print(f"      {no_crop} produced NO valid crop "
          f"(no_person {grand['no_person']}, too_small {grand['too_small']}, "
          f"unreadable {grand['unreadable']}).")
    print(f"      Cropped dataset: {dst_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
