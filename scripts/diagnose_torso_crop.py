#!/usr/bin/env python3
"""Verify WHERE the uniform torso crop lands — and prove the hard face floor works.

For each input frame this reproduces the two geometries on the SAME image:

  * OLD region — YOLO person box -> fixed 0.20-0.65 * person-height slice
                 (PersonDetector.get_torso_box, the geometry the live worker used before).
  * NEW region — the EXACT live path now: PersonDetector.chest_region(frame, face_box,
                 person_box) — pose shoulders+hips when available, else the face-anthropometry
                 chest box, with EVERY candidate clamped so its top is forced below the chin
                 (chin_y + 0.10*face_h). Same function the dashboard worker calls.

It draws the face box (yellow) + chin line (cyan) + floor line (magenta), annotates the NEW
box top as a signed multiple of face-height relative to the chin, measures skin_fraction()
(high = sitting on the face/neck), and runs the unchanged verdict signals (classifier
P(correct), colour P(correct), fused = min) so the WRONG(face) -> correct(chest) flip is visible.

Two input sources (the plan runs BOTH):
  DB mode (default): pulls the real '%uniform%' violation snapshots from data/cbvms.db.
                     NB those snapshots are PERSON-BOX crops, so the face box is re-detected
                     on them — second-hand but real failing pixels.
  Capture mode:      grabs fresh close-range frames from the live camera (true live pixels,
                     face box + person box detected first-hand) — stand in the blue polo.

Outputs to data/diag/:
  <tag>_panel.jpg   original with OLD (red) / NEW (green) / face (yellow) / chin+floor lines
  <tag>_old.jpg     the OLD fixed-fraction crop
  <tag>_new.jpg     the FINAL NEW crop sent to the classifier

Usage:
    python scripts/diagnose_torso_crop.py --top 8          # DB uniform snapshots, worst-by-skin
    python scripts/diagnose_torso_crop.py --capture 8      # fresh close-range capture (blue polo)
    python scripts/diagnose_torso_crop.py --capture 8 --warmup 5 --index 1
    python scripts/diagnose_torso_crop.py --analyze-capture # re-analyze already-captured frames
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.person_detector import PersonDetector, skin_fraction  # noqa: E402
from core.trainer import ViolationTrainer                              # noqa: E402
from core.uniform_matcher import UniformColorMatcher, fuse_uniform_prob  # noqa: E402

DB_PATH = ROOT / "data" / "cbvms.db"
OUT_DIR = ROOT / "data" / "diag"
CAP_DIR = OUT_DIR / "capture"

# Mirrors dashboard.UNIFORM_SKIN_ABSTAIN — the live skin-abstain threshold (after clamping).
SKIN_ABSTAIN = 0.40

RED = (60, 60, 230)      # BGR — OLD fixed-fraction box
GREEN = (60, 200, 90)    # BGR — NEW chest_region box (exact live path)
YELLOW = (40, 220, 240)  # BGR — recognised face box
CYAN = (230, 230, 60)    # BGR — chin line (face bottom)
MAGENTA = (230, 60, 230)  # BGR — hard floor line (chin + 0.10*face_h)

_recognizer = None
_recognizer_tried = False


def _get_recognizer():
    """Lazy-load the face detector (needed to anchor the NEW chest_region)."""
    global _recognizer, _recognizer_tried
    if _recognizer_tried:
        return _recognizer
    _recognizer_tried = True
    try:
        from core.recognizer import FaceRecognizer
        from database.db_manager import CBVMSDatabase
        _recognizer = FaceRecognizer(CBVMSDatabase())
    except Exception as exc:
        print(f"  [warn] face detector unavailable: {exc}")
        _recognizer = None
    return _recognizer


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def _clamp(box, w, h):
    x1, y1, x2, y2 = box
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 - x1 < 32 or y2 - y1 < 32:
        return None
    return [x1, y1, x2, y2]


def _largest_face(img):
    rec = _get_recognizer()
    if rec is None:
        return None
    try:
        faces = rec.detect_faces(img)
    except Exception:
        faces = []
    if not faces:
        return None
    return max(faces, key=lambda f: _area(f["box"]))["box"]


def old_region(detector: PersonDetector, img: np.ndarray):
    """Geometry the live worker used before: largest person box -> 0.20-0.65 slice."""
    boxes = detector.detect_persons(img)
    if not boxes:
        return None, None
    box = boxes[0]
    h, w = img.shape[:2]
    return _clamp(detector.get_torso_box(box), w, h), box


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
    """What the live worker emits after the skin-abstain guard (mirrors dashboard)."""
    if new_box is None:
        return "ABSTAIN"
    if new_skin is not None and new_skin > SKIN_ABSTAIN:
        return "ABSTAIN"
    return _verdict(fused)


def _top_rel_chin(new_box, face_box):
    """NEW box top as a signed multiple of face-height relative to the chin (fy2)."""
    if new_box is None or face_box is None:
        return None
    fy2 = int(face_box[3])
    fh = int(face_box[3]) - int(face_box[1])
    if fh <= 0:
        return None
    return (new_box[1] - fy2) / fh


def process_frame(detector, trainer, colour, tag, img, results):
    """Run OLD + live NEW geometry on one frame, dump panel + crops, collect metrics."""
    h, w = img.shape[:2]
    old_box, person_box = old_region(detector, img)
    face_box = _largest_face(img)

    new_box, method = (None, "noface")
    if face_box is not None:
        new_box, method = detector.chest_region(img, face_box, person_box)

    old_crop = img[old_box[1]:old_box[3], old_box[0]:old_box[2]] if old_box else None
    new_crop = img[new_box[1]:new_box[3], new_box[0]:new_box[2]] if new_box else None

    old_skin = skin_fraction(old_crop) if old_crop is not None else None
    new_skin = skin_fraction(new_crop) if new_crop is not None else None
    o_cls, o_col, o_fused = _signals(trainer, colour, old_crop)
    n_cls, n_col, n_fused = _signals(trainer, colour, new_crop)
    top_rel = _top_rel_chin(new_box, face_box)

    panel = img.copy()
    if face_box is not None:
        fb = [int(v) for v in face_box]
        cv2.rectangle(panel, (fb[0], fb[1]), (fb[2], fb[3]), YELLOW, 2)
        cv2.putText(panel, "face", (fb[0] + 2, fb[1] - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, YELLOW, 2)
        fh = fb[3] - fb[1]
        cv2.line(panel, (0, fb[3]), (w, fb[3]), CYAN, 1)                 # chin line
        floor = int(fb[3] + 0.10 * fh)
        cv2.line(panel, (0, floor), (w, floor), MAGENTA, 1)             # hard floor line
    if old_box:
        cv2.rectangle(panel, (old_box[0], old_box[1]), (old_box[2], old_box[3]), RED, 2)
        cv2.putText(panel, "OLD", (old_box[0] + 2, old_box[1] + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, RED, 2)
    if new_box:
        lbl = f"NEW/{method}"
        if top_rel is not None:
            lbl += f" top=chin{top_rel:+.2f}fh"
        cv2.rectangle(panel, (new_box[0], new_box[1]), (new_box[2], new_box[3]), GREEN, 2)
        cv2.putText(panel, lbl, (new_box[0] + 2, new_box[3] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 2)
    cv2.imwrite(str(OUT_DIR / f"{tag}_panel.jpg"), panel)
    if old_crop is not None and old_crop.size:
        cv2.imwrite(str(OUT_DIR / f"{tag}_old.jpg"), old_crop)
    if new_crop is not None and new_crop.size:
        cv2.imwrite(str(OUT_DIR / f"{tag}_new.jpg"), new_crop)

    results.append(dict(
        tag=tag, size=f"{w}x{h}", old_skin=old_skin, new_skin=new_skin, method=method,
        has_new=new_box is not None, top_rel=top_rel,
        o_cls=o_cls, o_col=o_col, o_fused=o_fused, n_cls=n_cls, n_col=n_col, n_fused=n_fused,
        guarded=_guarded(new_box, new_skin, n_fused),
    ))


def _capture_frames(n, index, url, warmup, delay) -> list[Path]:
    """Grab n fresh frames from the live camera (reuses the diagnose_uniform pattern)."""
    from core.camera import CameraCapture
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    cam = CameraCapture(camera_index=index, source_url=url)
    print(f"Opening camera (index={index}, url={url})...")
    if not cam.open():
        print(f"ERROR: could not open camera: {cam.last_error}")
        print("On macOS, grant Camera permission to your terminal/VS Code in "
              "System Settings > Privacy & Security > Camera, then retry.")
        return []
    print(f"Camera open (index {cam.camera_index}). Warming up {warmup:.0f}s — "
          f"get CLOSE in the blue polo (seated, leaning in)...")
    t0 = time.time()
    while time.time() - t0 < warmup:
        cam.read()
        time.sleep(0.03)
    paths: list[Path] = []
    for i in range(n):
        frame = None
        for _ in range(10):
            frame = cam.read()
            if frame is not None and frame.size > 0:
                break
            time.sleep(0.03)
        if frame is None:
            print(f"  frame {i}: read failed")
            continue
        p = CAP_DIR / f"cap_{i:02d}.jpg"
        cv2.imwrite(str(p), frame)
        paths.append(p)
        print(f"  captured {p.name}  {frame.shape[1]}x{frame.shape[0]}")
        time.sleep(delay)
    cam.release()
    return paths


def _print_table(results, feat, label):
    print(f"\n{label} — OLD vs NEW (live chest_region) on the SAME frame:")
    print("  tag        size       | OLD skin% fused verd | NEW skin% fused verd  via   top      | GUARDED")
    print("  " + "-" * 104)
    for r in feat:
        os_ = " n/a" if r["old_skin"] is None else f"{r['old_skin'] * 100:4.0f}%"
        ns_ = " n/a" if r["new_skin"] is None else f"{r['new_skin'] * 100:4.0f}%"
        tr = "  n/a " if r["top_rel"] is None else f"chin{r['top_rel']:+.2f}fh"
        print(f"  {str(r['tag']):<10} {r['size']:<10} |   {os_} {_f(r['o_fused'])} {_verdict(r['o_fused']):>5} "
              f"|   {ns_} {_f(r['n_fused'])} {_verdict(r['n_fused']):>5}  {r['method']:<4} {tr} | {r['guarded']}")

    def _mean(key):
        xs = [r[key] for r in results if r[key] is not None]
        return sum(xs) / len(xs) if xs else None

    print("  " + "-" * 104)
    print(f"  MEAN over {len(results)} frames:  OLD skin={_pct(_mean('old_skin'))}  "
          f"NEW skin={_pct(_mean('new_skin'))}   |   "
          f"OLD fused={_f(_mean('o_fused'))}  NEW fused={_f(_mean('n_fused'))}")
    n_old_wrong = sum(1 for r in results if r["o_fused"] is not None and r["o_fused"] < 0.5)
    n_new_wrong = sum(1 for r in results if r["n_fused"] is not None and r["n_fused"] < 0.5)
    g_wrong = sum(1 for r in results if r["guarded"] == "WRONG")
    g_correct = sum(1 for r in results if r["guarded"] == "correct")
    g_abstain = sum(1 for r in results if r["guarded"] == "ABSTAIN")
    print(f"  Frames called WRONG (raw):  OLD={n_old_wrong}/{len(results)}   NEW={n_new_wrong}/{len(results)}")
    print(f"  GUARDED live outcome (skin>{SKIN_ABSTAIN:.0%} -> abstain):  "
          f"correct={g_correct}  WRONG={g_wrong}  ABSTAIN={g_abstain}  (of {len(results)})")
    print(f"\n  Panels + crops written to: {OUT_DIR}")
    print("  Open <tag>_panel.jpg (OLD=red NEW=green face=yellow chin=cyan floor=magenta) "
          "and <tag>_new.jpg to inspect the exact pixels classified.")


def run_db(detector, trainer, colour, top):
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        return 1
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
    print(f"Decoded {len(rows)} uniform snapshots from {DB_PATH.name}")

    results: list[dict] = []
    for vid, vtype, blob in rows:
        if not blob:
            continue
        img = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            continue
        process_frame(detector, trainer, colour, f"snap_{vid}", img, results)

    # Feature the worst OLD crops (most skin = clearest face-landing).
    ranked = sorted(results, key=lambda r: (r["old_skin"] or -1.0), reverse=True)
    _print_table(results, ranked[:top], "WORST OLD crops (most skin = on the face/neck)")
    return 0


def run_capture(detector, trainer, colour, args):
    if args.analyze_capture:
        frames = sorted(CAP_DIR.glob("cap_*.jpg"))
        print(f"Analyzing {len(frames)} existing capture frames in {CAP_DIR}")
    else:
        frames = _capture_frames(args.capture, args.index, args.url, args.warmup, args.delay)
    if not frames:
        return 1
    results: list[dict] = []
    for f in frames:
        img = cv2.imread(str(f))
        if img is None or img.size == 0:
            continue
        process_frame(detector, trainer, colour, f.stem, img, results)
    _print_table(results, results, "FRESH CAPTURE (close-range blue polo)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify torso-crop placement (OLD vs live chest_region).")
    ap.add_argument("--top", type=int, default=5, help="DB mode: how many worst-by-old-skin frames to feature")
    ap.add_argument("--capture", type=int, default=0, help="capture N fresh frames from the camera instead of DB")
    ap.add_argument("--analyze-capture", action="store_true", help="re-analyze already-captured cap_*.jpg frames")
    ap.add_argument("--index", type=int, default=None, help="camera index (default auto)")
    ap.add_argument("--url", default=None, help="IP/RTSP camera URL")
    ap.add_argument("--warmup", type=float, default=4.0, help="seconds before first capture")
    ap.add_argument("--delay", type=float, default=0.8, help="seconds between captures")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    detector = PersonDetector()
    trainer = ViolationTrainer()
    colour = UniformColorMatcher()
    print(f"classifier_trained={trainer.is_trained('uniform')}  colour_loaded={colour.is_loaded()}  "
          f"skin_abstain={SKIN_ABSTAIN:.0%}")

    if args.capture > 0 or args.analyze_capture:
        return run_capture(detector, trainer, colour, args)
    return run_db(detector, trainer, colour, args.top)


def _pct(x):
    return " n/a " if x is None else f"{x * 100:4.0f}%"


if __name__ == "__main__":
    raise SystemExit(main())
