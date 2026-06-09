"""In-app YOLOv8 image-classification trainer for CBVMS violation modules.

Uses the ultralytics library already present in requirements.txt — no new
dependencies. Photos are stored as plain image files; at train time a proper
YOLOv8-cls train/val split is built automatically.
"""

from __future__ import annotations

import json
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
EPOCHS = 40
PATIENCE = 12
# Fraction of each class held out for validation/evaluation (never trained on).
VAL_FRAC = 0.2
# Filename of the per-module held-out manifest written at train time.
VAL_MANIFEST = "val_manifest.json"


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

    def add_sample(self, module: str, label: str, image_bgr: np.ndarray) -> Path:
        """Save one letterboxed sample; return the saved file's Path."""
        self._validate(module, label)
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError("Empty image")

        processed = _letterbox(image_bgr, IMG_SIZE)
        folder = self.label_dir(module, label)
        folder.mkdir(parents=True, exist_ok=True)
        out_path = folder / f"{uuid.uuid4().hex}.jpg"
        cv2.imwrite(str(out_path), processed, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

        return out_path

    def delete_sample(self, module: str, label: str, path) -> bool:
        """Delete a single sample file. Only removes files inside this label's folder
        (guards against deleting anything outside the dataset). Returns True on success."""
        self._validate(module, label)
        try:
            p = Path(path).resolve()
            folder = self.label_dir(module, label).resolve()
            if folder not in p.parents or p.suffix.lower() != ".jpg":
                return False
            if p.exists():
                p.unlink()
            return True
        except Exception as exc:
            print(f"[Trainer] delete_sample error: {exc}")
            return False

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

    @staticmethod
    def _split_label_files(files: list[Path]) -> tuple[list[Path], list[Path]]:
        """Deterministic per-class train/val split (val = first VAL_FRAC of sorted files).

        Sorting makes the split reproducible, so evaluate() can reconstruct the exact
        held-out set the model never trained on even if the manifest is missing.
        """
        files = sorted(files)
        if not files:
            return [], []
        n_val = max(1, int(len(files) * VAL_FRAC))
        val = files[:n_val]
        train = files[n_val:] or files  # never leave train empty (tiny datasets)
        return train, val

    def _prepare_split(self, module: str) -> Path:
        """Build a YOLOv8-cls train/val directory from the flat sample folders.

        The TRAIN split is class-balanced by oversampling the minority class(es) up to
        the largest train-class size — otherwise a lopsided dataset (e.g. 563 vs 11)
        trains a degenerate model that always predicts the majority class. YOLOv8's
        built-in augmentation varies the duplicated images during training.

        The VAL file list is persisted to a manifest so evaluate() reports honest,
        held-out accuracy on photos the model never saw during training.
        """
        prepared = PREPARED_DIR / module
        if prepared.exists():
            shutil.rmtree(prepared, ignore_errors=True)

        # First pass: split each class into train/val file lists.
        train_by_label: dict[str, list[Path]] = {}
        val_by_label: dict[str, list[Path]] = {}
        for label in MODULES[module]["labels"]:
            files = list(self.label_dir(module, label).glob("*.jpg"))
            if not files:
                continue
            train, val = self._split_label_files(files)
            val_by_label[label] = val
            train_by_label[label] = train

        # Balance the train split by oversampling the smaller class(es) to the max size.
        target = max((len(f) for f in train_by_label.values()), default=0)

        for label, files in train_by_label.items():
            dest = prepared / "train" / label
            dest.mkdir(parents=True, exist_ok=True)
            for i in range(target):
                src = files[i % len(files)]
                # Duplicate copies get a unique name so none are overwritten.
                name = src.name if i < len(files) else f"{src.stem}_dup{i}{src.suffix}"
                shutil.copy(src, dest / name)

        for label, files in val_by_label.items():
            dest = prepared / "val" / label
            dest.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy(f, dest / f.name)

        # Persist the held-out val set (absolute source paths) for honest evaluation.
        try:
            prepared.mkdir(parents=True, exist_ok=True)
            manifest = {lbl: [str(f) for f in files] for lbl, files in val_by_label.items()}
            (prepared / VAL_MANIFEST).write_text(json.dumps(manifest, indent=2))
        except Exception as exc:
            print(f"[Trainer] could not write val manifest: {exc}")

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
                patience=PATIENCE,
                project=str(RUNS_DIR),
                name=module,
                exist_ok=True,
                verbose=False,
                workers=0,     # avoid multiprocessing inside a daemon thread
                device="cpu",
                cos_lr=True,           # smoother LR decay → better convergence
                dropout=0.1,           # light regularisation against overfitting
                # Clothing-appropriate augmentation: colour/brightness jitter, flips,
                # small rotation/translation/scale, and random erasing for occlusion
                # robustness. Makes the duplicated (oversampled) minority images vary
                # every epoch and helps the model generalise to live framing/lighting.
                hsv_h=0.015, hsv_s=0.5, hsv_v=0.4,
                fliplr=0.5,
                degrees=10.0, translate=0.1, scale=0.4,
                erasing=0.4,
                auto_augment="randaugment",
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

    def predict_proba(self, module: str, image_bgr: np.ndarray) -> dict[str, float] | None:
        """Return the full {label: probability} distribution, or None if unavailable.

        Used by the live fusion path to get a real P(correct_uniform) rather than just the
        top-1 class. Labels follow MODULES[module]["labels"].
        """
        self._validate(module)
        model = self._get_model(module)
        if model is None or image_bgr is None or image_bgr.size == 0:
            return None
        try:
            proc = _letterbox(image_bgr, IMG_SIZE)
            results = model.predict(proc, verbose=False)
            if not results:
                return None
            res = results[0]
            probs = getattr(res, "probs", None)
            if probs is None:
                return None
            data = probs.data.tolist() if hasattr(probs.data, "tolist") else list(probs.data)
            return {res.names[i]: float(p) for i, p in enumerate(data)}
        except Exception as exc:
            print(f"[Trainer] predict_proba error ({module}): {exc}")
            return None

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

    def _held_out_files(self, module: str) -> dict[str, list[Path]]:
        """Resolve the held-out val files per label for honest evaluation.

        Prefers the manifest written at train time (the exact set the model never saw);
        falls back to recomputing the deterministic split from current samples.
        """
        labels = MODULES[module]["labels"]
        manifest_path = PREPARED_DIR / module / VAL_MANIFEST
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                out: dict[str, list[Path]] = {}
                for lbl in labels:
                    out[lbl] = [Path(p) for p in data.get(lbl, []) if Path(p).exists()]
                if any(out.values()):
                    return out
            except Exception as exc:
                print(f"[Trainer] val manifest unreadable, recomputing split: {exc}")
        # Fallback: reconstruct the same deterministic split _prepare_split uses.
        out = {}
        for lbl in labels:
            _, val = self._split_label_files(list(self.label_dir(module, lbl).glob("*.jpg")))
            out[lbl] = val
        return out

    def evaluate(
        self,
        module: str,
        on_progress: Callable[[str], None],
    ) -> dict | None:
        """
        Run inference on the HELD-OUT validation photos (the set the model never trained
        on) and compute metrics. Returns a dict with keys: accuracy, per_class (dict
        label->precision/recall/f1/support), confusion (2x2 list), total, correct, held_out.
        Returns None if model not trained or no held-out samples exist.
        """
        self._validate(module)
        if not self.is_trained(module):
            return None

        labels = MODULES[module]["labels"]
        eval_files = self._held_out_files(module)
        y_true, y_pred = [], []

        on_progress("Loading held-out samples...")
        for label in labels:
            files = eval_files.get(label, [])
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
            "held_out": True,
        }
