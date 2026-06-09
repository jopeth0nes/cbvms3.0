#!/usr/bin/env python3
"""Label audit for the noisy 'wrong_uniform' class.

The 'wrong' folder is the suspect class: it may contain images that are actually the
CORRECT uniform (mislabeled), which teaches the classifier to call good polos "wrong".
This script renders a contact sheet of every image in a class folder (default
wrong_uniform) and runs two independent opinions on each:

  * classifier  -> ViolationTrainer.predict_proba('uniform')  (the trained YOLOv8-cls)
  * colour      -> UniformColorMatcher.is_uniform()           (deterministic hue match)

Each tile is bordered:
  RED    = SUSPECT MISLABEL  -> at least one opinion says this is the correct uniform
  YELLOW = the two opinions DISAGREE (but neither clearly calls it correct)
  GREEN  = both agree it is wrong (consistent with the folder label)

A CSV with the raw numbers is written alongside the sheet so findings are auditable.

By default both opinions look at the WHOLE stored frame, because the current model and
colour reference were both built on whole 224x224 frames. Use --on crop to instead run
them on the inference torso crop.

Usage:
    python scripts/audit_wrong_labels.py
    python scripts/audit_wrong_labels.py --label wrong_uniform --on crop
    python scripts/audit_wrong_labels.py --cols 12 --thumb 160
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.person_detector import PersonDetector          # noqa: E402
from core.trainer import MODULES, ViolationTrainer        # noqa: E402
from core.uniform_matcher import UniformColorMatcher      # noqa: E402

_EXTS = (".jpg", ".jpeg", ".png")
# BGR colours for tile borders.
_RED, _YELLOW, _GREEN, _GREY = (0, 0, 255), (0, 215, 255), (0, 200, 0), (120, 120, 120)


def _torso(detector: PersonDetector, img: np.ndarray) -> np.ndarray:
    """Inference torso crop; falls back to the whole image so every photo is judged."""
    boxes = detector.detect_persons(img)
    if boxes:
        crop = detector.get_torso_crop(img, boxes[0])
        if crop is not None and crop.size > 0:
            return crop
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit a class folder for mislabeled images.")
    ap.add_argument("--module", default="uniform", choices=sorted(MODULES))
    ap.add_argument("--label", default="wrong_uniform", help="class folder to audit")
    ap.add_argument("--src", default="data/training")
    ap.add_argument("--on", choices=["whole", "crop"], default="whole",
                    help="run opinions on the whole frame (default) or the torso crop")
    ap.add_argument("--cols", type=int, default=10, help="thumbnails per row")
    ap.add_argument("--thumb", type=int, default=140, help="thumbnail size (px)")
    ap.add_argument("--out", default="data/runs/uniform/label_audit", help="output folder")
    args = ap.parse_args()

    module, label = args.module, args.label
    if label not in MODULES[module]["labels"]:
        print(f"ERROR: '{label}' not a label of module '{module}' ({MODULES[module]['labels']})")
        return 1

    src_dir = (ROOT / args.src / module / label).resolve()
    if not src_dir.exists():
        print(f"ERROR: folder not found: {src_dir}")
        return 1
    files = sorted(f for f in src_dir.iterdir() if f.suffix.lower() in _EXTS)
    if not files:
        print(f"No images in {src_dir}")
        return 1

    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    detector = PersonDetector()
    trainer = ViolationTrainer()
    colour = UniformColorMatcher()
    correct_lbl = "correct_uniform" if "correct_uniform" in MODULES[module]["labels"] else MODULES[module]["labels"][0]

    if not trainer.is_trained(module):
        print(f"WARN: no trained '{module}' model — classifier opinion will be blank.")
    if not colour.is_loaded():
        print("WARN: uniform colour reference not built — colour opinion will be blank.")

    print(f"Auditing {len(files)} '{label}' images (opinions on: {args.on})...\n")

    rows: list[dict] = []
    tiles: list[np.ndarray] = []
    n_suspect = n_disagree = 0
    t = args.thumb

    for i, f in enumerate(files):
        img = cv2.imread(str(f))
        if img is None:
            continue
        target = _torso(detector, img) if args.on == "crop" else img

        # Classifier opinion: P(correct uniform).
        proba = trainer.predict_proba(module, target)
        cls_pcorrect = float(proba.get(correct_lbl, 0.0)) if proba else None
        cls_says_correct = cls_pcorrect is not None and cls_pcorrect >= 0.5

        # Colour opinion: hue-match verdict + P(correct).
        col_verdict, col_p = colour.is_uniform(target)  # (None|bool, float)
        col_says_correct = col_verdict is True

        # Either model thinking it's the real uniform == likely mislabeled into 'wrong'.
        suspect = cls_says_correct or col_says_correct
        # Disagreement between the two opinions (only meaningful when both have a verdict).
        disagree = (cls_pcorrect is not None and col_verdict is not None
                    and cls_says_correct != col_says_correct)

        if suspect:
            n_suspect += 1
        if disagree and not suspect:
            n_disagree += 1

        rows.append({
            "file": f.name,
            "cls_p_correct": "" if cls_pcorrect is None else f"{cls_pcorrect:.3f}",
            "cls_says_correct": int(cls_says_correct),
            "colour_p_correct": f"{col_p:.3f}",
            "colour_verdict": {True: "correct", False: "wrong", None: "n/a"}[col_verdict],
            "SUSPECT_MISLABEL": int(suspect),
            "DISAGREE": int(disagree),
        })

        # Build the bordered thumbnail.
        thumb = cv2.resize(img, (t, t), interpolation=cv2.INTER_AREA)
        border = _RED if suspect else (_YELLOW if disagree else _GREEN)
        thumb = cv2.copyMakeBorder(thumb, 3, 22, 3, 3, cv2.BORDER_CONSTANT, value=border)
        cap_c = "?" if cls_pcorrect is None else f"{cls_pcorrect:.2f}"
        cap = f"C{cap_c} H{col_p:.2f}"
        cv2.putText(thumb, cap, (5, t + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)
        tiles.append(thumb)

        if i % 25 == 0:
            print(f"  ...{i}/{len(files)}")

    # Assemble the grid.
    cols = max(1, args.cols)
    tw = tiles[0].shape[1]
    th = tiles[0].shape[0]
    rows_n = (len(tiles) + cols - 1) // cols
    sheet = np.full((rows_n * th, cols * tw, 3), 30, dtype=np.uint8)
    for idx, tile in enumerate(tiles):
        r, c = divmod(idx, cols)
        sheet[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = tile

    sheet_path = out_dir / f"contact_sheet_{label}.png"
    csv_path = out_dir / f"audit_{label}.csv"
    cv2.imwrite(str(sheet_path), sheet)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("\n" + "=" * 60)
    print(f"Audited {len(rows)} '{label}' images.")
    print(f"  RED    SUSPECT MISLABEL (looks like correct uniform): {n_suspect}")
    print(f"  YELLOW classifier/colour DISAGREE:                    {n_disagree}")
    print(f"  GREEN  both agree 'wrong':                            {len(rows) - n_suspect - n_disagree}")
    print(f"\nContact sheet: {sheet_path}")
    print(f"CSV:           {csv_path}")
    if n_suspect:
        print(f"\nReview the {n_suspect} RED tiles — move any genuine correct-uniform photos out of "
              f"'{label}' before retraining.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
