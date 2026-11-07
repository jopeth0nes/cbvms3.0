"""Independent, review-first uniform model evaluation; never loads or trains models."""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

CATEGORY_IDS = ("campus_uniform", "organizational_shirt", "other_clothing")
POLICY_LABELS = {"compliant", "violation"}
MANIFEST_SCHEMA = 1
POLICY_VERSION = "uniform-independent-evaluation-v1"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 12_000
MAX_RECORDS = 5_000
MAX_SET_BYTES = 512 * 1024 * 1024
HAMMING_OVERLAP_LIMIT = 5
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}
_ROOT_LOCKS: dict[str, threading.Lock] = {}
_ROOT_GUARD = threading.Lock()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _dataset_identity(manifest: dict) -> dict:
    """Content identity excludes import time/set id, preventing re-import bypass."""
    identity = {key: value for key, value in manifest.items()
                if key not in {"created_at", "source_manifest_sha256"}}
    identity["records"] = [
        {key: value for key, value in record.items() if key not in {"stored_path", "source_path"}}
        for record in manifest.get("records", [])
    ]
    return identity


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError(f"Evaluation metadata cannot be a symlink: {path.name}")
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable evaluation metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid evaluation metadata: {path.name}")
    return value


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 200 and not any(ord(c) < 32 for c in value)


def _known_group(value: str) -> bool:
    return bool(value.strip()) and value.strip().casefold() not in {
        "unknown", "unknown-provenance", "unknown provenance", "n/a", "na", "none", "null", "?"}


def _safe_relative(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Image paths must be nonempty relative POSIX paths")
    if re.match(r"^[A-Za-z]:", value):
        raise ValueError("Drive-qualified image paths are not allowed")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Image path escapes the manifest directory")
    return path


def _contained_regular_file(root: Path, relative: PurePosixPath) -> Path:
    path = root
    for component in relative.parts:
        path = path / component
        if path.is_symlink():
            raise ValueError("Image path contains a symlink")
    resolved = path.resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError("Image path is missing or escapes the manifest directory")
    return resolved


def _read_image(path: Path, expected_hash: str) -> tuple[bytes, dict]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Evaluation image must be a regular non-symlink file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise ValueError("Evaluation image exceeds the bounded file-size limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) != size or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Evaluation image changed during bounded read")
    digest = _sha_bytes(data)
    if digest != expected_hash:
        raise ValueError("Evaluation image SHA256 does not match the manifest")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image_format = (image.format or "").upper()
            if image_format not in ALLOWED_FORMATS or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Evaluation image format is unsupported or animated")
            if width < 16 or height < 16 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                raise ValueError("Evaluation image dimensions are outside supported bounds")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Evaluation image pixel count exceeds the supported limit")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            orientation = int(image.getexif().get(274, 1) or 1)
            gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = np.asarray(gray, dtype=np.uint8)
            digest_bits = np.packbits(pixels[:, 1:] > pixels[:, :-1]).tobytes()
            dhash = f"{int.from_bytes(digest_bits, 'big'):016x}"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Evaluation image is invalid or unreadable") from exc
    return data, {"format": image_format, "width": width, "height": height,
                  "exif_orientation": orientation, "dhash": dhash}


def _crop(value: Any, width: int, height: int) -> list[int]:
    if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int for v in value):
        raise ValueError("Crop must contain four integer original-raster coordinates")
    x1, y1, x2, y2 = value
    if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x2 - x1 < 16 or y2 - y1 < 16:
        raise ValueError("Crop is outside the original raster or too small")
    return list(value)


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> dict:
    if total <= 0:
        return {"n": 0, "successes": successes, "value": None, "ci95": None}
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return {"n": total, "successes": successes, "value": p,
            "ci95": {"low": max(0.0, center - margin), "high": min(1.0, center + margin)}}


def _safe_probabilities(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        result = {}
        for key, probability in value.items():
            if not isinstance(key, str) or isinstance(probability, (bool, np.bool_)) or not isinstance(probability, (int, float, np.number)):
                raise ValueError("Predictor probabilities must map string labels to finite numbers")
            number = float(probability)
            if not np.isfinite(number) or not 0.0 <= number <= 1.0:
                raise ValueError("Predictor probabilities must be finite values from 0 to 1")
            result[key] = number
        return result
    if isinstance(value, (list, tuple)):
        output = []
        for probability in value:
            if isinstance(probability, (bool, np.bool_)) or not isinstance(probability, (int, float, np.number)):
                raise ValueError("Predictor probabilities must be finite numbers")
            number = float(probability)
            if not np.isfinite(number) or not 0.0 <= number <= 1.0:
                raise ValueError("Predictor probabilities must be finite values from 0 to 1")
            output.append(number)
        return output
    raise ValueError("Predictor probabilities must be a label map, list, tuple, or None")


class _EvaluationLock:
    def __init__(self, root: Path):
        self.path = root / ".evaluation.lock"
        with _ROOT_GUARD:
            self.thread_lock = _ROOT_LOCKS.setdefault(str(root.resolve()), threading.Lock())
        self.stream = None

    def acquire(self) -> bool:
        if not self.thread_lock.acquire(blocking=False):
            return False
        try:
            if self.path.is_symlink():
                raise ValueError("Evaluation lock cannot be a symlink")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags, 0o600)
            self.stream = os.fdopen(descriptor, "a+")
            try:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stream.close()
                self.stream = None
                self.thread_lock.release()
                return False
            return True
        except Exception:
            if self.stream:
                self.stream.close()
                self.stream = None
            self.thread_lock.release()
            raise

    def release(self) -> None:
        if self.stream:
            try:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None
                self.thread_lock.release()


class EvaluationService:
    """Freeze explicitly reviewed final-test sets and evaluate paired predictors."""

    def __init__(self, root: str | Path | None = None):
        default = Path(__file__).resolve().parents[1] / "data" / "uniform_evaluations"
        self.root = Path(root).resolve() if root is not None else default
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sets_root = self.root / "sets"
        self.reports_root = self.root / "reports"
        if self.sets_root.is_symlink() or self.reports_root.is_symlink():
            raise ValueError("Evaluation storage directories cannot be symlinks")
        self.sets_root.mkdir(exist_ok=True, mode=0o700)
        self.reports_root.mkdir(exist_ok=True, mode=0o700)
        if self.sets_root.resolve().parent != self.root or self.reports_root.resolve().parent != self.root:
            raise ValueError("Evaluation storage directories must remain inside the configured root")
        self.lock_path = self.root / ".evaluation.lock"
        self.bindings_path = self.root / "final_test_bindings.json"

    @staticmethod
    def _set_id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("Invalid evaluation set id")
        return value

    @staticmethod
    def _report_id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("Invalid evaluation report id")
        return value

    def import_set(self, manifest_path: str | Path) -> str:
        path = Path(manifest_path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("Evaluation manifest must be a bounded regular non-symlink file")
        with path.open("rb") as stream:
            manifest_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(manifest_bytes) > MAX_MANIFEST_BYTES or len(manifest_bytes) != path.stat().st_size:
            raise ValueError("Evaluation manifest exceeds the size limit")
        try:
            manifest = json.loads(manifest_bytes)
        except json.JSONDecodeError as exc:
            raise ValueError("Evaluation manifest is invalid JSON") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA:
            raise ValueError("Unsupported evaluation manifest schema")
        if not _valid_identifier(manifest.get("version")):
            raise ValueError("Evaluation manifest version is required")
        if manifest.get("purpose") != "final_test":
            raise ValueError("Evaluation manifest purpose must be final_test")
        independence = manifest.get("independence")
        if not isinstance(independence, dict) or not all(_valid_identifier(independence.get(k)) for k in ("baseline_id", "reviewed_by", "statement")):
            raise ValueError("Explicit independence baseline, reviewer, and statement are required")
        if len(independence["statement"]) > 2000:
            raise ValueError("Independence statement is too long")
        if "synthetic" in manifest and type(manifest["synthetic"]) is not bool:
            raise ValueError("Manifest synthetic flag must be boolean")
        records = manifest.get("records")
        if not isinstance(records, list) or not records or len(records) > MAX_RECORDS:
            raise ValueError(f"Evaluation manifest must contain 1 to {MAX_RECORDS} records")
        source_root = path.parent.resolve()
        validated = []
        total_bytes = 0
        seen_hashes = set()
        for index, source in enumerate(records):
            if not isinstance(source, dict):
                raise ValueError(f"Record {index} must be an object")
            relative = _safe_relative(source.get("path"))
            image_path = _contained_regular_file(source_root, relative)
            digest = source.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"Record {index} has an invalid SHA256")
            if digest in seen_hashes:
                raise ValueError("Final-test manifest contains duplicate exact image hashes")
            seen_hashes.add(digest)
            data, image_info = _read_image(image_path, digest)
            total_bytes += len(data)
            if total_bytes > MAX_SET_BYTES:
                raise ValueError("Final-test image set exceeds total expanded byte limit")
            category = source.get("category")
            if category not in CATEGORY_IDS:
                raise ValueError(f"Record {index} has an unsupported category")
            if source.get("review_status") != "approved":
                raise ValueError(f"Record {index} must have explicit approved review status")
            group = source.get("capture_group")
            if not isinstance(group, str) or not _known_group(group) or len(group) > 200:
                raise ValueError(f"Record {index} requires known nonempty capture_group provenance")
            assessability = source.get("assessability")
            if assessability not in {"assessable", "unassessable", "unknown"}:
                raise ValueError(f"Record {index} has invalid assessability")
            policy_label = source.get("policy_label")
            if policy_label not in {"compliant", "violation", "unknown"}:
                raise ValueError(f"Record {index} policy label must be explicitly reviewed or unknown")
            for field in ("camera_id", "lighting"):
                val = source.get(field)
                if val is not None and (not isinstance(val, str) or len(val) > 200):
                    raise ValueError(f"Record {index} {field} must be a string or null")
            crop = _crop(source.get("crop"), image_info["width"], image_info["height"])
            validated.append({"source_path": relative.as_posix(), "source_absolute": image_path,
                              "sha256": digest, "category": category, "review_status": "approved",
                              "crop": crop, "capture_group": group.strip(), "camera_id": source.get("camera_id"),
                              "lighting": source.get("lighting"), "assessability": assessability,
                              "policy_label": policy_label, **image_info, "_bytes": data})

        # All records have been validated before a frozen directory is created.
        if self.sets_root.is_symlink() or self.sets_root.resolve().parent != self.root:
            raise ValueError("Evaluation set storage path is unsafe")
        lock = _EvaluationLock(self.root)
        if not lock.acquire():
            raise RuntimeError("Another evaluation import or evaluation is active")
        set_id = uuid.uuid4().hex
        staging = self.sets_root / f".{set_id}.tmp"
        final = self.sets_root / set_id
        try:
            if self.sets_root.is_symlink() or self.sets_root.resolve().parent != self.root:
                raise ValueError("Evaluation set storage path changed during import")
            staging.mkdir(mode=0o700)
            originals = staging / "originals"
            originals.mkdir(mode=0o700)
            frozen_records = []
            copied = {}
            for record in validated:
                data = record.pop("_bytes")
                current = _contained_regular_file(source_root, PurePosixPath(record["source_path"]))
                if _sha_file(current) != record["sha256"]:
                    raise ValueError("Evaluation image changed after preflight; no set was imported")
                extension = current.suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
                    extension = ".img"
                stored_rel = f"originals/{record['sha256']}{extension}"
                if stored_rel not in copied:
                    destination = staging / stored_rel
                    with destination.open("wb") as out:
                        out.write(data)
                        out.flush()
                        os.fsync(out.fileno())
                    os.chmod(destination, 0o400)
                    copied[stored_rel] = record["sha256"]
                frozen = {k: v for k, v in record.items() if k != "source_absolute"}
                frozen["stored_path"] = stored_rel
                frozen_records.append(frozen)
            frozen_manifest = {"schema_version": 1, "policy_version": POLICY_VERSION,
                               "version": manifest["version"],
                               "source_manifest_sha256": _sha_bytes(manifest_bytes),
                               "independence": independence, "records": frozen_records,
                               "created_at": _utc()}
            dataset_hash = _sha_bytes(_json_bytes(_dataset_identity(frozen_manifest)))
            image_inventory = sorted((row["stored_path"], row["sha256"]) for row in frozen_records)
            manifest_file_hash = _sha_bytes(_json_bytes(frozen_manifest))
            metadata = {"set_id": set_id, "dataset_sha256": dataset_hash,
                        "record_count": len(frozen_records), "source_manifest_sha256": frozen_manifest["source_manifest_sha256"],
                        "created_at": frozen_manifest["created_at"], "policy_version": POLICY_VERSION,
                        "frozen_manifest_sha256": manifest_file_hash,
                        "image_inventory_sha256": _sha_bytes(_json_bytes(image_inventory)),
                        "synthetic": bool(manifest.get("synthetic", False))}
            _atomic_json(staging / "frozen_manifest.json", frozen_manifest)
            _atomic_json(staging / "set.json", metadata)
            for path_item in staging.iterdir():
                if path_item.is_file():
                    os.chmod(path_item, 0o400)
            for directory in (originals, staging):
                dir_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            os.replace(staging, final)
            dir_fd = os.open(self.sets_root, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            return set_id
        except Exception:
            if staging.exists():
                for item in staging.rglob("*"):
                    try:
                        if item.is_file():
                            os.chmod(item, 0o600)
                    except OSError:
                        pass
                import shutil
                shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            lock.release()

    def _set_dir(self, set_id: str) -> Path:
        set_id = self._set_id(set_id)
        if self.sets_root.is_symlink() or self.sets_root.resolve().parent != self.root:
            raise ValueError("Evaluation set storage path is unsafe")
        path = self.sets_root / set_id
        if path.is_symlink() or not path.is_dir():
            raise FileNotFoundError("Evaluation set was not found")
        return path

    def load_set(self, set_id: str) -> dict:
        directory = self._set_dir(set_id)
        metadata = _read_json(directory / "set.json")
        manifest_path = directory / "frozen_manifest.json"
        manifest = _read_json(manifest_path)
        digest = _sha_bytes(_json_bytes(_dataset_identity(manifest)))
        if (digest != metadata.get("dataset_sha256") or metadata.get("set_id") != set_id
                or _sha_bytes(_json_bytes(manifest)) != metadata.get("frozen_manifest_sha256")):
            raise ValueError("Frozen evaluation set manifest hash mismatch")
        if len(manifest.get("records", [])) != metadata.get("record_count"):
            raise ValueError("Frozen evaluation record count mismatch")
        image_inventory = []
        for record in manifest["records"]:
            relative = _safe_relative(record.get("stored_path"))
            path = _contained_regular_file(directory, relative)
            if _sha_file(path) != record.get("sha256"):
                raise ValueError("Frozen evaluation image hash mismatch")
            image_inventory.append((relative.as_posix(), record["sha256"]))
        if _sha_bytes(_json_bytes(sorted(image_inventory))) != metadata.get("image_inventory_sha256"):
            raise ValueError("Frozen evaluation image inventory hash mismatch")
        return {**metadata, "independence": manifest["independence"], "records": manifest["records"],
                "frozen_manifest": manifest}

    def list_sets(self) -> list[dict]:
        if self.sets_root.is_symlink() or self.sets_root.resolve().parent != self.root:
            raise ValueError("Evaluation set storage path is unsafe")
        results = []
        for path in self.sets_root.iterdir():
            if path.is_symlink() or not path.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", path.name):
                continue
            try:
                metadata = _read_json(path / "set.json")
                results.append(metadata)
            except ValueError:
                continue
        return sorted(results, key=lambda row: (row.get("created_at", ""), row.get("set_id", "")), reverse=True)

    def _write_report(self, report_id: str, report: dict) -> None:
        if self.reports_root.is_symlink() or self.reports_root.resolve().parent != self.root:
            raise ValueError("Evaluation report storage path is unsafe")
        _atomic_json(self.reports_root / f"{report_id}.json", report)

    def _finalize_report(self, report_id: str, report: dict) -> None:
        self._write_report(report_id, report)
        path = self.reports_root / f"{report_id}.json"
        _atomic_json(self.reports_root / f"{report_id}.sha256.json", {
            "report_id": report_id, "sha256": _sha_file(path),
            "dataset_sha256": report.get("dataset_sha256"),
            "baseline_id": (report.get("baseline") or {}).get("id"),
            "candidate_id": (report.get("candidate") or {}).get("id"),
            "policy_version": report.get("policy_version"),
        })

    def _bind_policy_versions(self, dataset: dict, baseline: dict, candidate: dict,
                              report_id: str) -> str:
        """Persist once per content-addressed test set and ordered model pair."""
        key_material = {"dataset_sha256": dataset["dataset_sha256"],
                        "baseline_id": baseline["id"], "candidate_id": candidate["id"]}
        binding_id = _sha_bytes(_json_bytes(key_material))
        if self.bindings_path.is_symlink():
            raise ValueError("Final-test policy binding ledger cannot be a symlink")
        if self.bindings_path.exists():
            ledger = _read_json(self.bindings_path)
            if ledger.get("schema_version") != 1 or not isinstance(ledger.get("bindings"), dict):
                raise ValueError("Final-test policy binding ledger is invalid")
        else:
            ledger = {"schema_version": 1, "bindings": {}}
        expected = {**key_material, "binding_id": binding_id,
                    "baseline_policy_version": baseline.get("policy_version"),
                    "candidate_policy_version": candidate.get("policy_version")}
        existing = ledger["bindings"].get(binding_id)
        if existing is not None:
            fields = ("dataset_sha256", "baseline_id", "candidate_id",
                      "baseline_policy_version", "candidate_policy_version")
            if any(existing.get(field) != expected.get(field) for field in fields):
                raise ValueError("Policy version is already bound for this final-test set and model pair")
        else:
            ledger["bindings"][binding_id] = {**expected, "first_report_id": report_id,
                                               "bound_at": _utc()}
            _atomic_json(self.bindings_path, ledger)
        return binding_id

    def _overlap_check(self, dataset: dict, baseline: dict, candidate: dict) -> dict:
        rows = dataset["records"]
        evaluation_hashes = {row["sha256"] for row in rows}
        evaluation_groups = {row["capture_group"] for row in rows}
        overlap = {}
        for role, descriptor in (("baseline", baseline), ("candidate", candidate)):
            training_hashes = set(descriptor["training_hashes"])
            training_groups = set(descriptor["training_groups"])
            exact = sorted(evaluation_hashes & training_hashes)
            groups = sorted(evaluation_groups & training_groups)
            hashes = descriptor["training_dhashes"]
            chunk_index = _DHashIndex(hashes)
            near = []
            for row in rows:
                match = chunk_index.near(row["dhash"])
                if match is not None:
                    near.append({"image_sha256": row["sha256"], "training_dhash": match[0], "distance": match[1]})
            overlap[role] = {"exact_hashes": exact, "capture_groups": groups, "near_duplicates": near}
        overlap["possible_overlap"] = any(v["exact_hashes"] or v["capture_groups"] or v["near_duplicates"]
                                          for v in overlap.values())
        return overlap

    @staticmethod
    def _validate_descriptor(value: Any, role: str) -> dict:
        if not isinstance(value, dict) or not _valid_identifier(value.get("id")):
            raise ValueError(f"{role} model descriptor requires a stable id")
        labels = value.get("labels")
        if (not isinstance(labels, list) or not labels or len(labels) > 100
                or any(not _valid_identifier(label) for label in labels) or len(set(labels)) != len(labels)):
            raise ValueError(f"{role} descriptor labels must be unique nonempty strings")
        preproc = value.get("preprocessing_version")
        if not _valid_identifier(preproc):
            raise ValueError(f"{role} descriptor requires preprocessing_version")
        normalized = dict(value)
        for key, pattern in (("training_hashes", r"[0-9a-f]{64}"), ("training_dhashes", r"[0-9a-f]{16}")):
            items = value.get(key)
            if (not isinstance(items, list) or not items or len(items) > 100_000
                    or any(not isinstance(item, str) or not re.fullmatch(pattern, item) for item in items)):
                raise ValueError(f"{role} descriptor {key} must be a list of valid hashes")
            normalized[key] = sorted(set(items))
        groups = value.get("training_groups")
        if (not isinstance(groups, list) or not groups or len(groups) > 100_000
                or any(not isinstance(group, str) or not _known_group(group) for group in groups)):
            raise ValueError(f"{role} descriptor training_groups must be known nonempty strings")
        normalized["training_groups"] = sorted(set(groups))
        if type(value.get("synthetic")) is not bool:
            raise ValueError(f"{role} descriptor synthetic must be boolean")
        if value.get("policy_version") is not None and not _valid_identifier(value.get("policy_version")):
            raise ValueError(f"{role} descriptor policy_version must be a string or null")
        if value.get("dataset_version") is not None and not _valid_identifier(value.get("dataset_version")):
            raise ValueError(f"{role} descriptor dataset_version must be a string or null")
        normalized["labels"] = list(labels)
        return normalized

    @staticmethod
    def _predict(predictor: Callable, image_bgr: np.ndarray, descriptor: dict) -> dict:
        result = predictor(image_bgr)
        if not isinstance(result, dict):
            raise ValueError("Predictor must return an object")
        label = result.get("label")
        if label is not None and (not isinstance(label, str) or label not in descriptor["labels"]):
            raise ValueError(f"Predictor returned label outside descriptor labels: {label!r}")
        policy_label = result.get("policy_label")
        if policy_label not in POLICY_LABELS | {None}:
            raise ValueError("Predictor policy_label must be compliant, violation, or null")
        reason = result.get("reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ValueError("Predictor reason must be a string or null")
        probabilities = _safe_probabilities(result.get("probabilities"))
        if isinstance(probabilities, dict) and any(key not in descriptor["labels"] for key in probabilities):
            raise ValueError("Predictor probability map contains labels outside the descriptor")
        if isinstance(probabilities, list) and len(probabilities) != len(descriptor["labels"]):
            raise ValueError("Predictor probability list length does not match descriptor labels")
        return {"label": label, "policy_label": policy_label,
                "probabilities": probabilities, "reason": reason}

    @staticmethod
    def _is_cancelled(cancelled) -> bool:
        if cancelled is None:
            return False
        if callable(cancelled):
            return bool(cancelled())
        is_set = getattr(cancelled, "is_set", None)
        if callable(is_set):
            return bool(is_set())
        raise ValueError("cancelled must be a predicate, Event-like object, or None")

    @staticmethod
    def _confusion(rows: list[dict], model_key: str, labels: list[str]) -> dict:
        columns = list(labels) + ["unassessed"]
        matrix = {truth: {prediction: 0 for prediction in columns} for truth in CATEGORY_IDS}
        for row in rows:
            label = row[model_key]["label"]
            matrix[row["category"]][label if label in labels else "unassessed"] += 1
        return {"rows_true": list(CATEGORY_IDS), "columns_predicted": columns, "counts": matrix}

    @staticmethod
    def _class_metrics(rows: list[dict], model_key: str) -> dict:
        supports = Counter(row["category"] for row in rows)
        metrics = {}
        for category in CATEGORY_IDS:
            true_positive = sum(r["category"] == category and r[model_key]["label"] == category for r in rows)
            predicted = sum(r[model_key]["label"] == category for r in rows)
            support = supports[category]
            precision = _wilson(true_positive, predicted)
            recall = _wilson(true_positive, support)
            metrics[category] = {"support": support, "predicted_count": predicted,
                                 "precision": precision, "recall": recall}
        return metrics

    @staticmethod
    def _policy_metrics(rows: list[dict], model_key: str) -> dict:
        eligible = [row for row in rows if row["policy_label"] in POLICY_LABELS]
        predicted = [row for row in eligible if row[model_key]["policy_label"] in POLICY_LABELS]
        truth_compliant = sum(row["policy_label"] == "compliant" for row in predicted)
        truth_violation = sum(row["policy_label"] == "violation" for row in predicted)
        false_violations = sum(row["policy_label"] == "compliant" and row[model_key]["policy_label"] == "violation" for row in predicted)
        missed_violations = sum(row["policy_label"] == "violation" and row[model_key]["policy_label"] == "compliant" for row in predicted)
        return {"explicit_truth_count": len(eligible), "truth_unknown_count": len(rows) - len(eligible),
                "prediction_unassessed_count": len(eligible) - len(predicted),
                "assessed_count": len(predicted),
                "false_violation_rate_on_compliant": _wilson(false_violations, truth_compliant),
                "missed_violation_rate": _wilson(missed_violations, truth_violation),
                "false_violation_count": false_violations, "missed_violation_count": missed_violations,
                "interpretation": "Policy labels are independent reviewed outcomes; garment categories are never mapped to compliance."}

    def evaluate(self, set_id: str, baseline: dict, candidate: dict,
                 baseline_predict: Callable[[np.ndarray], dict], candidate_predict: Callable[[np.ndarray], dict],
                 cancelled: Callable[[], bool] | None = None,
                 progress: Callable[..., None] | None = None) -> str:
        report_id = uuid.uuid4().hex
        report = {"report_id": report_id, "set_id": set_id, "status": "evaluating",
                  "started_at": _utc(), "metrics": None, "predictions": [], "failure": None,
                  "independent": False, "nondeployable": True, "experimental": False,
                  "limitations": ["Evaluation results are measurements, not an activation or accuracy acceptance decision.",
                                  "No model was loaded, tuned, trained, or activated by this service."]}
        lock = _EvaluationLock(self.root)
        if not lock.acquire():
            raise RuntimeError("Another evaluation import or evaluation is active")
        try:
            self._write_report(report_id, report)
            dataset = self.load_set(set_id)
            baseline_desc = self._validate_descriptor(baseline, "baseline")
            candidate_desc = self._validate_descriptor(candidate, "candidate")
            if set(candidate_desc["labels"]) != set(CATEGORY_IDS):
                raise ValueError("Candidate descriptor must explicitly use the fixed three garment categories")
            binding_id = self._bind_policy_versions(dataset, baseline_desc, candidate_desc, report_id)
            overlap_evidence = self._overlap_check(dataset, baseline_desc, candidate_desc)
            synthetic = bool(dataset.get("synthetic") or baseline_desc["synthetic"] or candidate_desc["synthetic"])
            def descriptor_snapshot(desc):
                return {"id": desc["id"], "labels": desc["labels"],
                        "dataset_version": desc.get("dataset_version"),
                        "preprocessing_version": desc["preprocessing_version"],
                        "training_inventory": {"hash_count": len(desc["training_hashes"]),
                                               "capture_group_count": len(desc["training_groups"]),
                                               "dhash_count": len(desc["training_dhashes"]),
                                               "sha256": _sha_bytes(_json_bytes({"hashes": desc["training_hashes"],
                                                                                "groups": desc["training_groups"],
                                                                                "dhashes": desc["training_dhashes"]}))},
                        "synthetic": desc["synthetic"], "policy_version": desc.get("policy_version")}
            report.update(dataset_sha256=dataset["dataset_sha256"],
                          evaluation_binding_id=binding_id,
                          frozen_manifest_sha256=_sha_file(self._set_dir(set_id) / "frozen_manifest.json"),
                          independence=dataset["independence"], overlap=overlap_evidence,
                          baseline=descriptor_snapshot(baseline_desc),
                          candidate=descriptor_snapshot(candidate_desc),
                          synthetic=synthetic, experimental=synthetic,
                          eligible_for_real_accuracy_claims=not synthetic,
                          evaluation_policy_version=POLICY_VERSION,
                          policy_version={"baseline": baseline_desc.get("policy_version"),
                                          "candidate": candidate_desc.get("policy_version"),
                                          "comparable": bool(baseline_desc.get("policy_version") and
                                                             baseline_desc.get("policy_version") == candidate_desc.get("policy_version"))})
            if synthetic:
                report["limitations"].append("Synthetic set or model descriptor: results are experimental and ineligible for real-data accuracy claims.")
            if dataset["independence"].get("baseline_id") != baseline_desc["id"]:
                raise ValueError("Final-test independence declaration does not bind to this baseline descriptor")
            if overlap_evidence["possible_overlap"]:
                raise ValueError("Possible training/test overlap detected; exact, capture-group, or near-duplicate evidence is present")
            examples = []
            records = dataset["records"]
            for index, frozen in enumerate(records):
                if self._is_cancelled(cancelled):
                    report.update(status="cancelled", finished_at=_utc(),
                                  message="Evaluation cancelled; partial predictions are retained and no metrics are reported.",
                                  predictions=examples, metrics=None)
                    self._finalize_report(report_id, report)
                    return report_id
                image_path = _contained_regular_file(self._set_dir(set_id), _safe_relative(frozen["stored_path"]))
                data, _ = _read_image(image_path, frozen["sha256"])
                with Image.open(io.BytesIO(data)) as image:
                    raster = image.convert("RGB")
                x1, y1, x2, y2 = frozen["crop"]
                crop_rgb = np.asarray(raster.crop((x1, y1, x2, y2)), dtype=np.uint8)
                crop_bgr = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR)
                base_output = self._predict(baseline_predict, crop_bgr.copy(), baseline_desc)
                candidate_output = self._predict(candidate_predict, crop_bgr.copy(), candidate_desc)
                examples.append({"index": index, "image_sha256": frozen["sha256"], "category": frozen["category"],
                                 "capture_group": frozen["capture_group"], "camera_id": frozen["camera_id"],
                                 "lighting": frozen["lighting"], "assessability": frozen["assessability"],
                                 "policy_label": frozen["policy_label"], "baseline": base_output,
                                 "candidate": candidate_output})
                report["predictions"] = examples
                if progress is not None:
                    progress(index + 1, len(records), f"Evaluated {index + 1}/{len(records)} reviewed examples")
                self._write_report(report_id, report)

            class_rows = [e for e in examples if e["assessability"] == "assessable"]
            def classification_unassessed(model_key):
                assessable_count = sum(e[model_key]["label"] is None for e in class_rows)
                all_count = sum(e[model_key]["label"] is None for e in examples)
                assessable_rate = _wilson(assessable_count, len(class_rows))
                assessable_rate.update(population="assessable_reviewed_class_metric_rows",
                                       denominator=len(class_rows))
                all_rate = _wilson(all_count, len(examples))
                all_rate.update(population="all_reviewed_examples",
                                denominator=len(examples))
                return {"unassessed_count": assessable_count,
                        "unassessed_rate": assessable_rate,
                        "all_example_unassessed_count": all_count,
                        "all_example_unassessed_rate": all_rate}
            candidate_metrics = {"confusion": self._confusion(class_rows, "candidate", list(CATEGORY_IDS)),
                                 "per_class": self._class_metrics(class_rows, "candidate"),
                                 "total": len(examples), "metric_population": len(class_rows),
                                 "excluded_nonassessable_count": len(examples) - len(class_rows),
                                 "assessed": sum(e["candidate"]["label"] is not None for e in class_rows)}
            candidate_metrics.update(classification_unassessed("candidate"))
            baseline_metrics = {"confusion": self._confusion(class_rows, "baseline", baseline_desc["labels"]),
                                "total": len(examples), "assessed": sum(e["baseline"]["label"] is not None for e in class_rows),
                                "metric_population": len(class_rows),
                                "excluded_nonassessable_count": len(examples) - len(class_rows)}
            baseline_metrics.update(classification_unassessed("baseline"))
            if set(baseline_desc["labels"]) == set(CATEGORY_IDS):
                baseline_metrics["per_class"] = self._class_metrics(class_rows, "baseline")
                baseline_metrics["comparison"] = {category: {
                    "candidate_recall": candidate_metrics["per_class"][category]["recall"],
                    "baseline_recall": baseline_metrics["per_class"][category]["recall"],
                    "recall_delta": _rate_delta(candidate_metrics["per_class"][category]["recall"],
                                                 baseline_metrics["per_class"][category]["recall"])}
                    for category in CATEGORY_IDS}
            elif set(baseline_desc["labels"]) == {"correct_uniform", "wrong_uniform"}:
                campus = [e for e in class_rows if e["category"] == "campus_uniform"]
                response = sum(e["baseline"]["label"] == "correct_uniform" for e in campus)
                baseline_metrics["per_class"] = None
                baseline_metrics["per_class_unavailable_reason"] = (
                    "Baseline labels are not the fixed three garment classes; legacy correct_uniform/wrong_uniform "
                    "cannot be mapped semantically to garment categories.")
                baseline_metrics["campus_correct_uniform_response_proxy"] = _wilson(response, len(campus))
                baseline_metrics["proxy_interpretation"] = (
                    "Among reviewed Campus Uniform examples, fraction receiving the legacy correct_uniform response; "
                    "this is not semantic Campus Uniform recall.")
                baseline_metrics["candidate_campus_recall"] = candidate_metrics["per_class"]["campus_uniform"]["recall"]
            else:
                baseline_metrics["per_class"] = None
                baseline_metrics["per_class_unavailable_reason"] = (
                    "Baseline labels do not match the fixed three garment categories or the known legacy "
                    "correct_uniform/wrong_uniform labels; no semantic mapping is inferred.")
            policy = {"baseline": self._policy_metrics(examples, "baseline"),
                      "candidate": self._policy_metrics(examples, "candidate")}
            policy["comparable_versions"] = bool(baseline_desc.get("policy_version") and
                                                 baseline_desc.get("policy_version") == candidate_desc.get("policy_version"))
            policy["comparison_reason"] = ("Both descriptors declare the same policy version."
                                            if policy["comparable_versions"] else
                                            "Policy versions are missing or differ; per-model counts are separate and not compared.")
            coverage = _coverage(examples)
            if set(baseline_desc["labels"]) == set(CATEGORY_IDS):
                campus_comparison = {"kind": "three_class_recall_comparison",
                                     "candidate_campus_recall": candidate_metrics["per_class"]["campus_uniform"]["recall"],
                                     "baseline_campus_recall": baseline_metrics["per_class"]["campus_uniform"]["recall"],
                                     "recall_delta": baseline_metrics["comparison"]["campus_uniform"]["recall_delta"]}
            elif set(baseline_desc["labels"]) == {"correct_uniform", "wrong_uniform"}:
                campus_comparison = {"kind": "legacy_response_proxy",
                                     "candidate_campus_recall": candidate_metrics["per_class"]["campus_uniform"]["recall"],
                                     "baseline_correct_uniform_response_proxy": baseline_metrics["campus_correct_uniform_response_proxy"],
                                     "interpretation": baseline_metrics["proxy_interpretation"]}
            else:
                campus_comparison = {"kind": "not_comparable",
                                     "candidate_campus_recall": candidate_metrics["per_class"]["campus_uniform"]["recall"],
                                     "reason": baseline_metrics["per_class_unavailable_reason"]}
            per_model = {"baseline": {"classification": baseline_metrics, "policy": policy["baseline"]},
                         "candidate": {"classification": candidate_metrics, "policy": policy["candidate"]}}
            policy_version = {"baseline": baseline_desc.get("policy_version"),
                              "candidate": candidate_desc.get("policy_version"),
                              "comparable": policy["comparable_versions"]}
            report.update(status="completed", finished_at=_utc(),
                          independent=True, metrics={"candidate": candidate_metrics, "baseline": baseline_metrics,
                                   "policy": policy,
                                   "coverage": coverage,
                                   "assessability": dict(Counter(e["assessability"] for e in examples))},
                          per_model=per_model, coverage=coverage, campus_comparison=campus_comparison,
                          policy_version=policy_version, predictions=examples,
                          message="Paired predictions completed on the same frozen reviewed crops; no model was loaded by this service.")
            self._finalize_report(report_id, report)
            return report_id
        except BaseException as exc:
            report.update(status="failed", finished_at=_utc(), failure=f"{type(exc).__name__}: {exc}",
                          metrics=None, message="Evaluation failed; no metrics are reported.")
            self._finalize_report(report_id, report)
            return report_id
        finally:
            lock.release()

    def list_reports(self) -> list[dict]:
        self._recover_interrupted_reports()
        if self.reports_root.is_symlink() or self.reports_root.resolve().parent != self.root:
            raise ValueError("Evaluation report storage path is unsafe")
        reports = []
        for path in self.reports_root.glob("*.json"):
            if path.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                continue
            try:
                report = _read_json(path)
                reports.append({k: report.get(k) for k in ("report_id", "set_id", "status", "started_at", "finished_at", "message", "failure")})
            except ValueError:
                continue
        return sorted(reports, key=lambda row: (row.get("started_at", ""), row.get("report_id", "")), reverse=True)

    def load_report(self, report_id: str) -> dict:
        self._recover_interrupted_reports()
        if self.reports_root.is_symlink() or self.reports_root.resolve().parent != self.root:
            raise ValueError("Evaluation report storage path is unsafe")
        report_id = self._report_id(report_id)
        path = self.reports_root / f"{report_id}.json"
        report = _read_json(path)
        if report.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            sidecar = _read_json(self.reports_root / f"{report_id}.sha256.json")
            if sidecar.get("report_id") != report_id or sidecar.get("sha256") != _sha_file(path):
                raise ValueError("Evaluation report checksum integrity verification failed")
        return report

    def report_checksum(self, report_id: str) -> str:
        if self.reports_root.is_symlink() or self.reports_root.resolve().parent != self.root:
            raise ValueError("Evaluation report storage path is unsafe")
        report_id = self._report_id(report_id)
        path = self.reports_root / f"{report_id}.json"
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Evaluation report was not found or is unsafe")
        report = self.load_report(report_id)
        if report.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("Evaluation report has no immutable terminal checksum yet")
        return _sha_file(path)

    def _recover_interrupted_reports(self) -> None:
        if self.reports_root.is_symlink() or self.reports_root.resolve().parent != self.root:
            return
        lock = _EvaluationLock(self.root)
        if not lock.acquire():
            return
        try:
            for path in self.reports_root.glob("*.json"):
                if path.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                    continue
                try:
                    report = _read_json(path)
                except ValueError:
                    continue
                if report.get("status") == "evaluating":
                    report.update(status="interrupted", finished_at=_utc(), metrics=None,
                                  message="Evaluation process ended before completion; no metrics are reported.")
                    self._finalize_report(report["report_id"], report)
        finally:
            lock.release()


def _rate_delta(candidate: dict, baseline: dict) -> dict:
    if candidate.get("value") is None or baseline.get("value") is None:
        return {"value": None, "reason": "At least one denominator is zero; no delta is defined."}
    return {"value": candidate["value"] - baseline["value"],
            "reason": "Difference of paired-set point estimates; confidence interval is not inferred from marginals."}


class _DHashIndex:
    """Pigeonhole index for exact detection of 64-bit dHash distance <= 5."""
    _widths = (11, 11, 11, 11, 10, 10)
    _starts = (0, 11, 22, 33, 44, 54)

    def __init__(self, values: list[str]):
        self.buckets: dict[tuple[int, int], list[str]] = {}
        for value in values:
            number = int(value, 16)
            for index, (start, width) in enumerate(zip(self._starts, self._widths)):
                chunk = (number >> start) & ((1 << width) - 1)
                self.buckets.setdefault((index, chunk), []).append(value)

    def near(self, value: str) -> tuple[str, int] | None:
        number = int(value, 16)
        candidates = set()
        for index, (start, width) in enumerate(zip(self._starts, self._widths)):
            chunk = (number >> start) & ((1 << width) - 1)
            candidates.update(self.buckets.get((index, chunk), ()))
        best = None
        best_distance = HAMMING_OVERLAP_LIMIT + 1
        for candidate in candidates:
            distance = (number ^ int(candidate, 16)).bit_count()
            if distance < best_distance:
                best, best_distance = candidate, distance
        return (best, best_distance) if best is not None and best_distance <= HAMMING_OVERLAP_LIMIT else None


def _coverage(examples: list[dict]) -> dict:
    result = {"total": len(examples), "by_category": dict(Counter(e["category"] for e in examples)),
              "by_capture_group": dict(Counter(e["capture_group"] for e in examples)),
              "by_camera_id": dict(Counter(e["camera_id"] or "(missing)" for e in examples)),
              "by_lighting": dict(Counter(e["lighting"] or "(missing)" for e in examples)),
              "missing_camera_id": sum(e["camera_id"] is None for e in examples),
              "missing_lighting": sum(e["lighting"] is None for e in examples),
              "missing_policy_truth": sum(e["policy_label"] == "unknown" for e in examples),
              "per_class": {category: sum(e["category"] == category for e in examples) for category in CATEGORY_IDS}}
    return result
