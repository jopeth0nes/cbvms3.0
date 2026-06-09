#!/usr/bin/env python3
"""Step 4 — diagnose WHICH signal flags a correctly-worn polo as 'wrong'.

Captures a few raw frames from the camera, then on each frame reproduces the live torso
localization (YOLO person box -> 0.20-0.65 * height slice, largest-box fallback — the same
geometry the fixed live path now uses) and prints every uniform signal on that crop:

  * classifier   ViolationTrainer.predict_proba('uniform')   P(correct)
  * embedder     UniformEmbedMatcher.prob_correct()          P(correct)
  * colour       UniformColorMatcher.is_uniform()            P(correct)
  * fused (live) fuse_uniform_prob(embed, colour) = min      P(correct)  <- current live verdict

It dumps the actual torso crop fed to the verdict (frame_XX_torso.jpg) so we confirm it is a
tight torso, not full scene, and prints a summary of which signal drags the verdict to 'wrong'.

Raw frames + crops are written to data/runs/uniform/diag_frames/.

Usage:
    python scripts/diagnose_uniform.py                 # auto camera, 8 frames
    python scripts/diagnose_uniform.py --n 10 --warmup 5
    python scripts/diagnose_uniform.py --index 1
    python scripts/diagnose_uniform.py --url rtsp://...
    python scripts/diagnose_uniform.py --analyze-only   # re-analyze already-captured frames
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.camera import CameraCapture                                  # noqa: E402
from core.person_detector import PersonDetector                       # noqa: E402
from core.trainer import ViolationTrainer                             # noqa: E402
from core.uniform_matcher import UniformColorMatcher, fuse_uniform_prob  # noqa: E402

try:
    from core.uniform_embedder import UniformEmbedMatcher
except Exception:
    UniformEmbedMatcher = None

OUT_DIR = ROOT / "data" / "runs" / "uniform" / "diag_frames"


def capture(n: int, index, url, warmup: float, delay: float) -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cam = CameraCapture(camera_index=index, source_url=url)
    print(f"Opening camera (index={index}, url={url})...")
    if not cam.open():
        print(f"ERROR: could not open camera: {cam.last_error}")
        print("On macOS, grant Camera permission to your terminal/VS Code in "
              "System Settings > Privacy & Security > Camera, then retry.")
        return []
    print(f"Camera open (index {cam.camera_index}). Warming up {warmup:.0f}s — get in frame...")
    t0 = time.time()
    while time.time() - t0 < warmup:
        cam.read()
        time.sleep(0.03)

    paths: list[Path] = []
    for i in range(n):
        frame = None
        for _ in range(10):           # a few tries per shot
            frame = cam.read()
            if frame is not None and frame.size > 0:
                break
            time.sleep(0.03)
        if frame is None:
            print(f"  frame {i}: read failed")
            continue
        p = OUT_DIR / f"frame_{i:02d}.jpg"
        cv2.imwrite(str(p), frame)
        paths.append(p)
        print(f"  captured {p.name}  {frame.shape[1]}x{frame.shape[0]}")
        time.sleep(delay)
    cam.release()
    return paths


def torso_crop(detector: PersonDetector, frame: np.ndarray):
    """Reproduce the fixed live localization: largest person box -> 0.20-0.65 slice."""
    boxes = detector.detect_persons(frame)
    if not boxes:
        return None, None
    box = boxes[0]                                  # largest by area
    crop = detector.get_torso_crop(frame, box)      # 0.20-0.65 slice, clamps, >=32px
    return (crop if crop is not None and crop.size else None), box


def _f(p):
    return "  n/a " if p is None else f"{p:5.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose the uniform verdict on captured frames.")
    ap.add_argument("--n", type=int, default=8, help="frames to capture")
    ap.add_argument("--index", type=int, default=None, help="camera index (default auto 0/1)")
    ap.add_argument("--url", default=None, help="IP/RTSP camera URL")
    ap.add_argument("--warmup", type=float, default=4.0, help="seconds before first capture")
    ap.add_argument("--delay", type=float, default=0.8, help="seconds between captures")
    ap.add_argument("--analyze-only", action="store_true", help="skip capture, reuse saved frames")
    args = ap.parse_args()

    if args.analyze_only:
        frames = sorted(OUT_DIR.glob("frame_*.jpg"))
        frames = [f for f in frames if "_torso" not in f.name]
        print(f"Analyzing {len(frames)} existing frames in {OUT_DIR}")
    else:
        frames = capture(args.n, args.index, args.url, args.warmup, args.delay)
    if not frames:
        return 1

    detector = PersonDetector()
    trainer = ViolationTrainer()
    colour = UniformColorMatcher()
    embed = UniformEmbedMatcher() if UniformEmbedMatcher is not None else None

    print(f"\nReferences: classifier_trained={trainer.is_trained('uniform')}  "
          f"colour_loaded={colour.is_loaded()}  "
          f"embedder_loaded={embed.is_loaded() if embed else False}")
    print("\n  frame            crop      cls    emb(off) colour  FUSED=min(cls,col)  -> verdict")
    print("  " + "-" * 80)

    agg = {"cls": [], "embed": [], "colour": [], "fused": []}
    n_wrong = 0
    for f in frames:
        img = cv2.imread(str(f))
        if img is None:
            continue
        crop, box = torso_crop(detector, img)
        if crop is None:
            print(f"  {f.name:14}  NO PERSON/torso crop (box={box})")
            continue
        cv2.imwrite(str(f.with_name(f.stem + "_torso.jpg")), crop)

        proba = trainer.predict_proba("uniform", crop)
        p_cls = float(proba.get("correct_uniform")) if proba else None

        p_embed = None
        if embed is not None and embed.is_loaded():
            v, p = embed.prob_correct(crop)
            p_embed = None if v is None else p
        p_col = None
        if colour.is_loaded():
            v, p = colour.is_uniform(crop)
            p_col = None if v is None else p
        # NEW live verdict = min(classifier, colour). The embedder column below is shown only
        # for reference — it was dropped from the live path (Step 4/5).
        fused = fuse_uniform_prob(p_cls, p_col)

        verdict = "?" if fused is None else ("correct" if fused >= 0.5 else "WRONG")
        if fused is not None and fused < 0.5:
            n_wrong += 1
        ch, cw = crop.shape[:2]
        print(f"  {f.name:14}  {cw:3}x{ch:<3}  {_f(p_cls)}  {_f(p_embed)}  {_f(p_col)}   "
              f"{_f(fused)}     -> {verdict}")
        for k, v in (("cls", p_cls), ("embed", p_embed), ("colour", p_col), ("fused", fused)):
            if v is not None:
                agg[k].append(v)

    print("  " + "-" * 78)
    def mean(xs):
        return sum(xs) / len(xs) if xs else None
    print(f"  MEAN P(correct):  cls={_f(mean(agg['cls']))}  embed={_f(mean(agg['embed']))}  "
          f"colour={_f(mean(agg['colour']))}  fused={_f(mean(agg['fused']))}")
    print(f"  Frames the LIVE fused verdict called WRONG: {n_wrong}/{len(frames)}")

    # Which LIVE signal (classifier/colour) is weakest on these correctly-worn polos?
    means = {k: mean(agg[k]) for k in ("cls", "colour") if mean(agg[k]) is not None}
    if means:
        worst = min(means, key=means.get)
        print(f"\n  Weakest LIVE signal on the correct polo = '{worst}' "
              f"(mean P(correct)={means[worst]:.3f}). embed(off) shown for reference only.")
    print(f"\n  Raw frames + torso crops: {OUT_DIR}")
    print("  Inspect the *_torso.jpg files to confirm the crop is a tight torso, not full scene.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
