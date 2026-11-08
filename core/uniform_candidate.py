"""Isolated, resumable experimental uniform candidate training.

This module writes only beneath ``data/uniform_candidates`` (or an explicitly
provided disposable candidate root). It never calls the active trainer or
publishes weights into ``models/``.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.uniform_dataset import CATEGORIES, PREPROCESS_VERSION as DATASET_PREPROCESS_VERSION

MAPPING_VERSION = "uniform-garments-v1"
CLASS_MAP = {0: "campus_uniform", 1: "organizational_shirt", 2: "other_clothing"}
CATEGORY_TO_INDEX = {value: key for key, value in CLASS_MAP.items()}
DEFAULT_CONFIG = {"epochs": 5, "batch": 4, "threads": 2, "min_per_class": 10, "synthetic_smoke": False}
BLOCKING_FLAGS = {
    "obstruction", "obstructed", "low_resolution", "possible_blur", "image_quality_check_failed",
    "incorrect_association", "unable_to_assess", "multiple_people", "person_association_uncertain",
    "provenance_association_uncertain", "label_conflict", "near_duplicate_review",
}
RUN_STATES = {"preparing", "running", "cancel_requested", "completed", "cancelled", "failed", "interrupted"}
_ROOT_LOCKS: dict[str, threading.Lock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, sort_keys=True, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict:
    if path.is_symlink():
        raise RuntimeError(f"Candidate metadata cannot be a symlink: {path.name}")
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid candidate metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid candidate metadata: {path.name}")
    return value


def _snapshot(dataset: Any) -> dict:
    value = dataset.snapshot() if callable(getattr(dataset, "snapshot", None)) else dataset
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("Dataset must provide a Stage 1 snapshot with records")
    return value


def _flags(record: dict) -> set[str]:
    value = record.get("flags", [])
    if isinstance(value, str):
        return {value}
    return {str(flag) for flag in value or []}


def _record_groups(record: dict) -> tuple[set[str], bool]:
    groups = {str(value) for value in record.get("capture_groups", []) if value}
    if record.get("capture_group"):
        groups.add(str(record["capture_group"]))
    unknown = bool(record.get("has_unknown_provenance", not groups))
    return groups, unknown


def _valid_crop(record: dict) -> bool:
    crop = record.get("crop")
    if not isinstance(crop, (list, tuple)) or len(crop) != 4 or any(type(v) is not int for v in crop):
        return False
    x1, y1, x2, y2 = crop
    width, height = record.get("width"), record.get("height")
    return (type(width) is int and type(height) is int and x1 >= 0 and y1 >= 0
            and x2 <= width and y2 <= height and x2 - x1 >= 16 and y2 - y1 >= 16)


def training_eligibility_reason(record: dict) -> str | None:
    """Metadata eligibility shared by readiness counts and candidate freezing.

    Candidate freezing still verifies source paths, hashes and decoded bytes.
    """
    category = record.get("category")
    if record.get("status") != "approved": return "not approved"
    if category not in CATEGORY_TO_INDEX: return "invalid training category"
    if _flags(record) & BLOCKING_FLAGS: return "unresolved training-blocking flags"
    if not _valid_crop(record): return "missing or invalid torso crop"
    if record.get("duplicate_of"): return "near duplicate requires review"
    if (record.get("logo_annotation") or {}).get("source_kind") == "reference_artwork":
        return "reference artwork is not a photographed garment"
    return None


def is_training_eligible(record: dict) -> bool:
    return training_eligibility_reason(record) is None


def candidate_summary(dataset: Any, *, min_per_class: int = 10) -> dict:
    """Summarize records usable for experiments, without changing the catalog."""
    snap = _snapshot(dataset)
    included = {category: 0 for category in CATEGORY_TO_INDEX}
    approved = {category: 0 for category in CATEGORY_TO_INDEX}
    groups = {category: {"known": 0, "unknown": 0} for category in CATEGORY_TO_INDEX}
    known_sets = {category: set() for category in CATEGORY_TO_INDEX}
    unknown_seen = {category: False for category in CATEGORY_TO_INDEX}
    pending = sum(r.get("status") == "pending_review" for r in snap["records"])
    excluded = sum(r.get("status") == "excluded" for r in snap["records"])
    quality_blocked = 0
    reasons = []
    for record in snap["records"]:
        category = record.get("category")
        if record.get("status") == "approved" and record.get("category") in CATEGORY_TO_INDEX and not is_training_eligible(record):
            quality_blocked += 1
        if record.get("status") != "approved" or category not in CATEGORY_TO_INDEX:
            continue
        approved[category] += 1
        if not is_training_eligible(record):
            continue
        included[category] += 1
        known, unknown = _record_groups(record)
        known_sets[category].update(known)
        unknown_seen[category] |= unknown
    for category in included:
        groups[category] = {"known": len(known_sets[category]), "unknown": int(unknown_seen[category])}
    missing = {category: count for category, count in included.items() if count < min_per_class}
    eligible = not missing
    if missing:
        reasons.extend(f"{CATEGORIES[category]} has {count} eligible approved examples; requires {min_per_class}."
                       for category, count in missing.items())
    if quality_blocked:
        reasons.append(f"{quality_blocked} approved record(s) have unresolved flags, invalid crops, duplicate links, or reference-artwork provenance and are excluded.")
    if pending:
        reasons.append(f"{pending} pending-review record(s) are excluded.")
    if excluded:
        reasons.append(f"{excluded} excluded record(s) are not used.")
    return {"mapping_version": MAPPING_VERSION, "approved_by_category": approved,
            "included_by_category": included,
            "groups_by_category": groups, "pending": pending, "excluded": excluded,
            "quality_blocked": quality_blocked, "eligible": eligible,
            "experimental_training_eligible": eligible, "reasons": reasons,
            "readiness_reasons": list(reasons), "evaluation_ready": False,
            "activation_allowed": False}


class _TrainingLock:
    def __init__(self, root: Path, *, blocking: bool = False):
        self.root = root
        self.path = root / ".candidate-training.lock"
        self.file = None
        with _ROOT_LOCKS_GUARD:
            self.thread_lock = _ROOT_LOCKS.setdefault(str(root.resolve()), threading.Lock())
        self.blocking = blocking

    def acquire(self) -> bool:
        if not self.thread_lock.acquire(blocking=False):
            return False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.file = self.path.open("a+")
            flags = fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(self.file.fileno(), flags)
            except BlockingIOError:
                self.file.close()
                self.file = None
                self.thread_lock.release()
                return False
            return True
        except Exception:
            if self.file:
                self.file.close()
                self.file = None
            self.thread_lock.release()
            raise

    def release(self) -> None:
        if self.file:
            # Closing relinquishes this descriptor but does not explicitly
            # unlock the inherited flock; a launched worker may still own it.
            self.file.close()
            self.file = None
            self.thread_lock.release()

    def handoff(self) -> None:
        """Close the parent handle after Popen inherited it; child keeps flock."""
        self.release()


class CandidateService:
    """Prepare immutable candidate bundles and supervise isolated CPU workers."""

    def __init__(self, dataset_root: Any | str | Path | None = None, candidates_root: str | Path | None = None,
                 base_weights: str | Path | None = None):
        repo = Path(__file__).resolve().parents[1]
        self.dataset = dataset_root if callable(getattr(dataset_root, "snapshot", None)) else None
        if self.dataset is not None:
            dataset_root = self.dataset.root
        elif dataset_root is None:
            from core.uniform_dataset import UniformDataset
            dataset_root = UniformDataset().root
        self.dataset_root = Path(dataset_root).resolve()
        if candidates_root is None:
            candidates_root = repo / "data" / "uniform_candidates"
        self.candidates_root = Path(candidates_root).resolve()
        self.candidates_root.mkdir(parents=True, exist_ok=True)
        self.base_weights = Path(base_weights).absolute() if base_weights else repo / "yolov8n-cls.pt"
        self._handles: dict[str, subprocess.Popen] = {}
        self._preparing: set[str] = set()
        self._handles_guard = threading.Lock()

    @staticmethod
    def validate_config(config: dict | None = None) -> dict:
        value = dict(DEFAULT_CONFIG)
        if config:
            unknown = set(config) - set(value)
            if unknown:
                raise ValueError(f"Unknown candidate config keys: {', '.join(sorted(unknown))}")
            value.update(config)
        for key, low, high in (("epochs", 1, 40), ("batch", 2, 16), ("threads", 1, 4)):
            if type(value[key]) is not int or not low <= value[key] <= high:
                raise ValueError(f"{key} must be an integer from {low} to {high}")
        if type(value["synthetic_smoke"]) is not bool:
            raise ValueError("synthetic_smoke must be boolean")
        min_value = 2 if value["synthetic_smoke"] else 10
        if type(value["min_per_class"]) is not int or value["min_per_class"] < min_value or value["min_per_class"] > 5000:
            raise ValueError(f"min_per_class must be from {min_value} to 5000")
        return value

    def _dataset_obj(self):
        if self.dataset is None:
            from core.uniform_dataset import UniformDataset
            self.dataset = UniformDataset(self.dataset_root)
        return self.dataset

    def summary(self, min_per_class: int = 10) -> dict:
        summary = candidate_summary(self._dataset_obj(), min_per_class=min_per_class)
        reason = None
        if not self.base_weights.is_file() or self.base_weights.is_symlink():
            reason = f"Trusted local classifier weights are unavailable: {self.base_weights}"
        elif not 0 < self.base_weights.stat().st_size <= 256 * 1024 * 1024:
            reason = "Trusted local classifier weights must be nonempty and at most 256 MiB."
        if reason:
            summary['eligible'] = summary['experimental_training_eligible'] = False
            summary['reasons'].append(reason)
            summary['readiness_reasons'].append(reason)
        return summary

    def is_busy(self) -> bool:
        """Nonblocking candidate lock probe; does not read the catalog or weights."""
        probe = _TrainingLock(self.candidates_root)
        if not probe.acquire():
            return True
        probe.release()
        return False

    def _run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("Invalid candidate run id")
        raw_path = self.candidates_root / run_id
        if raw_path.is_symlink():
            raise ValueError("Candidate run directory cannot be a symlink")
        path = raw_path.resolve()
        if self.candidates_root not in path.parents:
            raise ValueError("Candidate path escapes the candidates root")
        return path

    def _status_write(self, run_dir: Path, status: dict) -> None:
        path = run_dir / "status.json"
        if path.is_file():
            try:
                merged = _read_json(path)
            except RuntimeError:
                merged = {}
            merged.update(status)
            status = merged
        status.setdefault("run_id", run_dir.name)
        status["updated_at"] = _utc()
        _atomic_json(path, status)

    def _interrupt_stale_runs(self) -> None:
        for path in self.candidates_root.iterdir():
            status_path = path / "status.json"
            if path.is_symlink() or not path.is_dir() or len(path.name) != 32 or not status_path.is_file():
                continue
            try:
                value = _read_json(status_path)
            except RuntimeError:
                continue
            if value.get("status") in {"preparing", "running", "cancel_requested"}:
                value.update(status="interrupted", epoch=int(value.get("epoch", 0)),
                             message="No worker owns the candidate lock; last complete epoch checkpoint is preserved.",
                             resumable=self._has_checkpoint(path))
                self._status_write(path, value)

    def _snapshot_digest(self, snap: dict) -> str:
        payload = {"preprocess_version": snap.get("preprocess_version"),
                   "records": snap.get("records", [])}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False).encode("utf-8")).hexdigest()

    def _freeze(self, run_dir: Path, config: dict) -> dict:
        dataset = self._dataset_obj()
        snap = _snapshot(dataset)
        summary = candidate_summary(snap, min_per_class=config["min_per_class"])
        if not summary["eligible"]:
            raise ValueError("Candidate dataset is not eligible: " + " ".join(summary["reasons"]))
        if config["synthetic_smoke"]:
            included = [r for r in snap["records"] if is_training_eligible(r)]
            if not included or any(not any(s.get("kind") == "synthetic_smoke" for s in r.get("sources", []))
                                   for r in included):
                raise ValueError("synthetic_smoke is allowed only for records marked synthetic_smoke")
        if (run_dir / "cancel.flag").exists():
            return {"cancelled_during_freeze": True}
        if not self.base_weights.is_file() or self.base_weights.is_symlink():
            raise FileNotFoundError(f"Trusted local classifier weights are unavailable: {self.base_weights}")
        if self.base_weights.stat().st_size <= 0 or self.base_weights.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("Trusted local classifier weights are outside the supported file-size bound")
        base_hash = _sha256(self.base_weights)
        base_copy = run_dir / "base_weights.pt"
        with self.base_weights.open("rb") as source, base_copy.open("wb") as target:
            copied = 0
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > 256 * 1024 * 1024:
                    raise ValueError("Trusted local classifier weights exceed the copy bound")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if _sha256(base_copy) != base_hash:
            raise RuntimeError("Base weights changed while the candidate bundle was frozen")
        if (run_dir / "cancel.flag").exists():
            return {"cancelled_during_freeze": True}
        (run_dir / "prepared").mkdir(parents=True, exist_ok=True)
        root = dataset.root.resolve()
        eligible = [r for r in snap["records"] if is_training_eligible(r)]
        entries = []
        from PIL import Image
        import cv2
        import numpy as np
        from core.trainer import _letterbox
        for record in sorted(eligible, key=lambda row: row["image_id"]):
            if (run_dir / "cancel.flag").exists():
                return {"cancelled_during_freeze": True}
            relative = Path(record.get("original_path", ""))
            if relative.is_absolute() or not relative.parts:
                raise ValueError("Original image path is not a safe dataset-relative path")
            untrusted_path = root / relative
            cursor = root
            for component in relative.parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    raise ValueError("Candidate original path contains a symlink")
            original_path = untrusted_path.resolve()
            if root not in original_path.parents:
                raise ValueError("Original image path escapes the dataset root")
            if not original_path.is_file():
                raise ValueError("Candidate original is missing or is not a regular file")
            original_bytes = original_path.read_bytes()
            original_hash = hashlib.sha256(original_bytes).hexdigest()
            if original_hash != record.get("sha256") or original_hash != record.get("image_id"):
                raise ValueError(f"Original SHA256 mismatch for image {record.get('image_id')}")
            crop = record.get("crop")
            if not _valid_crop(record):
                raise ValueError(f"Approved image {record.get('image_id')} has no valid crop")
            with Image.open(io.BytesIO(original_bytes)) as im:
                im.load()
                raster = im.convert("RGB")
            if raster.size != (record.get("width"), record.get("height")):
                raise ValueError(f"Original dimensions changed for image {record.get('image_id')}")
            crop_rgb = np.asarray(raster.crop(tuple(crop)), dtype=np.uint8)
            if crop_rgb.size == 0:
                raise ValueError(f"Approved image {record.get('image_id')} has an empty crop")
            crop_bgr = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR)
            boxed_bgr = _letterbox(crop_bgr, 224)
            tensor = (cv2.cvtColor(boxed_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0)
            tensor_path = run_dir / "prepared" / f"{record['image_id']}.npy"
            with tensor_path.open("wb") as out:
                np.save(out, tensor, allow_pickle=False)
                out.flush()
                os.fsync(out.fileno())
            known, unknown = _record_groups(record)
            entries.append({"image_id": record["image_id"], "original_sha256": original_hash,
                            "prepared_path": str(tensor_path.relative_to(run_dir)),
                            "prepared_sha256": _sha256(tensor_path), "category": record["category"],
                            "class_index": CATEGORY_TO_INDEX[record["category"]], "crop": crop,
                            "crop_source": record.get("crop_source"),
                            "preprocess_version": record.get("preprocess_version", snap.get("preprocess_version")),
                            "group_ids": sorted(known), "unknown_provenance": unknown,
                            "near_duplicate_ids": sorted(record.get("duplicate_of", [])), "split": "train"})
        after = _snapshot(dataset)
        if (run_dir / "cancel.flag").exists():
            return {"cancelled_during_freeze": True}
        dataset_hash = self._snapshot_digest(snap)
        if self._snapshot_digest(after) != dataset_hash:
            raise RuntimeError("Stage 1 catalog changed while freezing candidate inputs; retry from a fresh review")
        class_map = {"mapping_version": MAPPING_VERSION,
                     "classes": [{"index": index, "category": category} for index, category in CLASS_MAP.items()]}
        _atomic_json(run_dir / "class_map.json", class_map)
        manifest = {"schema_version": 1, "mapping_version": MAPPING_VERSION,
                    "dataset_preprocess_version": snap.get("preprocess_version"),
                    "model_preprocess": {"version": "trainer-letterbox-rgb-0-1-v1", "size": 224,
                                         "color": "RGB", "range": [0.0, 1.0], "augmentation": "none"},
                    "dataset_snapshot_sha256": dataset_hash,
                    "eligibility_snapshot": {"approved_by_category": summary["approved_by_category"],
                                             "included_by_category": summary["included_by_category"],
                                             "groups_by_category": summary["groups_by_category"],
                                             "pending": summary["pending"], "excluded": summary["excluded"],
                                             "quality_blocked": summary["quality_blocked"],
                                             "readiness_reasons": summary["reasons"]},
                    "split_policy": "experimental_train_only_no_validation_or_evaluation",
                    "limitations": ["Experimental train-only bundle; no validation or evaluation metrics.",
                                    "Train loss does not establish accuracy or deployment readiness."],
                    "entries": entries}
        _atomic_json(run_dir / "dataset_manifest.json", manifest)
        manifest_hash = _sha256(run_dir / "dataset_manifest.json")
        import importlib.metadata
        config_data = {**config, "mapping_version": MAPPING_VERSION, "base_weights_sha256": base_hash,
                       "base_weights_file": "base_weights.pt", "dataset_manifest_sha256": manifest_hash,
                       "synthetic_smoke": config["synthetic_smoke"], "nondeployable": True,
                       "architecture": "yolov8n-cls", "optimizer": "AdamW", "learning_rate": 1e-3,
                       "weight_decay": 1e-2, "seed": int(run_dir.name[:8], 16),
                       "eligibility_snapshot": manifest["eligibility_snapshot"],
                       "model_preprocess": manifest["model_preprocess"],
                       "ultralytics_version": importlib.metadata.version("ultralytics"),
                       "torch_version": importlib.metadata.version("torch"),
                       "limitations": ["Experimental train-only; no independent validation/evaluation.",
                                       "Training loss is not an accuracy measure.",
                                       "Candidate is nondeployable and is never activated by this service."]}
        _atomic_json(run_dir / "config.json", config_data)
        immutable = {}
        for path in [run_dir / "base_weights.pt", run_dir / "class_map.json", run_dir / "dataset_manifest.json",
                     run_dir / "config.json", *(run_dir / e["prepared_path"] for e in entries)]:
            immutable[str(path.relative_to(run_dir))] = _sha256(path)
        _atomic_json(run_dir / "immutable_checksums.json", immutable)
        return {"manifest_sha256": manifest_hash, "base_weights_sha256": base_hash,
                "dataset_snapshot_sha256": dataset_hash, "included": len(entries), "config": config_data}

    def _spawn(self, run_id: str, lock: _TrainingLock, *, resume: bool) -> None:
        run_dir = self._run_dir(run_id)
        command = [sys.executable, "-m", "core.uniform_candidate_worker", str(run_dir),
                   str(lock.file.fileno()), "resume" if resume else "start"]
        log = (run_dir / "train.log").open("a", encoding="utf-8")
        log.write(f"[{_utc()}] command={command!r} pid_pending run_uuid={run_id}\n")
        log.flush()
        try:
            process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                                       pass_fds=(lock.file.fileno(),),
                                       env={**os.environ, "YOLO_CONFIG_DIR": str(self.candidates_root / ".ultralytics"),
                                            "MPLCONFIGDIR": str(self.candidates_root / ".mplconfig")})
        except Exception:
            log.close()
            raise
        log.write(f"[{_utc()}] pid={process.pid} started_at={_utc()} run_uuid={run_id}\n")
        log.flush()
        log.close()
        lock.handoff()
        with self._handles_guard:
            self._handles[run_id] = process
        waiter = threading.Thread(target=self._wait_worker, args=(run_id, process),
                                  name=f"uniform-candidate-{run_id[:8]}", daemon=True)
        waiter.start()

    def _wait_worker(self, run_id: str, process: subprocess.Popen) -> None:
        process.wait()
        with self._handles_guard:
            self._handles.pop(run_id, None)

    def _prepare_and_spawn(self, run_id: str, lock: _TrainingLock, config: dict) -> None:
        run_dir = self._run_dir(run_id)
        try:
            frozen = self._freeze(run_dir, config)
            if frozen.get("cancelled_during_freeze") or (run_dir / "cancel.flag").exists():
                self._status_write(run_dir, {"status": "cancelled", "epoch": 0, "epochs": config["epochs"],
                                             "message": "Cancelled while preparing immutable candidate inputs.",
                                             "resumable": False})
                return
            self._status_write(run_dir, {"status": "running", "epoch": 0, "epochs": config["epochs"],
                                         "message": "Candidate worker starting.", "resumable": True,
                                         "run_uuid": run_id, **frozen})
            self._spawn(run_id, lock, resume=False)
        except Exception as exc:
            try:
                self._status_write(run_dir, {"status": "failed", "message": str(exc),
                                             "resumable": self._has_checkpoint(run_dir)})
                limitations = run_dir / "limitations.txt"
                if not limitations.exists():
                    limitations.write_text("NONDEPLOYABLE candidate bundle. No evaluation or activation.\n",
                                           encoding="utf-8")
            except Exception:
                pass
        finally:
            with self._handles_guard:
                self._preparing.discard(run_id)
            if lock.file:
                lock.release()

    def start(self, config: dict | None = None) -> str:
        chosen = self.validate_config(config)
        lock = _TrainingLock(self.candidates_root)
        if not lock.acquire():
            raise RuntimeError("A uniform candidate job is already active")
        run_id = uuid.uuid4().hex
        try:
            self._interrupt_stale_runs()
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(mode=0o700, parents=True)
            os.chmod(run_dir, 0o700)
            self._status_write(run_dir, {"run_id": run_id, "status": "preparing", "epoch": 0,
                                         "epochs": chosen["epochs"], "message": "Freezing reviewed candidate inputs.",
                                         "resumable": False, "started_at": _utc(), "process_id": os.getpid(),
                                         "run_uuid": run_id})
            (run_dir / "limitations.txt").write_text(
                "NONDEPLOYABLE experimental train-only bundle. No validation/evaluation or activation.\n",
                encoding="utf-8")
            with self._handles_guard:
                self._preparing.add(run_id)
            thread = threading.Thread(target=self._prepare_and_spawn, args=(run_id, lock, chosen),
                                      name=f"uniform-candidate-prepare-{run_id[:8]}", daemon=True)
            thread.start()
        except Exception as exc:
            with self._handles_guard:
                self._preparing.discard(run_id)
            if "run_dir" in locals() and run_dir.exists():
                self._status_write(run_dir, {"run_id": run_id, "status": "failed", "epoch": 0,
                                             "epochs": chosen["epochs"], "message": str(exc),
                                             "resumable": False, "run_uuid": run_id})
            if lock.file:
                lock.release()
            raise
        return run_id

    def resume(self, run_id: str) -> str:
        lock = _TrainingLock(self.candidates_root)
        if not lock.acquire():
            raise RuntimeError("A uniform candidate job is already active")
        try:
            run_dir = self._run_dir(run_id)
            current = _read_json(run_dir / "status.json")
            if current.get("status") in {"running", "preparing", "cancel_requested"}:
                current.update(status="interrupted", message="Previous process ended; the last complete epoch checkpoint is preserved.")
                self._status_write(run_dir, current)
            if current.get("status") == "completed":
                raise ValueError("Completed candidate jobs cannot be resumed")
            self._verify_bundle(run_dir)
            config = _read_json(run_dir / "config.json")
            completed_epoch = self._checkpoint_epoch(run_dir)
            if completed_epoch > config["epochs"]:
                raise ValueError("Checkpoint epoch exceeds the configured epoch count")
            cancel = run_dir / "cancel.flag"
            try:
                cancel.unlink()
            except FileNotFoundError:
                pass
            self._status_write(run_dir, {**current, "status": "running", "epoch": completed_epoch,
                                         "epochs": config["epochs"], "message": "Resuming from last complete epoch.",
                                         "resumable": True, "run_uuid": run_id})
            self._spawn(run_id, lock, resume=True)
        except Exception:
            if lock.file:
                lock.release()
            raise
        return run_id

    def _checkpoint_epoch(self, run_dir: Path) -> int:
        pointer_path = run_dir / "last_checkpoint.json"
        if not pointer_path.is_file():
            raise ValueError("No recoverable completed-epoch checkpoint exists")
        config = _read_json(run_dir / "config.json")
        pointer = _read_json(pointer_path)
        relative = Path(pointer.get("file", ""))
        if (relative.is_absolute() or ".." in relative.parts
                or not re.fullmatch(r"checkpoint_epoch_[0-9]{6}\.pt", relative.name)):
            raise ValueError("Checkpoint path escapes candidate bundle")
        checkpoint = run_dir / relative
        sidecar_path = run_dir / f"{relative.name}.sha256.json"
        for candidate_path in (checkpoint, sidecar_path):
            cursor = run_dir
            for component in candidate_path.relative_to(run_dir).parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    raise ValueError("Checkpoint files cannot be symlinks")
        sidecar = _read_json(sidecar_path)
        expected = {"run_uuid": run_dir.name, "mapping_version": MAPPING_VERSION,
                    "dataset_manifest_sha256": config.get("dataset_manifest_sha256"),
                    "base_weights_sha256": config.get("base_weights_sha256"),
                    "config_sha256": _sha256(run_dir / "config.json")}
        if any(sidecar.get(key) != value for key, value in expected.items()):
            raise ValueError("Checkpoint sidecar does not match run, mapping, data, weights, or config")
        if not checkpoint.is_file() or _sha256(checkpoint) != pointer.get("sha256") or pointer.get("sha256") != sidecar.get("sha256"):
            raise ValueError("Checkpoint SHA256 verification failed")
        if int(pointer.get("epoch", -1)) != int(sidecar.get("epoch", -2)):
            raise ValueError("Checkpoint pointer and sidecar epoch differ")
        if relative.name != f"checkpoint_epoch_{int(sidecar['epoch']):06d}.pt":
            raise ValueError("Checkpoint filename and epoch metadata differ")
        return int(sidecar["epoch"])

    def _verify_bundle(self, run_dir: Path) -> None:
        config = _read_json(run_dir / "config.json")
        manifest_path = run_dir / "dataset_manifest.json"
        if _sha256(manifest_path) != config.get("dataset_manifest_sha256"):
            raise ValueError("Frozen dataset manifest hash mismatch")
        if config.get("mapping_version") != MAPPING_VERSION:
            raise ValueError("Class mapping version changed; this bundle cannot resume")
        base_relative = Path(config.get("base_weights_file", ""))
        if base_relative.is_absolute() or ".." in base_relative.parts:
            raise ValueError("Frozen base weights path escapes candidate bundle")
        base_path = run_dir / base_relative
        if base_path.is_symlink() or not base_path.is_file() or _sha256(base_path) != config.get("base_weights_sha256"):
            raise ValueError("Frozen base weights hash mismatch")
        class_map = _read_json(run_dir / "class_map.json")
        expected_class_map = {"mapping_version": MAPPING_VERSION,
                              "classes": [{"index": index, "category": category} for index, category in CLASS_MAP.items()]}
        if class_map != expected_class_map:
            raise ValueError("Class mapping or class index order changed")
        manifest = _read_json(manifest_path)
        immutable = _read_json(run_dir / "immutable_checksums.json")
        expected_paths = {"base_weights.pt", "class_map.json", "dataset_manifest.json", "config.json"}
        expected_paths.update(entry.get("prepared_path") for entry in manifest.get("entries", []))
        if not manifest.get("entries") or set(immutable) != expected_paths:
            raise ValueError("Frozen candidate checksum inventory is empty or incomplete")
        for relative, expected in immutable.items():
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError(f"Unsafe frozen candidate path: {relative}")
            path = run_dir / relative_path
            cursor = run_dir
            for component in relative_path.parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    raise ValueError(f"Frozen candidate path contains a symlink: {relative}")
            resolved = path.resolve()
            if path.is_symlink() or run_dir not in resolved.parents or not resolved.is_file() or _sha256(resolved) != expected:
                raise ValueError(f"Frozen candidate input hash mismatch: {relative}")

    def cancel(self, run_id: str) -> None:
        run_dir = self._run_dir(run_id)
        current = _read_json(run_dir / "status.json")
        if current.get("status") not in {"preparing", "running", "cancel_requested"}:
            raise ValueError(f"Cannot cancel a {current.get('status')} candidate")
        if not self.is_busy():
            raise ValueError("Candidate worker is no longer active")
        flag = run_dir / "cancel.flag"
        flag.write_text(_utc(), encoding="ascii")

    def status(self, run_id: str | None = None) -> dict:
        if run_id is None:
            runs = []
            for path in self.candidates_root.iterdir():
                if not path.is_symlink() and path.is_dir() and len(path.name) == 32 and (path / "status.json").is_file():
                    try:
                        runs.append(_read_json(path / "status.json"))
                    except RuntimeError:
                        continue
            if not runs:
                return {"run_id": None, "status": "idle", "epoch": 0, "epochs": 0,
                        "message": "No candidate job exists.", "resumable": False}
            current = max(runs, key=lambda row: (row.get("started_at", ""), row.get("run_id", "")))
            run_id = current["run_id"]
        else:
            current = _read_json(self._run_dir(run_id) / "status.json")
        if current.get("status") in {"running", "preparing", "cancel_requested"}:
            probe = _TrainingLock(self.candidates_root)
            if probe.acquire():
                try:
                    current = _read_json(self._run_dir(run_id) / "status.json")
                    if current.get("status") in {"running", "preparing", "cancel_requested"}:
                        current.update(status="interrupted", message="No worker holds the candidate lock; last complete checkpoint is preserved.")
                        current["resumable"] = self._has_checkpoint(self._run_dir(run_id))
                        self._status_write(self._run_dir(run_id), current)
                finally:
                    probe.release()
        if ((self._run_dir(run_id) / "cancel.flag").exists()
                and current.get("status") in {"running", "preparing", "cancel_requested"}):
            current = {**current, "status": "cancel_requested",
                       "message": "Cancellation requested; waiting for the worker to preserve the last complete epoch."}
        return {key: current.get(key) for key in
                ("run_id", "status", "epoch", "epochs", "message", "resumable", "train_loss",
                 "started_at", "worker_started_at", "process_id", "run_uuid", "manifest_sha256", "nondeployable") if key in current}

    def list_runs(self) -> list[dict]:
        result = []
        for path in self.candidates_root.iterdir():
            if not path.is_symlink() and path.is_dir() and len(path.name) == 32 and (path / "status.json").is_file():
                result.append(self.status(path.name))
        return sorted(result, key=lambda row: row.get("started_at", ""), reverse=True)

    @staticmethod
    def _has_checkpoint(run_dir: Path) -> bool:
        return (run_dir / "last_checkpoint.json").is_file()

    def _reap_handle(self, run_id: str) -> None:
        with self._handles_guard:
            process = self._handles.get(run_id)
        if process is not None and process.poll() is not None:
            with self._handles_guard:
                self._handles.pop(run_id, None)
