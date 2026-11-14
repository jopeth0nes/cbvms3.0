"""Isolated uniform image catalog with automatic dataset processing.

This module never writes to the active trainer, production database, or model
artifacts. Originals are content-addressed and retained byte-for-byte.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

import cv2
import numpy as np
import copy
import os
from PIL import Image, UnidentifiedImageError

from core.trainer import TRAIN_DIR
from core.uniform_logo_annotations import (
    is_eligible_visible_annotation, logo_readiness, normalize_visibility,
)

PREPROCESS_VERSION = "uniform-stage1-raster-v1"
CATEGORIES = {
    "campus_uniform": "Campus Uniform",
    "organizational_shirt": "Organizational Shirt",
    # Stable persisted key/class ID; only the user-facing term changes.
    "other_clothing": "Wrong Uniform",
    "unassigned": "Unassigned (Legacy Review)",
}
GARMENT_CATEGORIES = frozenset({"campus_uniform", "organizational_shirt", "other_clothing"})
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_DIMENSION = 12_000
MAX_ZIP_FILES = 500
MAX_ZIP_EXPANDED = 200 * 1024 * 1024
MAX_ZIP_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def visually_similar(a, b):
    """Shared perceptual estimate; explicit inspection corrections take precedence."""
    if b.get('image_id') in a.get('unrelated_image_ids', []) or a.get('image_id') in b.get('unrelated_image_ids', []):
        return False
    try:
        return (int(a['dhash'], 16) ^ int(b['dhash'], 16)).bit_count() <= 4
    except (KeyError, TypeError, ValueError):
        return False


class ImportLimitError(ValueError):
    """A bounded import stopped with actionable, retry-safe progress counts."""

    def __init__(self, limit_name: str, configured_limit: int, *, imported: int,
                 duplicates: int, unprocessed: int | None, measured_value: int | float | None = None,
                 unit: str = "files"):
        self.limit_name = limit_name
        self.configured_limit = configured_limit
        self.imported = imported
        self.duplicates = duplicates
        self.unprocessed = unprocessed
        self.measured_value = measured_value
        remaining = unprocessed if unprocessed is not None else "all (count unavailable; archive rejected before opening)"
        observed = f"; observed {measured_value} {unit}" if measured_value is not None else ""
        super().__init__(f"Import exceeded {limit_name} limit ({configured_limit} {unit}{observed}). "
                         f"Imported: {imported}; skipped as duplicates: {duplicates}; "
                         f"left unprocessed: {remaining}. Retry the remaining files in smaller batches.")


class BulkApprovalError(RuntimeError):
    """Atomic bulk-approval failure with counts captured under the catalog lock."""

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


class ImportSummary(list):
    """List-compatible result carrying counts for the UI without changing callers."""

    def __init__(self, ids=(), *, imported=0, duplicates=0, unprocessed=0):
        super().__init__(ids)
        self.imported = imported
        self.duplicates = duplicates
        self.unprocessed = unprocessed

    def message(self):
        return (f"Imported: {self.imported}; skipped as duplicates: {self.duplicates}; "
                f"left unprocessed: {self.unprocessed}.")


class _ImportProgress:
    """One latest state, with bounded callback frequency and forced phase/final updates."""
    def __init__(self, callback, cancel):
        self.callback, self.cancel = callback, cancel
        self.state = dict(phase="discovery", discovered=0, total=None, processed=0,
                          imported=0, duplicates=0, skipped=0, failed=0,
                          current=None, image_ids=[], cancelled=False)
        self._last_emit = 0.0
        self._emit(force=True)

    def _emit(self, force=False):
        self.state["cancelled"] = bool(self.cancel and self.cancel.is_set())
        now = time.monotonic()
        if self.callback and (force or now - self._last_emit >= .1):
            self.callback(copy.deepcopy(self.state))
            self._last_emit = now

    def update(self, **values):
        force = (any(key in values and values[key] != self.state[key] for key in ("phase", "total"))
                 or self.state["discovered"] == 0 and values.get("discovered", 0) > 0
                 or self.state["phase"] == "importing" and "current" in values and values["current"] != self.state["current"])
        self.state.update(values)
        self._emit(force=force)

    def check(self):
        if self.cancel and self.cancel.is_set():
            self._emit(force=True)
            return True
        return False



def _is_supported_zip_image(info) -> bool:
    return (not info.is_dir() and "__MACOSX" not in PurePosixPath(info.filename).parts
            and Path(info.filename).suffix.lower() in
            {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _review_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category: {category}")


def _image_info(data: bytes) -> tuple[str, int, int, str, int]:
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("Image is empty or exceeds the file size limit")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            width, height = im.size
            if fmt not in ALLOWED_FORMATS:
                raise ValueError("Unsupported image format")
            if width < 16 or height < 16 or width > MAX_DIMENSION or height > MAX_DIMENSION:
                raise ValueError("Image dimensions are outside the supported bounds")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Image pixel count exceeds the supported limit")
            im.verify()
        # Force decode too; verify() alone does not prove all pixel data is readable.
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            orientation = int(im.getexif().get(274, 1) or 1)
            gray = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            px = np.asarray(gray, dtype=np.uint8)
            bits = px[:, 1:] > px[:, :-1]
            dhash = f"{int.from_bytes(np.packbits(bits).tobytes(), 'big'):016x}"
        return fmt, width, height, dhash, orientation
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid or unreadable image") from exc


def _blur_flag(data: bytes) -> list[str]:
    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            im.thumbnail((512, 512), Image.Resampling.LANCZOS)
            arr = np.asarray(im.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        flags = []
        if min(w, h) < 160:
            flags.append("low_resolution")
        if blur < 25:
            flags.append("possible_blur")
        return flags
    except Exception:
        return ["image_quality_check_failed"]


class UniformDataset:
    """JSON catalog backed by immutable originals beneath ``root``."""

    def __init__(self, root: str | Path | None = None, *, auto_process: bool = True):
        self.auto_process = auto_process
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[1] / "data" / "uniform_stage1"
        self.root.mkdir(parents=True, exist_ok=True)
        self.images_dir = self.root / "originals"
        self.images_dir.mkdir(exist_ok=True)
        self.index_path = self.root / "catalog.json"
        self.lock_path = self.root / ".catalog.lock"
        with _LOCKS_GUARD:
            self._thread_lock = _PROCESS_LOCKS.setdefault(str(self.root.resolve()), threading.RLock())

    def _locked(self):
        return _CatalogLock(self._thread_lock, self.lock_path)

    def _load(self) -> dict:
        if not self.index_path.exists():
            return {"schema_version": 1, "preprocess_version": PREPROCESS_VERSION, "records": []}
        try:
            value = json.loads(self.index_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Uniform dataset catalog is unreadable; refusing to overwrite it") from exc
        if value.get("schema_version") != 1 or not isinstance(value.get("records"), list):
            raise RuntimeError("Unsupported uniform dataset catalog schema")
        return value

    def _save(self, catalog: dict) -> None:
        fd, name = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(catalog, f, ensure_ascii=False, sort_keys=True, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.index_path)
            try:
                dfd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def _import_bytes(self, data: bytes, source_path: str, kind: str, category: str,
                      capture_group: str | None, legacy_label: str | None = None,
                      archive_sha256: str | None = None, *, resolution: dict | None = None) -> str:
        _validate_category(category)
        fmt, width, height, dhash, orientation = _image_info(data)
        digest = hashlib.sha256(data).hexdigest()
        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tiff"}[fmt]
        group = str(capture_group).strip() if capture_group is not None and str(capture_group).strip() else None
        source = {"path": source_path, "kind": kind, "capture_group": group, "category": category}
        if kind == "zip":
            source["archive_sha256"] = archive_sha256
        if legacy_label is not None:
            source["legacy_label"] = legacy_label
        with self._locked():
            catalog = self._load()
            records = catalog["records"]
            record = next((r for r in records if r["sha256"] == digest), None)
            if resolution is not None:
                expected = resolution.get("exact_category")
                if (record is None) != (expected is None) or (record is not None and record.get("category") != expected):
                    raise ValueError("Dataset changed since duplicate review; retry to review the current matches.")
                if record is None:
                    near = {r['image_id'] for r in records if visually_similar(r, {'image_id': digest, 'dhash': dhash})}
                    if near - set(resolution.get('near_ids', [])):
                        raise ValueError("New similar photos appeared since review; retry the import.")
                else:
                    action = resolution['action']
                    if action not in {'add_source', 'use_existing', 'change_category'}:
                        raise ValueError("Invalid exact duplicate decision")
                    old_category = record['category']
                    effective = category if action == 'change_category' else old_category
                    source['category'] = effective
                    if effective != category:
                        source['requested_category'] = category
                    identity = lambda item: tuple(item.get(k) for k in ('path', 'kind', 'capture_group', 'archive_sha256'))
                    added = not any(identity(item) == identity(source) for item in record['sources'])
                    if added:
                        record['sources'].append(source)
                    if action == 'change_category':
                        record['category'] = category
                        record['flags'] = [f for f in record.get('flags', []) if f not in {'label_conflict', 'category_conflict'}]
                        record.update(processing_state='queued', training_eligible=False,
                                      eligibility_reasons=['correction_awaiting_processing'])
                        record.setdefault('review_history', []).append(dict(at=_now(), action='import_category_change',
                            prior_category=old_category, category=category, source=source_path))
                        if record.get('logo_annotation'):
                            record['logo_annotation']['reviewed'] = False
                    record['capture_groups'] = sorted({s['capture_group'] for s in record['sources'] if s.get('capture_group')})
                    record['has_unknown_provenance'] = any(not s.get('capture_group') for s in record['sources'])
                    self._save(catalog)
                    result = dict(image_id=digest, imported=False, source_added=added)
                    try:
                        self.schedule_processing()
                    except Exception as exc:
                        result['processing_error'] = str(exc)
                    return result
            if record is not None:
                same_source = source in record["sources"]
                if not same_source:
                    record["sources"].append(source)
                source_categories = {s.get("category") for s in record["sources"] if s.get("category")}
                source_legacy_labels = {s.get("legacy_label") for s in record["sources"] if s.get("legacy_label")}
                conflict = (not same_source and (category != record.get("category")
                            or len(source_categories) > 1 or len(source_legacy_labels) > 1))
                if conflict:
                    record["flags"] = sorted(set(record.get("flags", [])) | {"label_conflict"})
                    if record.get("status") != "excluded":
                        record["status"] = "pending_review"
                    record.update(processing_state="quarantined", training_eligible=False,
                                  eligibility_reasons=["label_conflict"])
                if legacy_label is not None and not same_source:
                    source["legacy_label"] = legacy_label
                record["capture_groups"] = sorted({s["capture_group"] for s in record["sources"] if s.get("capture_group")})
                record["has_unknown_provenance"] = any(not s.get("capture_group") for s in record["sources"])
                self._save(catalog)
                self.schedule_processing()
                return record["image_id"]
            rel = f"originals/{digest}{ext}"
            dest = self.root / rel
            if not dest.exists():
                fd, temp_name = tempfile.mkstemp(prefix=".original-", dir=self.images_dir)
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_name, dest)
                finally:
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
            record = {
                "image_id": digest, "sha256": digest, "original_path": rel,
                "format": fmt, "width": width, "height": height, "exif_orientation": orientation,
                "preprocess_version": PREPROCESS_VERSION, "crop_source": None, "imported_at": _now(),
                "sources": [source], "category": category, "status": "pending_review",
                "processing_state": "queued", "training_eligible": False, "eligibility_reasons": ["awaiting_processing"],
                "crop": None, "logo_visibility": "unknown", "logo_box": None,
                "flags": _blur_flag(data) + ["provenance_association_uncertain"], "notes": "",
                "capture_group": group, "provenance_group": group or "unknown-provenance",
                "capture_groups": [group] if group else [], "has_unknown_provenance": group is None,
                "dhash": dhash, "duplicate_of": [], "review_history": [], "crop_proposal_history": [],
            }
            records.append(record)
            self._refresh_duplicates(records, record)
            self._save(catalog)
        if resolution is not None:
            result = dict(image_id=digest, imported=True, source_added=True)
            try:
                self.schedule_processing()
            except Exception as exc:
                result['processing_error'] = str(exc)
            return result
        self.schedule_processing()
        return digest

    def schedule_processing(self):
        if self.auto_process:
            from core.uniform_processing import schedule
            return schedule(self)
        return None

    def process_pending(self, **kwargs):
        from core.uniform_processing import migrate
        return migrate(self, **kwargs)

    @staticmethod
    def _refresh_duplicates(records: list[dict], new_record: dict) -> None:
        try:
            a = int(new_record["dhash"], 16)
        except (TypeError, ValueError):
            return
        near = []
        for other in records:
            if other is new_record:
                continue
            try:
                if visually_similar(new_record, other):
                    near.append(other)
            except (TypeError, ValueError):
                continue
        new_record["duplicate_of"] = sorted(r["image_id"] for r in near)
        if near:
            new_record["flags"] = sorted(set(new_record.get("flags", [])) | {"near_duplicate_review"})
        for other in near:
            other["duplicate_of"] = sorted(set(other.get("duplicate_of", [])) | {new_record["image_id"]})
            other["flags"] = sorted(set(other.get("flags", [])) | {"near_duplicate_review"})

    def import_paths(self, paths: Iterable[str | Path], category: str, *, capture_group: str | None = None,
                     legacy_label: str | None = None, progress=None, cancel: threading.Event | None = None) -> list[str]:
        _validate_category(category)
        state = _ImportProgress(progress, cancel) if progress or cancel is not None else None
        ids, expanded, duplicates, processed, imported = [], 0, 0, 0, 0
        known_total = None
        found = 0
        # Spool discovery rather than retaining an arbitrarily large directory tree.
        # Both discovery and reading run in the caller's background import worker.
        with tempfile.SpooledTemporaryFile(mode="w+t", max_size=1024 * 1024, encoding="utf-8") as discovered:
            try:
                for path in paths:
                    if state and state.check():
                        return ImportSummary(unprocessed=None)
                    discovered.write(json.dumps(str(path)) + "\n")
                    found += 1
                    if state:
                        state.update(discovered=state.state["discovered"] + 1, current=Path(path).name)
                if state and state.check():
                    return ImportSummary(unprocessed=None)
                known_total = found
                discovered.seek(0)
                if state:
                    state.update(phase="importing", total=known_total, current=None)
                existing_ids = {r.get("image_id") for r in self.snapshot()["records"]}
                for index, line in enumerate(discovered):
                    if state and state.check(): break
                    path = Path(json.loads(line))
                    if state: state.update(current=path.name)
                    try:
                        if state and (path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
                                      or path.is_symlink()):
                            state.state["skipped"] += 1
                            processed += 1
                            state.update(processed=processed)
                            continue
                        if path.is_symlink() or not path.is_file():
                            raise ValueError(f"Not a regular image file: {path}")
                        size = path.stat().st_size
                        if size > MAX_FILE_BYTES:
                            raise ImportLimitError("per-file size", MAX_FILE_BYTES, imported=imported,
                                                   duplicates=duplicates, unprocessed=known_total-index,
                                                   measured_value=size, unit="bytes")
                        if index >= MAX_ZIP_FILES:
                            raise ImportLimitError("file count", MAX_ZIP_FILES, imported=imported,
                                                   duplicates=duplicates, unprocessed=known_total-index,
                                                   measured_value=index + 1)
                        if expanded + size > MAX_ZIP_EXPANDED:
                            raise ImportLimitError("total expanded size", MAX_ZIP_EXPANDED, imported=imported,
                                                   duplicates=duplicates, unprocessed=known_total-index,
                                                   measured_value=expanded + size, unit="bytes")
                        expanded += size
                        chunks, hasher = [], hashlib.sha256()
                        with path.open("rb") as stream:
                            remaining = MAX_FILE_BYTES + 1
                            while remaining:
                                if state and state.check(): break
                                block = stream.read(min(256 * 1024, remaining))
                                if not block: break
                                chunks.append(block)
                                hasher.update(block)
                                remaining -= len(block)
                                if state: state._emit()
                        if state and state.check(): break
                        data = b"".join(chunks)
                        if len(data) != size or len(data) > MAX_FILE_BYTES:
                            raise ValueError("Image changed while being read or exceeds the file size limit")
                        digest = hasher.hexdigest()
                        iid = self._import_bytes(data, str(path), "file", category, capture_group, legacy_label)
                    except ImportLimitError:
                        raise
                    except (ValueError, OSError):
                        if not state: raise
                        processed += 1
                        state.update(failed=state.state["failed"] + 1, processed=processed)
                        continue
                    ids.append(iid)
                    if digest in existing_ids: duplicates += 1
                    else: imported += 1
                    existing_ids.add(digest)
                    processed += 1
                    if state:
                        state.update(processed=processed, imported=imported, duplicates=duplicates,
                                     image_ids=list(dict.fromkeys(ids)))
                return ImportSummary(ids, imported=imported, duplicates=duplicates,
                                     unprocessed=max(0, known_total - processed))
            finally:
                if state:
                    state.update(processed=processed, imported=imported, duplicates=duplicates,
                                 image_ids=list(dict.fromkeys(ids)))
                    state._emit(force=True)

    def import_zip(self, path: str | Path, category: str, *, capture_group: str | None = None,
                   progress=None, cancel: threading.Event | None = None) -> list[str]:
        _validate_category(category)
        state = _ImportProgress(progress, cancel) if progress or cancel is not None else None
        ids, duplicates, imported, processed = [], 0, 0, 0
        known_total = None
        try:
            path = Path(path)
            if path.is_symlink() or not path.is_file():
                raise ValueError("ZIP must be a regular file")
            if state: state.update(current=path.name)
            size = path.stat().st_size
            if size > MAX_ZIP_ARCHIVE_BYTES:
                raise ImportLimitError("compressed archive size", MAX_ZIP_ARCHIVE_BYTES, imported=0,
                                       duplicates=0, unprocessed=None, measured_value=size, unit="bytes")
            archive_hash, compressed_read = hashlib.sha256(), 0
            with path.open("rb") as source:
                while True:
                    if state and state.check(): return ImportSummary(unprocessed=None)
                    chunk = source.read(256 * 1024)
                    if not chunk: break
                    compressed_read += len(chunk)
                    if compressed_read > MAX_ZIP_ARCHIVE_BYTES:
                        raise ImportLimitError("compressed archive size", MAX_ZIP_ARCHIVE_BYTES, imported=0,
                                               duplicates=0, unprocessed=None,
                                               measured_value=compressed_read, unit="bytes")
                    archive_hash.update(chunk)
                    if state: state._emit()
            archive_digest = archive_hash.hexdigest()
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                files = []
                for info in infos:
                    if state and state.check(): return ImportSummary(unprocessed=None)
                    if not info.is_dir():
                        files.append(info)
                        if state: state.update(discovered=len(files), current=info.filename)
                known_total = len(files)
                if state: state.update(total=known_total)
                # Keep the existing complete-archive safety preflight before any writes.
                supported_count = sum(_is_supported_zip_image(row) for row in files)
                if len(infos) > MAX_ZIP_FILES:
                    raise ImportLimitError("archive entry count", MAX_ZIP_FILES, imported=0, duplicates=0,
                                           unprocessed=supported_count, measured_value=len(infos), unit="entries")
                expanded = sum(info.file_size for info in infos)
                if expanded > MAX_ZIP_EXPANDED:
                    raise ImportLimitError("archive expanded size", MAX_ZIP_EXPANDED, imported=0, duplicates=0,
                                           unprocessed=supported_count, measured_value=expanded, unit="bytes")
                for info in infos:
                    if state and state.check(): return ImportSummary(unprocessed=known_total)
                    name = PurePosixPath(info.filename)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if (name.is_absolute() or ".." in name.parts or "\\" in info.filename
                            or re.match(r"^[a-zA-Z]:", info.filename)):
                        raise ValueError("ZIP contains an unsafe path")
                    if stat.S_ISLNK(mode): raise ValueError("ZIP contains a symlink")
                    if info.flag_bits & 1: raise ValueError("Encrypted ZIP entries are not supported")
                    if info.file_size > MAX_FILE_BYTES:
                        raise ImportLimitError("per-file size", MAX_FILE_BYTES, imported=0, duplicates=0,
                                               unprocessed=supported_count, measured_value=info.file_size, unit="bytes")
                    if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                        raise ImportLimitError("compression ratio", MAX_COMPRESSION_RATIO, imported=0, duplicates=0,
                                               unprocessed=supported_count,
                                               measured_value=info.file_size // max(info.compress_size, 1), unit="ratio")
                if state: state.update(phase="importing", current=None)
                existing_ids = {r.get("image_id") for r in self.snapshot()["records"]}
                for info in files:
                    if state and state.check(): break
                    if state: state.update(current=info.filename)
                    if not _is_supported_zip_image(info):
                        processed += 1
                        if state: state.update(skipped=state.state["skipped"] + 1, processed=processed)
                        continue
                    try:
                        chunks, hasher, read = [], hashlib.sha256(), 0
                        with archive.open(info) as member:
                            while read <= MAX_FILE_BYTES:
                                if state and state.check(): break
                                block = member.read(min(256 * 1024, MAX_FILE_BYTES + 1 - read))
                                if not block: break
                                chunks.append(block)
                                hasher.update(block)
                                read += len(block)
                                if state: state._emit()
                        if state and state.check(): break
                        data = b"".join(chunks)
                        if len(data) > MAX_FILE_BYTES or len(data) != info.file_size:
                            raise ValueError("ZIP image exceeds bounds or has inconsistent size")
                        digest = hasher.hexdigest()
                        iid = self._import_bytes(data, f"{path}!/{info.filename}", "zip", category, capture_group,
                                                 archive_sha256=archive_digest)
                    except (ValueError, OSError, zipfile.BadZipFile):
                        if not state: raise
                        processed += 1
                        state.update(failed=state.state["failed"] + 1, processed=processed)
                        continue
                    ids.append(iid)
                    if digest in existing_ids: duplicates += 1
                    else: imported += 1
                    existing_ids.add(digest)
                    processed += 1
                    if state:
                        state.update(processed=processed, imported=imported, duplicates=duplicates,
                                     image_ids=list(dict.fromkeys(ids)))
            return ImportSummary(ids, imported=imported, duplicates=duplicates,
                                 unprocessed=max(0, known_total - processed))
        finally:
            if state:
                state.update(processed=processed, imported=imported, duplicates=duplicates,
                             image_ids=list(dict.fromkeys(ids)))
                state._emit(force=True)

    def import_folder(self, path: str | Path, category: str, *, capture_group: str | None = None,
                      progress=None, cancel: threading.Event | None = None) -> list[str]:
        folder = Path(path)
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Folder must be a regular directory")
        def walk(directory):
            # Streaming traversal; no rglob/sort of the complete tree on the Tk thread.
            with os.scandir(directory) as entries:
                for entry in entries:
                    if cancel and cancel.is_set(): return
                    child = Path(entry.path)
                    if entry.is_symlink():
                        if progress: yield child  # Count the skipped occurrence, never follow it.
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name != "__MACOSX": yield from walk(child)
                    elif entry.is_file(follow_symlinks=False):
                        if progress or child.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
                            yield child
        return self.import_paths(walk(folder), category, capture_group=capture_group,
                                 progress=progress, cancel=cancel)

    def import_capture(self, frame_bgr: np.ndarray, category: str, capture_group: str) -> str:
        _validate_category(category)
        if not isinstance(frame_bgr, np.ndarray) or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.size == 0:
            raise ValueError("Capture must be a non-empty BGR image")
        h, w = frame_bgr.shape[:2]
        if h > MAX_DIMENSION or w > MAX_DIMENSION or h * w > MAX_IMAGE_PIXELS:
            raise ValueError("Capture dimensions exceed supported bounds")
        ok, encoded = cv2.imencode(".png", frame_bgr)
        if not ok:
            raise ValueError("Capture could not be encoded")
        return self._import_bytes(encoded.tobytes(), f"capture:{_now()}", "capture", category, capture_group)

    def audit_legacy(self, root: str | Path | None = None) -> dict:
        legacy_root = Path(root) if root is not None else TRAIN_DIR / "uniform"
        imported = 0
        found = 0
        if not legacy_root.exists():
            return {"found": 0, "imported": 0, "root": str(legacy_root)}
        existing_ids = {r["image_id"] for r in self.snapshot()["records"]}
        for label_dir in sorted(legacy_root.iterdir()):
            if not label_dir.is_dir() or label_dir.is_symlink():
                continue
            for p in sorted(label_dir.iterdir()):
                if p.is_symlink() or not p.is_file() or p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
                    continue
                found += 1
                image_id = self.import_paths([p], "unassigned", legacy_label=label_dir.name)[0]
                if image_id not in existing_ids:
                    imported += 1
                    existing_ids.add(image_id)
        return {"found": found, "imported": imported, "root": str(legacy_root)}

    def snapshot(self) -> dict:
        with self._locked():
            catalog = self._load()
        records = catalog["records"]
        by_category = {key: sum(r.get("category") == key for r in records) for key in CATEGORIES}
        by_status = {key: sum(r.get("status") == key for r in records) for key in ("pending_review", "approved", "excluded")}
        approved_by_category = {key: sum(r.get("status") == "approved" and r.get("category") == key
                                          for r in records) for key in CATEGORIES}
        near_pairs = {(min(r["image_id"], other), max(r["image_id"], other)) for r in records for other in r.get("duplicate_of", [])}
        exact = sum(max(0, len(r.get("sources", [])) - 1) for r in records)
        # Reproducible group-aware splits are frozen by CandidateService.
        proposed = None
        from core.uniform_candidate import NORMAL_TRAINING_MIN_PER_CLASS, eligible_training_records
        eligible_rows = eligible_training_records(records)
        from core.uniform_processing import STATES, duplicate_groups
        duplicate_components = duplicate_groups(records)
        processing_counts = {state: sum(r.get("category") in GARMENT_CATEGORIES and r.get("processing_state", "queued") == state for r in records)
                             for state in STATES}
        eligibility_reasons = []
        if any(r.get("category") == "unassigned" for r in records):
            eligibility_reasons.append("Unassigned legacy records require an established garment category.")
        if any(r.get("status") == "excluded" for r in records):
            eligibility_reasons.append("Intentionally excluded records remain outside training.")
        eligible_counts = {c: sum(r['category'] == c for r in eligible_rows)
                           for c in CATEGORIES if c != 'unassigned'}
        missing_categories = [CATEGORIES[c] for c, count in eligible_counts.items()
                              if count < NORMAL_TRAINING_MIN_PER_CLASS]
        if missing_categories:
            eligibility_reasons.append(f'Fewer than {NORMAL_TRAINING_MIN_PER_CLASS} eligible unique photos for: '
                                       + ', '.join(missing_categories) + '.')
        eligibility_reasons.append('Category coverage is a minimum experimental input check, not evidence of accuracy.')
        experimental_training_eligible = not missing_categories
        if not experimental_training_eligible and not eligibility_reasons:
            eligibility_reasons.append("No processed, assigned, unambiguous records are available.")
        result = {
            "schema_version": 1, "preprocess_version": PREPROCESS_VERSION, "categories": dict(CATEGORIES),
            "records": records,
            "counts": {"total": len(records), "by_category": by_category, "by_status": by_status,
                       "approved_by_category": approved_by_category, "eligible_by_category": eligible_counts,
                       "exact_duplicate_occurrences": exact, "near_duplicate_pairs": len(near_pairs),
                       "processing": processing_counts,
                       "successfully_processed": processing_counts["training_ready"] + processing_counts["quarantined"],
                       "near_duplicate_groups": sum(len(rows) > 1 for rows in duplicate_components.values()),
                       "unknown_provenance": sum(r.get("has_unknown_provenance", not r.get("capture_group")) for r in records)},
            "experimental_training_eligible": experimental_training_eligible,
            "readiness": {"review_complete": bool(records) and all(r.get("status") != "pending_review" for r in records),
                          "experimental_training_eligible": experimental_training_eligible,
                          "experimental_training_reasons": eligibility_reasons,
                          "group_safe_splits_available": proposed is not None,
                          "independent_evaluation_ready": False, "model_activation_allowed": False},
            "proposed_splits": proposed,
        }
        result["logo"] = logo_readiness(records)
        return result

    def approve_all_pending(self) -> dict:
        """Atomically administratively approve pending garment records.

        This records acceptance without altering crop, flags, labels, provenance,
        duplicate relationships, or logo annotations. Eligibility remains the
        candidate trainer's separate decision.
        """
        with self._locked():
            catalog = self._load()
            records = catalog["records"]
            ids = [r.get("image_id") for r in records]
            if len(ids) != len(set(ids)):
                raise ValueError("Catalog contains ambiguous duplicate image IDs; no records were changed.")
            eligible = [r for r in records if r.get("status") == "approved" and r.get("category") in GARMENT_CATEGORIES]
            pending = [r for r in records if r.get("status") == "pending_review" and r.get("category") in GARMENT_CATEGORIES]
            from core.uniform_candidate import is_training_eligible
            prospective = [dict(r, status="approved") for r in pending]
            currently_ineligible = sum(not is_training_eligible(r) for r in eligible)
            projected_ineligible = currently_ineligible + sum(not is_training_eligible(r) for r in prospective)
            total = len(pending)
            if not total:
                return {"total_pending": 0, "approved": 0, "already_approved": len(eligible),
                        "failed": 0, "not_training_eligible": currently_ineligible,
                        "remaining_pending": 0}
            now = _review_now()
            for record in pending:
                record["status"] = "approved"
                record.setdefault("review_history", []).append({
                    "at": now, "action": "bulk_administrative_approval",
                    "prior_status": "pending_review", "status": "approved",
                    "category": copy.deepcopy(record.get("category")),
                    "crop": copy.deepcopy(record.get("crop")),
                    "flags": copy.deepcopy(record.get("flags", [])),
                })
            try:
                self._save(catalog)
            except Exception as exc:
                result = {"total_pending": total, "approved": 0, "already_approved": len(eligible),
                          "failed": total, "not_training_eligible": currently_ineligible,
                          "remaining_pending": total}
                raise BulkApprovalError(f"Bulk approval save failed; no photos were approved: {exc}", result) from exc
            return {"total_pending": total, "approved": total, "already_approved": len(eligible),
                    "failed": 0, "not_training_eligible": projected_ineligible,
                    "remaining_pending": 0}

    def review(self, image_id: str, *, category: str, crop: list[int], status: str,
               logo_visibility: str = "unknown", logo_box: list[int] | None = None,
               flags: list[str] | None = None, notes: str = "",
               crop_source: str = "manual", logo_reviewed: bool = False,
               logo_source_kind: str = "uncertain",
               logo_association_confirmed: bool = False, unrelated_image_ids: list[str] | None = None) -> dict:
        _validate_category(category)
        if status not in {"pending_review", "approved", "excluded"}:
            raise ValueError("Invalid review status")
        if logo_visibility not in {"unknown", "visible", "occluded", "not_visible",
                                   "obscured", "too_small", "back_view", "uncertain"}:
            raise ValueError("Invalid logo visibility")
        if crop_source not in {"manual", "detector"}:
            raise ValueError("Crop source must be manual or detector")
        if type(logo_reviewed) is not bool or type(logo_association_confirmed) is not bool:
            raise ValueError("Logo review and association confirmation must be explicit booleans")
        if logo_source_kind not in {"photographed_print", "reference_artwork", "uncertain"}:
            raise ValueError("Invalid logo source kind")
        with self._locked():
            catalog = self._load()
            record = next((r for r in catalog["records"] if r["image_id"] == image_id), None)
            if record is None:
                raise KeyError(image_id)
            if not isinstance(crop, (list, tuple)) or len(crop) != 4 or any(type(x) is not int for x in crop):
                raise ValueError("Crop must be four integer raster coordinates")
            x1, y1, x2, y2 = crop
            if x1 < 0 or y1 < 0 or x2 > record["width"] or y2 > record["height"] or x2 - x1 < 16 or y2 - y1 < 16:
                raise ValueError("Crop is outside the original image or too small")
            if logo_box is not None:
                if not isinstance(logo_box, (list, tuple)) or len(logo_box) != 4 or any(type(x) is not int for x in logo_box):
                    raise ValueError("Logo box must be four integer raster coordinates")
                lx1, ly1, lx2, ly2 = logo_box
                if lx1 < x1 or ly1 < y1 or lx2 > x2 or ly2 > y2 or lx2 <= lx1 or ly2 <= ly1:
                    raise ValueError("Logo box must be inside the reviewed crop")
            if status == "approved" and category == "unassigned":
                raise ValueError("Unassigned records cannot be approved")
            prior_annotation = record.get("logo_annotation") or {}
            if crop_source == "detector" and not any(p.get("box") == list(crop)
                                                        for p in record.get("crop_proposal_history", [])):
                raise ValueError("Detector crop must match a stored detector proposal")
            if status == "approved" and (category not in CATEGORIES or not crop):
                raise ValueError("Approval requires an explicit category and crop")
            canonical_visibility = normalize_visibility(logo_visibility)
            # An omitted/default uncertain value does not erase a known source
            # draft. Correcting artwork requires an explicit confirmed re-review.
            effective_source_kind = logo_source_kind
            if (logo_source_kind == "uncertain" and not logo_reviewed
                    and prior_annotation.get("source_kind") in {"photographed_print", "reference_artwork"}):
                effective_source_kind = prior_annotation["source_kind"]
            if (prior_annotation.get("source_kind") == "reference_artwork"
                    and not (logo_reviewed and logo_source_kind == "photographed_print"
                             and logo_association_confirmed)):
                # A pending unconfirmed source edit must not wash away artwork
                # provenance before a later default approval.
                effective_source_kind = "reference_artwork"
            if status == "approved" and (
                effective_source_kind == "reference_artwork"
                or (prior_annotation.get("source_kind") == "reference_artwork"
                    and not (logo_reviewed and logo_source_kind == "photographed_print"
                             and logo_association_confirmed))
            ):
                raise ValueError("Reference artwork cannot be approved as a training garment")
            if logo_reviewed:
                if not logo_association_confirmed or effective_source_kind == "uncertain":
                    raise ValueError("Reviewed logo annotations require a known source and confirmed torso association")
                if canonical_visibility == "visible":
                    if effective_source_kind != "photographed_print":
                        raise ValueError("Visible logos must be photographed prints, not reference artwork")
                    if logo_box is None:
                        raise ValueError("A visible logo annotation requires a box inside the reviewed torso")
                if status == "approved" and effective_source_kind == "reference_artwork":
                    raise ValueError("Reference artwork cannot be approved as a training garment")
            reviewed_at = _review_now()
            new = {"category": category, "status": status, "crop": list(crop),
                   "logo_visibility": logo_visibility,
                   "logo_box": list(logo_box) if logo_box else None,
                   "flags": list(dict.fromkeys(flags or [])),
                   "notes": str(notes),
                   "preprocess_version": PREPROCESS_VERSION, "crop_source": crop_source}
            record.update(new)
            record["preprocess_version"] = PREPROCESS_VERSION
            record["crop_source"] = crop_source
            annotate_logo = (logo_reviewed or logo_source_kind != "uncertain"
                             or prior_annotation.get("schema") == "printed-logo-v1")
            history_entry = {"at": reviewed_at, **new}
            if annotate_logo:
                annotation = {
                    "schema": "printed-logo-v1",
                    "reviewed": logo_reviewed,
                    "visibility": canonical_visibility,
                    "box": list(logo_box) if logo_box else None,
                    "source_kind": effective_source_kind,
                    "association_confirmed": logo_association_confirmed,
                    "image_sha256": record["sha256"],
                    "torso_crop": list(crop),
                    "category": category,
                    "preprocess_version": PREPROCESS_VERSION,
                    "provenance": {
                        "sources": copy.deepcopy(record.get("sources", [])),
                        "capture_group": record.get("capture_group"),
                        "capture_groups": copy.deepcopy(record.get("capture_groups", [])),
                        "provenance_group": record.get("provenance_group"),
                        "has_unknown_provenance": record.get("has_unknown_provenance", True),
                    },
                    "reviewed_at": reviewed_at,
                }
                record["logo_annotation"] = annotation
                history_entry["logo_annotation"] = copy.deepcopy(annotation)
            record["review_history"].append(history_entry)
            for other_id in unrelated_image_ids or []:
                other = next((r for r in catalog['records'] if r['image_id'] == other_id), None)
                if other is None or other_id == image_id:
                    raise ValueError('Invalid related photo correction')
                for row, target in ((record, other_id), (other, image_id)):
                    row['unrelated_image_ids'] = sorted(set(row.get('unrelated_image_ids', [])) | {target})
                    row['duplicate_of'] = [i for i in row.get('duplicate_of', []) if i != target]
                    if not row['duplicate_of']:
                        row['flags'] = [f for f in row.get('flags', []) if f != 'near_duplicate_review']
            if unrelated_image_ids:
                from core.uniform_processing import duplicate_groups
                duplicate_groups(catalog['records'])
            record.update(processing_state="queued", training_eligible=False, eligibility_reasons=["correction_awaiting_processing"])
            self._save(catalog)
            result = dict(record)
        self.schedule_processing()
        return result

    def image(self, image_id: str, crop: bool = False) -> Image.Image:
        with self._locked():
            catalog = self._load()
            record = next((r for r in catalog["records"] if r["image_id"] == image_id), None)
        if record is None:
            raise KeyError(image_id)
        path = self.root / record["original_path"]
        with Image.open(path) as im:
            result = im.convert("RGB")
        if crop:
            box = record.get("crop")
            if not box:
                raise ValueError("Image has no reviewed crop")
            return result.crop(tuple(box))
        return result

    def logo_image(self, image_id: str) -> Image.Image:
        """Return the native-resolution printed-logo crop for a current review."""
        with self._locked():
            catalog = self._load()
            record = next((r for r in catalog["records"] if r["image_id"] == image_id), None)
        if record is None:
            raise KeyError(image_id)
        annotation = record.get("logo_annotation") or {}
        if not is_eligible_visible_annotation(record):
            raise ValueError("No current eligible reviewed photographed-logo annotation")
        path = self.root / record["original_path"]
        try:
            path.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError("Original image path escapes the dataset root") from exc
        if path.is_symlink():
            raise ValueError("Original image path must not be a symlink")
        try:
            with path.open('rb') as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise ValueError("Original image is unavailable") from exc
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("Original image exceeds the reviewed image size limit")
        if hashlib.sha256(data).hexdigest() != record.get("sha256") or annotation.get("image_sha256") != record.get("sha256"):
            raise ValueError("Original image hash does not match the reviewed annotation")
        with Image.open(io.BytesIO(data)) as im:
            if im.size != (record['width'], record['height']):
                raise ValueError("Original dimensions do not match the reviewed annotation")
            image = im.convert("RGB")
        return image.crop(tuple(annotation["box"]))

    def propose_crop(self, image_id: str) -> list[int] | None:
        """Try local PersonDetector weights and persist a non-approving proposal."""
        with self._locked():
            catalog = self._load()
            record = next((r for r in catalog["records"] if r["image_id"] == image_id), None)
            if record is None:
                raise KeyError(image_id)
        proposal = {"at": _now(), "source": "detector", "preprocess_version": PREPROCESS_VERSION,
                    "box": None, "reason": None, "person_count": 0}
        try:
            from core.person_detector import PersonDetector
            rgb = np.asarray(self.image(image_id))
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            detector = PersonDetector(backend="cpu")
            persons = detector.detect_persons(bgr)
            if not persons:
                reason = detector.last_error or "No person was detected in the image."
                proposal["reason"] = reason
                raise ValueError(reason)
            proposal["person_count"] = len(persons)
            if len(persons) != 1:
                proposal["association_uncertain"] = True
                proposal["reason"] = "Multiple people: correct subject cannot be established automatically."
                raise ValueError(proposal["reason"])
            box = detector.pose_torso_box(bgr, persons[0]) if detector.use_pose else None
            if box is None:
                # Only local detector geometry; no guessed or generated detection.
                box = detector.get_torso_box(persons[0])
            x1, y1, x2, y2 = [int(v) for v in box]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(record["width"], x2), min(record["height"], y2)
            if x2 - x1 < 16 or y2 - y1 < 16:
                reason = "Detected torso box is outside the image or too small."
                proposal["reason"] = reason
                raise ValueError(reason)
            result = [x1, y1, x2, y2]
            proposal["box"] = result
            if len(persons) > 1:
                proposal["association_uncertain"] = True
            self._store_crop_proposal(image_id, proposal)
            return result
        except ValueError:
            if proposal["reason"] is None:
                proposal["reason"] = "Detector could not propose a crop."
            self._store_crop_proposal(image_id, proposal)
            raise
        except Exception as exc:
            reason = f"Detector crop proposal failed: {type(exc).__name__}: {exc}"
            proposal["reason"] = reason
            self._store_crop_proposal(image_id, proposal)
            raise ValueError(reason) from exc

    def _store_crop_proposal(self, image_id: str, proposal: dict) -> None:
        with self._locked():
            catalog = self._load()
            record = next((r for r in catalog["records"] if r["image_id"] == image_id), None)
            if record is None:
                raise KeyError(image_id)
            history = record.setdefault("crop_proposal_history", [])
            history.append(dict(proposal))
            record["crop_proposal"] = dict(proposal)
            if proposal.get("association_uncertain"):
                record["flags"] = sorted(set(record.get("flags", [])) |
                                          {"person_association_uncertain", "multiple_people"})
            self._save(catalog)


class _CatalogLock:
    def __init__(self, thread_lock, path):
        self.thread_lock, self.path, self.file = thread_lock, path, None
    def __enter__(self):
        self.thread_lock.acquire()
        try:
            self.file = self.path.open("a+")
            from core.file_lock import lock_exclusive
            lock_exclusive(self.file)
        except Exception:
            if self.file:
                self.file.close()
                self.file = None
            self.thread_lock.release()
            raise
        return self
    def __exit__(self, *_):
        try:
            if self.file:
                try:
                    from core.file_lock import unlock
                    unlock(self.file)
                finally:
                    self.file.close()
        finally:
            self.thread_lock.release()
