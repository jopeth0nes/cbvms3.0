"""In-app YOLOv8 image-classification trainer for CBVMS violation modules.

Uses the ultralytics library already present in requirements.txt — no new
dependencies. Photos are stored as plain image files; at train time a proper
YOLOv8-cls train/val split is built automatically.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TRAIN_DIR = ROOT / "data" / "training"
PREPARED_DIR = ROOT / "data" / "training_prepared"
RUNS_DIR = ROOT / "data" / "runs"

MODULES: dict[str, dict] = {
    "uniform": {
        "labels": ["correct_uniform", "wrong_uniform"],
        "model_out": "models/uniform_cls.pt",
    },
    "earring": {
        "labels": ["no_earring", "with_earring"],
        "model_out": "models/earring_cls.pt",
    },
}

MIN_SAMPLES_PER_CLASS = 10
IMG_SIZE = 224
EPOCHS = 20


def _letterbox(image_bgr: np.ndarray, size: int = IMG_SIZE) -> np.ndarray:
    """Resize to size x size keeping aspect ratio, padding with black."""
    h, w = image_bgr.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((size, size, 3), dtype=np.uint8)
    scale = size / float(max(h, w))
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    resized = cv2.resize(image_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    top = (size - nh) // 2
    left = (size - nw) // 2
    canvas[top:top + nh, left:left + nw] = resized
    return canvas


class ViolationTrainer:
    """Builds datasets and trains/predicts YOLOv8 classification models."""

    def __init__(self) -> None:
        self._models: dict[str, object] = {}  # module -> loaded YOLO model

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(module: str, label: str | None = None) -> None:
        if module not in MODULES:
            raise ValueError(f"Unknown module: {module}")
        if label is not None and label not in MODULES[module]["labels"]:
            raise ValueError(f"Unknown label '{label}' for module '{module}'")

    @staticmethod
    def label_dir(module: str, label: str) -> Path:
        return TRAIN_DIR / module / label

    # ------------------------------------------------------------------
    # Dataset building
    # ------------------------------------------------------------------

    def add_sample(self, module: str, label: str, image_bgr: np.ndarray) -> int:
        """Save one letterboxed sample; return the new file count for that label."""
        self._validate(module, label)
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError("Empty image")

        processed = _letterbox(image_bgr, IMG_SIZE)
        folder = self.label_dir(module, label)
        folder.mkdir(parents=True, exist_ok=True)
        out_path = folder / f"{uuid.uuid4().hex}.jpg"
        cv2.imwrite(str(out_path), processed, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

        return len(list(folder.glob("*.jpg")))

    def get_sample_counts(self, module: str) -> dict[str, int]:
        self._validate(module)
        counts: dict[str, int] = {}
        for label in MODULES[module]["labels"]:
            folder = self.label_dir(module, label)
            counts[label] = len(list(folder.glob("*.jpg"))) if folder.exists() else 0
        return counts

    def list_samples(self, module: str, label: str, limit: int = 12) -> list[Path]:
        """Return the most recent sample image paths (newest first)."""
        self._validate(module, label)
        folder = self.label_dir(module, label)
        if not folder.exists():
            return []
        files = sorted(folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:limit]

    def clear_samples(self, module: str, label: str | None = None) -> None:
        self._validate(module, label)
        labels = [label] if label else MODULES[module]["labels"]
        for lbl in labels:
            folder = self.label_dir(module, lbl)
            if folder.exists():
                for f in folder.glob("*.jpg"):
                    try:
                        f.unlink()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def _prepare_split(self, module: str) -> Path:
        """Build a YOLOv8-cls train/val directory from the flat sample folders."""
        prepared = PREPARED_DIR / module
        if prepared.exists():
            shutil.rmtree(prepared, ignore_errors=True)

        for label in MODULES[module]["labels"]:
            files = sorted(self.label_dir(module, label).glob("*.jpg"))
            if not files:
                continue
            n_val = max(1, int(len(files) * 0.2))
            val_files = files[:n_val]
            train_files = files[n_val:] or files  # never leave train empty

            for split, group in (("train", train_files), ("val", val_files)):
                dest = prepared / split / label
                dest.mkdir(parents=True, exist_ok=True)
                for f in group:
                    shutil.copy(f, dest / f.name)
        return prepared

    def train(self, module: str, on_progress: Callable[[str], None]) -> tuple[bool, str]:
        self._validate(module)

        on_progress("Validating dataset...")
        counts = self.get_sample_counts(module)
        for label, count in counts.items():
            if count < MIN_SAMPLES_PER_CLASS:
                pretty = label.replace("_", " ")
                return False, (
                    f"Need at least {MIN_SAMPLES_PER_CLASS} photos for '{pretty}' "
                    f"(have {count})."
                )

        on_progress("Preparing dataset...")
        dataset_dir = self._prepare_split(module)

        on_progress("Starting YOLOv8 training...")
        try:
            from ultralytics import YOLO
        except Exception as exc:
            return False, f"ultralytics not available: {exc}"

        try:
            model = YOLO("yolov8n-cls.pt")  # auto-downloads ~6MB on first use
        except Exception as exc:
            return False, f"Could not load base model: {exc}"

        def _on_epoch_end(trainer) -> None:
            try:
                epoch = int(getattr(trainer, "epoch", 0)) + 1
                on_progress(f"Training epoch {epoch}/{EPOCHS}...")
            except Exception:
                pass

        try:
            model.add_callback("on_train_epoch_end", _on_epoch_end)
        except Exception:
            pass

        try:
            model.train(
                data=str(dataset_dir),
                epochs=EPOCHS,
                imgsz=IMG_SIZE,
                batch=8,
                patience=5,
                project=str(RUNS_DIR),
                name=module,
                exist_ok=True,
                verbose=False,
                workers=0,     # avoid multiprocessing inside a daemon thread
                device="cpu",
            )
        except Exception as exc:
            return False, f"Training failed: {exc}"

        on_progress("Saving model...")
        best = RUNS_DIR / module / "weights" / "best.pt"
        if not best.exists():
            return False, "Training finished but no model file was produced."

        out_path = ROOT / MODULES[module]["model_out"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy(best, out_path)
        except Exception as exc:
            return False, f"Could not save model: {exc}"

        self._models.pop(module, None)  # invalidate cache so predict reloads
        on_progress("Done.")
        total = sum(counts.values())
        return True, f"Model trained successfully. {total} total samples."

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _get_model(self, module: str):
        if module in self._models:
            return self._models[module]
        if not self.is_trained(module):
            return None
        try:
            from ultralytics import YOLO
            model = YOLO(str(ROOT / MODULES[module]["model_out"]))
            self._models[module] = model
            return model
        except Exception as exc:
            print(f"[Trainer] could not load {module} model: {exc}")
            return None

    def predict(self, module: str, image_bgr: np.ndarray) -> tuple[str | None, float]:
        self._validate(module)
        model = self._get_model(module)
        if model is None or image_bgr is None or image_bgr.size == 0:
            return None, 0.0
        try:
            # Letterbox to match how training samples were stored (preserves the full
            # frame instead of ultralytics' default center-crop). Pass BGR as-is:
            # ultralytics' classification preprocess does cv2.cvtColor(im, BGR2RGB)
            # internally — converting here would double-swap R/B channels.
            proc = _letterbox(image_bgr, IMG_SIZE)
            results = model.predict(proc, verbose=False)
            if not results:
                return None, 0.0
            res = results[0]
            probs = getattr(res, "probs", None)
            if probs is None:
                return None, 0.0
            top1 = int(probs.top1)
            conf = float(probs.top1conf)
            name = res.names[top1]
            return name, conf
        except Exception as exc:
            print(f"[Trainer] predict error ({module}): {exc}")
            return None, 0.0

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_trained(self, module: str) -> bool:
        self._validate(module)
        return (ROOT / MODULES[module]["model_out"]).exists()

    def model_mtime(self, module: str) -> float | None:
        path = ROOT / MODULES[module]["model_out"]
        return path.stat().st_mtime if path.exists() else None

    def get_model_info(self, module: str) -> dict:
        self._validate(module)
        return {
            "trained": self.is_trained(module),
            "sample_counts": self.get_sample_counts(module),
            "model_path": str(ROOT / MODULES[module]["model_out"]),
        }

    def evaluate(
        self,
        module: str,
        on_progress: Callable[[str], None],
    ) -> dict | None:
        """
        Run inference on all samples in the training folder and compute metrics.
        Returns a dict with keys: accuracy, per_class (dict label->precision/recall/f1/support),
        confusion (2x2 list), total, correct.
        Returns None if model not trained or no samples exist.
        """
        self._validate(module)
        if not self.is_trained(module):
            return None

        labels = MODULES[module]["labels"]
        y_true, y_pred = [], []

        on_progress("Loading test samples...")
        for label in labels:
            files = list(self.label_dir(module, label).glob("*.jpg"))
            for i, f in enumerate(files):
                if i % 5 == 0:
                    on_progress(f"Testing {label}: {i}/{len(files)}...")
                img = cv2.imread(str(f))
                if img is None:
                    continue
                pred_label, _ = self.predict(module, img)
                y_true.append(label)
                y_pred.append(pred_label or "unknown")

        if not y_true:
            return None

        correct = sum(t == p for t, p in zip(y_true, y_pred))
        total = len(y_true)
        accuracy = correct / total if total else 0.0

        per_class = {}
        for lbl in labels:
            tp = sum(t == lbl and p == lbl for t, p in zip(y_true, y_pred))
            fp = sum(t != lbl and p == lbl for t, p in zip(y_true, y_pred))
            fn = sum(t == lbl and p != lbl for t, p in zip(y_true, y_pred))
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            support = sum(t == lbl for t in y_true)
            per_class[lbl] = {"precision": precision, "recall": recall, "f1": f1, "support": support}

        # 2x2 confusion matrix
        confusion = [[0, 0], [0, 0]]
        for t, p in zip(y_true, y_pred):
            i = labels.index(t) if t in labels else 0
            j = labels.index(p) if p in labels else 1
            confusion[i][j] += 1

        on_progress("Done.")
        return {
            "accuracy": accuracy,
            "per_class": per_class,
            "confusion": confusion,
            "labels": labels,
            "total": total,
            "correct": correct,
        }
