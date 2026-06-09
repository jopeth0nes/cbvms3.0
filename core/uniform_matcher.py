"""One-class uniform check by color signature.

A binary "correct vs wrong uniform" classifier needs many examples of BOTH classes.
Real deployments have abundant photos of the *correct* uniform and almost none of
"wrong", so a learned classifier degenerates to always-"correct". This matcher instead
asks a one-class question — "is the torso the uniform's colour?" — using only the
correct-uniform photos to learn the uniform's hue:

  * achromatic clothing (white / grey / black) → low chroma            → wrong
  * chromatic but a different colour (red / green) → low blue-ratio     → wrong
  * the uniform colour (e.g. blue polo) → chromatic + matching hue      → correct

Measured on the real dataset: correct polo crops have chroma-fraction ≈ 0.27 and
hue-match ≈ 0.35, while white/grey/black sit at 0.00 — a wide, reliable gap.
Pure cv2 + numpy; no model, no training, no "wrong" photos.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
_REF_PATH = _ROOT / "models" / "uniform_color_ref.json"

# A pixel counts as "chromatic" (coloured, not white/grey/black) when its saturation and
# brightness are in this range. Black letterbox / blown highlights are ignored.
_SAT_MIN = 35
_V_MIN = 35
_V_MAX = 250

_HUE_HALF_WINDOW = 20          # uniform hue band = peak ± this (OpenCV hue 0-179)

# Verdict thresholds — generous, since non-uniform clothing sits at ~0 on both.
_CHROMA_FRAC_MIN = 0.08        # ≥8% of body pixels must be coloured
_HUE_RATIO_MIN = 0.20         # ≥20% of those coloured pixels must be the uniform hue


def _logistic(x: float, center: float, scale: float) -> float:
    """Smooth 0→1 ramp through 0.5 at ``center`` (width ~``scale``)."""
    try:
        return float(1.0 / (1.0 + np.exp(-(x - center) / scale)))
    except Exception:
        return 0.5


def fuse_uniform_prob(p_cls: float | None, p_col: float | None) -> float | None:
    """Combine the trained-classifier and colour-matcher P(correct) as NECESSARY conditions.

    A torso is the correct uniform only if BOTH the learned classifier AND the uniform-colour
    match agree — so we take the weaker (``min``) of the two signals, not a weighted average.
    This lets the colour check veto a classifier that overfits its (imbalanced) training set
    (e.g. flags a beige shirt as "correct"), and lets the classifier veto a same-colour but
    wrong-style top. Degrades to whichever single signal is available, or None when neither is.
    """
    vals = [float(p) for p in (p_cls, p_col) if p is not None]
    if not vals:
        return None
    return min(vals)


class UniformColorMatcher:
    """Detects whether a torso crop shows the enrolled uniform colour (one-class)."""

    def __init__(self) -> None:
        self._hue_lo: int | None = None
        self._hue_hi: int | None = None
        self._n_samples: int = 0
        self.load()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        return self._hue_lo is not None

    @property
    def sample_count(self) -> int:
        return self._n_samples

    def load(self) -> bool:
        try:
            if not _REF_PATH.exists():
                return False
            data = json.loads(_REF_PATH.read_text())
            self._hue_lo = int(data["hue_lo"])
            self._hue_hi = int(data["hue_hi"])
            self._n_samples = int(data.get("n_samples", 0))
            return True
        except Exception as exc:
            print(f"[UniformMatcher] load failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Reference building (from the correct-uniform photos only)
    # ------------------------------------------------------------------

    def build_from_dir(self, correct_dir: str | os.PathLike, on_progress=None) -> bool:
        """Derive the uniform's dominant chromatic hue from the correct-uniform photos."""
        files = sorted(glob.glob(os.path.join(str(correct_dir), "*.jpg")))
        if not files:
            return False
        hue_acc = np.zeros(180, dtype=np.float64)
        used = 0
        for i, f in enumerate(files):
            if on_progress and i % 50 == 0:
                on_progress(f"Calibrating uniform colour… {i}/{len(files)}")
            img = cv2.imread(f)
            if img is None:
                continue
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            chroma = (S >= _SAT_MIN) & (V >= _V_MIN) & (V <= _V_MAX)
            if chroma.any():
                hist, _ = np.histogram(H[chroma], bins=180, range=(0, 180))
                hue_acc += hist
                used += 1
        if used == 0 or hue_acc.sum() == 0:
            return False

        peak = int(np.argmax(hue_acc))
        self._hue_lo = peak - _HUE_HALF_WINDOW
        self._hue_hi = peak + _HUE_HALF_WINDOW
        self._n_samples = len(files)
        try:
            _REF_PATH.parent.mkdir(parents=True, exist_ok=True)
            _REF_PATH.write_text(json.dumps({
                "hue_peak": peak, "hue_lo": self._hue_lo, "hue_hi": self._hue_hi,
                "n_samples": self._n_samples,
            }, indent=2))
        except Exception as exc:
            print(f"[UniformMatcher] save failed: {exc}")
        return True

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _hue_mask(self, H: np.ndarray) -> np.ndarray:
        """Boolean mask of pixels whose hue is in the uniform band (wrap-safe)."""
        lo, hi = self._hue_lo, self._hue_hi
        if lo < 0:               # band wraps past 0 (e.g. red)
            return (H >= (lo + 180)) | (H <= hi)
        if hi > 179:             # band wraps past 179
            return (H >= lo) | (H <= (hi - 180))
        return (H >= lo) & (H <= hi)

    def is_uniform(self, crop_bgr: np.ndarray) -> tuple[bool | None, float]:
        """Classify a torso crop.

        Returns (verdict, p_correct): verdict True = correct uniform, False = wrong,
        None = can't judge (empty / too dark a crop) → caller should stay neutral.
        ``p_correct`` ∈ [0,1] is a calibrated probability that the torso shows the uniform
        colour — high only when the crop is both sufficiently chromatic AND the right hue.
        """
        if not self.is_loaded() or crop_bgr is None or crop_bgr.size == 0:
            return None, 0.0
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        body = (V >= _V_MIN) & (V <= _V_MAX)
        n_body = int(body.sum())
        if n_body < 50:
            return None, 0.0  # too little usable area to judge

        chroma = body & (S >= _SAT_MIN)
        chroma_frac = float(chroma.sum()) / float(n_body)
        n_chroma = int(chroma.sum())
        hue_ratio = float((chroma & self._hue_mask(H)).sum()) / n_chroma if n_chroma else 0.0

        # Calibrated probability: a logistic on each cue (centred on its decision
        # threshold) combined via geometric mean so BOTH must hold to read "correct".
        # At the thresholds both cues sit at ~0.5 → p_correct ≈ 0.5 (the verdict boundary).
        chroma_score = _logistic(chroma_frac, _CHROMA_FRAC_MIN, 0.04)
        hue_score = _logistic(hue_ratio, _HUE_RATIO_MIN, 0.10)
        p_correct = float(np.sqrt(max(0.0, chroma_score * hue_score)))
        p_correct = min(1.0, max(0.0, p_correct))
        return (p_correct >= 0.5), p_correct
