"""Few-shot uniform matcher using a pretrained image encoder (no from-scratch training).

A small from-scratch YOLOv8 classifier trained on a handful of full-frame uniform photos does
not generalise to the live face-anchored chest crop (different framing/background/lighting) and
degenerates on imbalanced data — it mislabels the *real* uniform as "wrong" (and vice-versa).

This matcher instead uses a strong **pretrained** torchvision backbone (ResNet18, ImageNet) as a
frozen feature extractor and does **metric / prototype matching**:

  * encode the correct-uniform photos -> L2-normalised embeddings
  * cluster them into a few prototypes (k-means) covering pose/lighting/framing variation
  * at inference, encode the torso crop and score by the MAX cosine similarity to any prototype
  * calibrate the decision boundary using the (few) wrong-uniform photos as real negatives,
    biased toward recall so a genuine uniform under live framing still passes

Needs only the *correct* photos to define the uniform, generalises from few examples, and pairs
with the colour matcher. Pure torchvision + sklearn + numpy — no new dependency.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import cv2
import numpy as np

from core.trainer import _letterbox

_ROOT = Path(__file__).resolve().parents[1]
_REF_PATH = _ROOT / "models" / "uniform_embed_ref.npz"

_IMG_SIZE = 224
_MIN_CROP_PX = 40
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_MAX_PROTOTYPES = 16
_BATCH = 32
# Bump when the build pipeline changes so a stale reference is ignored and rebuilt.
# v2: prototypes built from person-detected TORSO crops (matches the live chest region).
# v3: pose-anchored (shoulders+hips) torso crops — stable across pose; matches the live crop.
_BUILD_VERSION = 3


class UniformEmbedMatcher:
    """Detects whether a torso crop is the enrolled uniform via pretrained-embedding similarity."""

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False
        self._pd = None                              # lazy PersonDetector for torso cropping
        self._prototypes: np.ndarray | None = None   # (k, D) L2-normalised
        self._boundary: float = 0.45
        self._scale: float = 0.12
        self._n_samples: int = 0
        self.load()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        return self._prototypes is not None and len(self._prototypes) > 0

    @property
    def sample_count(self) -> int:
        return self._n_samples

    def load(self) -> bool:
        try:
            if not _REF_PATH.exists():
                return False
            data = np.load(_REF_PATH, allow_pickle=False)
            if "build_version" not in data or int(data["build_version"]) != _BUILD_VERSION:
                return False  # stale build method → force a rebuild with the current pipeline
            self._prototypes = data["prototypes"].astype(np.float32)
            self._boundary = float(data["boundary"])
            self._scale = float(data["scale"])
            self._n_samples = int(data["n_samples"])
            return True
        except Exception as exc:
            print(f"[UniformEmbed] load failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Backbone (lazy)
    # ------------------------------------------------------------------

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None
        try:
            import torch
            from torchvision import models

            weights = models.ResNet18_Weights.DEFAULT
            net = models.resnet18(weights=weights)
            net.fc = torch.nn.Identity()   # 512-d penultimate embedding
            net.eval()
            self._torch = torch
            self._model = net
            return net
        except Exception as exc:
            print(f"[UniformEmbed] backbone load failed: {exc}")
            self._load_failed = True
            return None

    def _encode(self, frames_bgr: list[np.ndarray]) -> np.ndarray | None:
        """Encode a list of BGR crops to L2-normalised embeddings (N, D)."""
        net = self._ensure_model()
        if net is None or not frames_bgr:
            return None
        torch = self._torch
        out: list[np.ndarray] = []
        with torch.no_grad():
            for i in range(0, len(frames_bgr), _BATCH):
                batch = frames_bgr[i:i + _BATCH]
                tensors = []
                for f in batch:
                    proc = _letterbox(f, _IMG_SIZE)
                    rgb = cv2.cvtColor(proc, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                    rgb = (rgb - _IMAGENET_MEAN) / _IMAGENET_STD
                    tensors.append(torch.from_numpy(rgb.transpose(2, 0, 1)))
                x = torch.stack(tensors)
                emb = net(x).cpu().numpy().astype(np.float32)
                out.append(emb)
        embs = np.concatenate(out, axis=0)
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return embs / norms

    # ------------------------------------------------------------------
    # Reference building (from the correct-uniform photos; wrong = negatives)
    # ------------------------------------------------------------------

    def _torso_crop(self, img_bgr: np.ndarray) -> np.ndarray:
        """Extract the shirt region, matching the region scanned live. Prefers pose-anchored
        shoulders+hips (stable); falls back to the person-box torso slice, then the whole image."""
        if self._pd is None:
            try:
                from core.person_detector import PersonDetector
                self._pd = PersonDetector()
            except Exception:
                self._pd = False
        if self._pd:
            try:
                crop = self._pd.pose_torso_crop(img_bgr)          # shoulders+hips (preferred)
                if crop is not None and crop.size > 0:
                    return crop
                boxes = self._pd.detect_persons(img_bgr)
                if boxes:
                    crop = self._pd.get_torso_crop(img_bgr, boxes[0])  # box-fraction fallback
                    if crop is not None and crop.size > 0:
                        return crop
            except Exception:
                pass
        return img_bgr

    def _embed_dir(self, folder: str | os.PathLike, on_progress=None) -> np.ndarray | None:
        files = sorted(glob.glob(os.path.join(str(folder), "*.jpg")))
        if not files:
            return None
        frames: list[np.ndarray] = []
        for i, f in enumerate(files):
            if on_progress and i % 100 == 0:
                on_progress(f"Encoding uniform reference… {i}/{len(files)}")
            img = cv2.imread(f)
            if img is None:
                continue
            frames.append(self._torso_crop(img))   # match the live chest/torso region
        if not frames:
            return None
        return self._encode(frames)

    def build_from_dir(self, correct_dir, wrong_dir=None, on_progress=None) -> bool:
        """Build prototypes from the correct-uniform photos; calibrate with wrong photos if any."""
        correct = self._embed_dir(correct_dir, on_progress)
        if correct is None or len(correct) == 0:
            return False

        # k-means prototypes (covers pose/lighting/framing variation). Scale the cluster count
        # with the dataset so prototypes stay tight (few centres over-average many crops and
        # depress every match's similarity).
        k = int(min(_MAX_PROTOTYPES, max(6, len(correct) // 50)))
        k = min(k, len(correct))
        try:
            from sklearn.cluster import KMeans
            km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(correct)
            protos = km.cluster_centers_.astype(np.float32)
        except Exception:
            protos = correct.mean(axis=0, keepdims=True).astype(np.float32)
        pn = np.linalg.norm(protos, axis=1, keepdims=True)
        pn[pn == 0] = 1.0
        protos = protos / pn

        correct_sims = (correct @ protos.T).max(axis=1)   # each correct view's best match
        mc = float(np.median(correct_sims))

        wrong = None
        if wrong_dir is not None:
            wrong = self._embed_dir(wrong_dir, on_progress)
        if wrong is not None and len(wrong) >= 5:
            wrong_sims = (wrong @ protos.T).max(axis=1)
            mw = float(np.median(wrong_sims))
            # Sit the boundary just above the wrong distribution and bias HARD toward recall, so
            # a genuine uniform (live webcam chest crop ~0.93) passes comfortably while clearly
            # different garments are rejected. The colour matcher catches wrong-colour cases, so
            # erring toward recall here mainly risks letting a same-colour wrong garment through.
            boundary = float(np.clip(mw + 0.22 * (mc - mw), mw + 0.01, mc - 0.03))
            scale = float(max(0.05, (mc - mw) / 3.5))
        else:
            mw = 0.0
            boundary = float(max(0.30, np.percentile(correct_sims, 10)))
            scale = 0.12

        self._prototypes = protos
        self._boundary = boundary
        self._scale = scale
        self._n_samples = len(sorted(glob.glob(os.path.join(str(correct_dir), "*.jpg"))))
        try:
            _REF_PATH.parent.mkdir(parents=True, exist_ok=True)
            np.savez(_REF_PATH, prototypes=protos, boundary=np.float32(boundary),
                     scale=np.float32(scale), n_samples=np.int64(self._n_samples),
                     correct_med=np.float32(mc), wrong_med=np.float32(mw),
                     build_version=np.int64(_BUILD_VERSION))
        except Exception as exc:
            print(f"[UniformEmbed] save failed: {exc}")
        print(f"[UniformEmbed] built {k} prototypes from {self._n_samples} photos "
              f"(correct~{mc:.2f}, wrong~{mw:.2f}, boundary={boundary:.2f})")
        return True

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def prob_correct(self, crop_bgr: np.ndarray) -> tuple[bool | None, float]:
        """Classify a torso crop. Returns (verdict, p_correct): True=correct uniform, False=wrong,
        None=can't judge (too small / model unavailable) → caller stays neutral."""
        if not self.is_loaded() or crop_bgr is None or crop_bgr.size == 0:
            return None, 0.0
        h, w = crop_bgr.shape[:2]
        if h < _MIN_CROP_PX or w < _MIN_CROP_PX:
            return None, 0.0
        embs = self._encode([crop_bgr])
        if embs is None:
            return None, 0.0   # backbone unavailable → neutral (colour matcher still applies)
        sim = float((embs[0:1] @ self._prototypes.T).max())
        p = 1.0 / (1.0 + np.exp(-(sim - self._boundary) / max(1e-3, self._scale)))
        p = float(min(1.0, max(0.0, p)))
        return (p >= 0.5), p
