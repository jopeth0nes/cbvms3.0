#!/usr/bin/env python3
"""Steps 1-2 diagnosis — prove WHERE the torso crop lands at close range.

Pulls the real uniform-violation snapshots from data/cbvms.db (the actual person/face crops
that produced "Wrong uniform"), and on each reproduces:

  * OLD region — YOLO person box -> fixed 0.20-0.65 * person-height slice
                 (PersonDetector.get_torso_box, the geometry the live worker uses today).
  * NEW region — pose-anchored shoulders+hips (PersonDetector.pose_torso_box), falling back
                 to the chin-anchored chest box (get_uniform_region) when pose can't anchor.

For each crop it measures skin_fraction() (high = the crop sits on the face/neck) and runs the
unchanged verdict signals (classifier P(correct), colour P(correct), fused = min) so we can see
the verdict flip from WRONG (face) to correct (chest).

Outputs to data/diag/:
  snap_<id>_panel.jpg   original with OLD (red) and NEW (green) boxes drawn
  snap_<id>_old.jpg     the OLD fixed-fraction crop
  snap_<id>_new.jpg     the NEW pose / face-anchored crop

Usage:
    python scripts/diagnose_torso_crop.py            # all uniform snapshots, top-5 by old-skin%
    python scripts/diagnose_torso_crop.py --top 8
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.person_detector import PersonDetector, skin_fraction, _SKIN_FRAC_MAX  # noqa: E402
from core.trainer import ViolationTrainer                              # noqa: E402
from core.uniform_matcher import UniformColorMatcher, fuse_uniform_prob  # noqa: E402

DB_PATH = ROOT / "data" / "cbvms.db"
OUT_DIR = ROOT / "data" / "diag"

RED = (60, 60, 230)      # BGR — OLD fixed-fraction box
GREEN = (60, 200, 90)    # BGR — NEW pose / face-anchored box

_recognizer = None
_recognizer_tried = False


def _get_recognizer():
    """Lazy-load the face detector only if a frame needs the face-anchored fallback."""
    global _recognizer, _recognizer_tried
    if _recognizer_tried:
        return _recognizer
    _recognizer_tried = True
    try:
        from core.recognizer import FaceRecognizer
        from database.db_manager import CBVMSDatabase
        _recognizer = FaceRecognizer(CBVMSDatabase())
    except Exception as exc:
        print(f"  [warn] face detector unavailable for fallback: {exc}")
        _recognizer = None
    return _recognizer


def _clamp(box, w, h):
    x1, y1, x2, y2 = box
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 - x1 < 32 or y2 - y1 < 32:
        return None
    return [x1, y1, x2, y2]


def old_region(detector: PersonDetector, img: np.ndarray):
    """Live geometry today: largest person box -> 0.20-0.65 slice."""
    boxes = detector.detect_persons(img)
    if not boxes:
        return None, None
    box = boxes[0]
    h, w = img.shape[:2]
    return _clamp(detector.get_torso_box(box), w, h), box


def new_region(detector: PersonDetector, img: np.ndarray, person_box):
    """Pose-anchored torso; fall back to chin-anchored chest box when pose can't anchor."""
    region = detector.pose_torso_box(img, person_box)
    if region is not None:
        return region, "pose"
    rec = _get_recognizer()
    if rec is not None:
        try:
            faces = rec.detect_faces(img)
        except Exception:
            faces = []
        if faces:
            region = detector.get_uniform_region(faces[0]["box"], img.shape, person_box)
            if region is not None:
                return region, "face"
    return None, "none"


def _signals(trainer, colour, crop):
    if crop is None or crop.size == 0:
        return None, None, None
    proba = trainer.predict_proba("uniform", crop)
    p_cls = float(proba.get("correct_uniform")) if proba else None
    p_col = None
    if colour.is_loaded():
        v, p = colour.is_uniform(crop)
        p_col = None if v is None else p
    return p_cls, p_col, fuse_uniform_prob(p_cls, p_col)


def _f(p):
    return " n/a " if p is None else f"{p:5.2f}"


def _verdict(fused):
    return "?" if fused is None else ("correct" if fused >= 0.5 else "WRONG")


def _guarded(new_box, new_skin, fused):
    """Step-3 preview: what the live worker would actually emit after the abstain guard.

    No torso anchored, or a crop that is predominantly skin → ABSTAIN (no verdict), instead of
    classifying a face. Otherwise the normal fused verdict stands.
    """
    if new_box is None or new_skin is None:
        return "ABSTAIN"
    if new_skin > _SKIN_FRAC_MAX:
        return "ABSTAIN"
    return _verdict(fused)


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose torso-crop placement (OLD vs NEW).")
    ap.add_argument("--top", type=int, default=5, help="how many worst-by-old-skin frames to feature")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(DB_PATH))
    rows = con.execute(
        "SELECT id, violation_type, snapshot FROM violations "
        "WHERE violation_type LIKE '%uniform%' AND snapshot IS NOT NULL "
        "ORDER BY id DESC"
    ).fetchall()
    con.close()
    if not rows:
        print("No uniform-violation snapshots in the DB.")
        return 1

    detector = PersonDetector()
    trainer = ViolationTrainer()
    colour = UniformColorMatcher()
    print(f"classifier_trained={trainer.is_trained('uniform')}  colour_loaded={colour.is_loaded()}")
    print(f"Decoded {len(rows)} uniform snapshots from {DB_PATH.name}\n")

    results = []
    for vid, vtype, blob in rows:
        if not blob:
            continue
        img = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            continue
        h, w = img.shape[:2]

        old_box, person_box = old_region(detector, img)
        new_box, method = new_region(detector, img, person_box)

        old_crop = img[old_box[1]:old_box[3], old_box[0]:old_box[2]] if old_box else None
        new_crop = img[new_box[1]:new_box[3], new_box[0]:new_box[2]] if new_box else None

        old_skin = skin_fraction(old_crop) if old_crop is not None else None
        new_skin = skin_fraction(new_crop) if new_crop is not None else None
        o_cls, o_col, o_fused = _signals(trainer, colour, old_crop)
        n_cls, n_col, n_fused = _signals(trainer, colour, new_crop)

        panel = img.copy()
        if old_box:
            cv2.rectangle(panel, (old_box[0], old_box[1]), (old_box[2], old_box[3]), RED, 2)
            cv2.putText(panel, "OLD", (old_box[0] + 2, old_box[1] + 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, RED, 2)
        if new_box:
            cv2.rectangle(panel, (new_box[0], new_box[1]), (new_box[2], new_box[3]), GREEN, 2)
            cv2.putText(panel, f"NEW/{method}", (new_box[0] + 2, new_box[3] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 2)
        cv2.imwrite(str(OUT_DIR / f"snap_{vid}_panel.jpg"), panel)
        if old_crop is not None and old_crop.size:
            cv2.imwrite(str(OUT_DIR / f"snap_{vid}_old.jpg"), old_crop)
        if new_crop is not None and new_crop.size:
            cv2.imwrite(str(OUT_DIR / f"snap_{vid}_new.jpg"), new_crop)

        results.append(dict(
            vid=vid, size=f"{w}x{h}", old_skin=old_skin, new_skin=new_skin, method=method,
            has_new=new_box is not None,
            o_cls=o_cls, o_col=o_col, o_fused=o_fused, n_cls=n_cls, n_col=n_col, n_fused=n_fused,
            guarded=_guarded(new_box, new_skin, n_fused),
        ))

    # Feature the worst offenders: frames where the OLD crop is most skin (clearest face-landing).
    ranked = sorted(results, key=lambda r: (r["old_skin"] or -1.0), reverse=True)
    feat = ranked[: args.top]

    print("Worst OLD crops (most skin = sitting on the face/neck) — OLD vs NEW on the SAME frame:")
    print("  id    size       | OLD skin% fused verdict | NEW skin% fused verdict  via  | GUARDED (live)")
    print("  " + "-" * 100)
    for r in feat:
        os_ = " n/a" if r["old_skin"] is None else f"{r['old_skin'] * 100:4.0f}%"
        ns_ = " n/a" if r["new_skin"] is None else f"{r['new_skin'] * 100:4.0f}%"
        print(f"  {r['vid']:<5} {r['size']:<10} |   {os_}  {_f(r['o_fused'])} {_verdict(r['o_fused']):>7} "
              f"|   {ns_}  {_f(r['n_fused'])} {_verdict(r['n_fused']):>7}  {r['method']:<4} | {r['guarded']}")

    def _mean(key):
        xs = [r[key] for r in results if r[key] is not None]
        return sum(xs) / len(xs) if xs else None

    print("  " + "-" * 104)
    print(f"  MEAN over {len(results)} snapshots:  OLD skin={_pct(_mean('old_skin'))}  "
          f"NEW skin={_pct(_mean('new_skin'))}   |   "
          f"OLD fused={_f(_mean('o_fused'))}  NEW fused={_f(_mean('n_fused'))}")
    n_old_wrong = sum(1 for r in results if r["o_fused"] is not None and r["o_fused"] < 0.5)
    n_new_wrong = sum(1 for r in results if r["n_fused"] is not None and r["n_fused"] < 0.5)
    g_wrong = sum(1 for r in results if r["guarded"] == "WRONG")
    g_correct = sum(1 for r in results if r["guarded"] == "correct")
    g_abstain = sum(1 for r in results if r["guarded"] == "ABSTAIN")
    print(f"  Frames called WRONG (raw):  OLD={n_old_wrong}/{len(results)}   NEW={n_new_wrong}/{len(results)}")
    print(f"  GUARDED live outcome (Step-3 skin/abstain applied):  "
          f"correct={g_correct}  WRONG={g_wrong}  ABSTAIN={g_abstain}  (of {len(results)})")
    print(f"\n  Panels + crops written to: {OUT_DIR}")
    print("  Open snap_<id>_panel.jpg (OLD=red, NEW=green) and snap_<id>_old/new.jpg to compare.")
    return 0


def _pct(x):
    return " n/a " if x is None else f"{x * 100:4.0f}%"


if __name__ == "__main__":
    raise SystemExit(main())
