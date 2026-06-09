#!/usr/bin/env python3
"""Diagnose a single failing frame across every uniform-decision signal.

Point this at the frame where a correctly-worn polo was flagged "Wrong uniform 77%".
It reproduces the live region selection and prints what EACH signal says, so you can see
exactly which one produces the false positive:

  * YOLOv8-cls classifier  -> ViolationTrainer.predict_proba('uniform')
  * garment embedder       -> UniformEmbedMatcher.prob_correct()   (live path)
  * colour matcher         -> UniformColorMatcher.is_uniform()     (live path)
  * fused (min, EMA-less)  -> fuse_uniform_prob(embed, colour)     (what the dashboard shows)

IMPORTANT: the live dashboard verdict comes from the embedder+colour fusion on a
POSE-anchored torso, NOT from the YOLOv8-cls classifier (LiveViolationChecker.check is
unused). This script reports both the pose crop and the 0.20-0.65 box-slice crop so you
can tell which region/signal is responsible.

Usage:
    python scripts/test_frame.py --frame /path/to/failing_frame.jpg
    python scripts/test_frame.py --frame frame.jpg --save-crop
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.person_detector import PersonDetector                       # noqa: E402
from core.trainer import MODULES, ViolationTrainer                    # noqa: E402
from core.uniform_matcher import UniformColorMatcher, fuse_uniform_prob  # noqa: E402

try:
    from core.uniform_embedder import UniformEmbedMatcher
except Exception:  # pragma: no cover - optional heavy backbone
    UniformEmbedMatcher = None


def _fmt(p):
    return "  n/a" if p is None else f"{p:6.3f}"


def _report_region(name, crop, trainer, embed, colour):
    if crop is None or crop.size == 0:
        print(f"\n[{name}] no crop available")
        return
    h, w = crop.shape[:2]
    print(f"\n[{name}] crop {w}x{h}px")

    proba = trainer.predict_proba("uniform", crop)
    if proba:
        pc = proba.get("correct_uniform")
        pw = proba.get("wrong_uniform")
        top = max(proba, key=proba.get)
        print(f"   classifier   P(correct)={_fmt(pc)}  P(wrong)={_fmt(pw)}  top1={top}")
    else:
        print("   classifier   (no trained model)")

    p_embed = None
    if embed is not None and embed.is_loaded():
        v, p = embed.prob_correct(crop)
        p_embed = None if v is None else p
        print(f"   embedder     P(correct)={_fmt(p_embed)}  verdict={v}")
    else:
        print("   embedder     (not loaded)")

    p_col = None
    if colour.is_loaded():
        v, p = colour.is_uniform(crop)
        p_col = None if v is None else p
        print(f"   colour       P(correct)={_fmt(p_col)}  verdict={v}")
    else:
        print("   colour       (not loaded)")

    fused = fuse_uniform_prob(p_embed, p_col)
    if fused is not None:
        is_correct = fused >= 0.5
        conf = fused if is_correct else (1.0 - fused)
        verdict = "correct_uniform" if is_correct else "wrong_uniform"
        print(f"   >> LIVE fuse P(correct)={_fmt(fused)}  -> {verdict} ({conf:.0%})  "
              f"[dashboard shows this region's value]")


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose a failing uniform frame.")
    ap.add_argument("--frame", required=True, help="path to the frame image")
    ap.add_argument("--save-crop", action="store_true", help="save the torso crops next to the frame")
    args = ap.parse_args()

    fp = Path(args.frame).expanduser()
    if not fp.exists():
        print(f"ERROR: frame not found: {fp}")
        return 1
    frame = cv2.imread(str(fp))
    if frame is None:
        print(f"ERROR: could not read image: {fp}")
        return 1

    detector = PersonDetector()
    trainer = ViolationTrainer()
    colour = UniformColorMatcher()
    embed = UniformEmbedMatcher() if UniformEmbedMatcher is not None else None

    H, W = frame.shape[:2]
    print(f"Frame {W}x{H}  {fp}")
    boxes = detector.detect_persons(frame)
    print(f"Persons detected: {len(boxes)}")

    box_crop = None
    pose_crop = detector.pose_torso_crop(frame)  # whole-image pose (live prefers this)
    if boxes:
        print(f"Largest person box: {boxes[0]}")
        box_crop = detector.get_torso_crop(frame, boxes[0])  # 0.20-0.65 slice (PLAN.md)
        if pose_crop is None:
            pose_crop = detector.pose_torso_crop(frame, boxes[0])

    # The classifier path documented in PLAN.md uses the 0.20-0.65 box slice.
    _report_region("0.20-0.65 box-slice torso (classifier path / PLAN.md)",
                   box_crop, trainer, embed, colour)
    # The live dashboard prefers the pose-anchored torso.
    _report_region("pose-anchored torso (live dashboard path)",
                   pose_crop, trainer, embed, colour)

    if args.save_crop:
        if box_crop is not None and box_crop.size:
            p = fp.with_name(fp.stem + "_boxcrop.jpg")
            cv2.imwrite(str(p), box_crop)
            print(f"\nsaved {p}")
        if pose_crop is not None and pose_crop.size:
            p = fp.with_name(fp.stem + "_posecrop.jpg")
            cv2.imwrite(str(p), pose_crop)
            print(f"saved {p}")

    print("\nInterpretation: whichever region shows P(correct) < 0.5 with conf >= 0.65 is what "
          "produced the false 'Wrong uniform' alert. Compare the box-slice vs pose crops above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
