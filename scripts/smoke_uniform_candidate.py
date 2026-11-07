#!/usr/bin/env python3
"""Opt-in disposable CPU smoke for the isolated uniform candidate service.

Creates a retained run under data/uniform_candidate_smoke/<uuid>; trains only
synthetic fixtures, requests cancellation after an epoch, resumes, verifies the
bundle and confirms all Stage 1-audited artifacts retain their original hashes.
Run only when explicitly authorized by the project lead.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from core.uniform_candidate import CandidateService
from core.uniform_dataset import UniformDataset

REPO = Path(__file__).resolve().parents[1]
AUDIT = REPO / "output" / "uniform-stage1" / "final-audit.json"
RESULTS = REPO / "output" / "uniform-stage2" / "smoke-results.json"


def file_fingerprint(path: Path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def persist(result: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    temp = RESULTS.with_suffix(".json.tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, RESULTS)


def verified_bundle(run_dir: Path) -> dict:
    checksums = json.loads((run_dir / "bundle_checksums.json").read_text("utf-8"))
    for relative, expected in checksums.items():
        path = run_dir / relative
        if not path.is_file() or file_fingerprint(path) != expected:
            raise RuntimeError(f"Bundle checksum mismatch: {relative}")
    pointer = json.loads((run_dir / "last_checkpoint.json").read_text("utf-8"))
    checkpoint = run_dir / pointer["file"]
    sidecar = json.loads((run_dir / f"{pointer['file']}.sha256.json").read_text("utf-8"))
    if (file_fingerprint(checkpoint) != pointer["sha256"] or sidecar.get("sha256") != pointer["sha256"]
            or sidecar.get("epoch") != pointer.get("epoch")):
        raise RuntimeError("Final checkpoint pointer/sidecar checksum mismatch")
    class_map = json.loads((run_dir / "class_map.json").read_text("utf-8"))
    if class_map != {"mapping_version": "uniform-garments-v1", "classes": [
            {"index": 0, "category": "campus_uniform"},
            {"index": 1, "category": "organizational_shirt"},
            {"index": 2, "category": "other_clothing"}]}:
        raise RuntimeError("Candidate class map is incorrect")
    architecture = json.loads((run_dir / "architecture.json").read_text("utf-8"))
    if (architecture.get("scale") != "n" or architecture.get("yaml_file") != "yolov8n-cls.yaml"
            or architecture.get("trainable_backbone_parameter_count", 0) <= 0
            or architecture.get("trainable_head_parameter_count", 0) <= 0):
        raise RuntimeError(f"Candidate architecture/trainable parameter check failed: {architecture}")
    manifest = json.loads((run_dir / "dataset_manifest.json").read_text("utf-8"))
    if manifest.get("model_preprocess") != {"version": "trainer-letterbox-rgb-0-1-v1", "size": 224,
                                              "color": "RGB", "range": [0.0, 1.0], "augmentation": "none"}:
        raise RuntimeError("Frozen candidate preprocessing record differs from expected letterbox RGB [0,1]")
    if any(entry.get("split") != "train" for entry in manifest.get("entries", [])):
        raise RuntimeError("Synthetic smoke bundle contains a non-train split")
    config = json.loads((run_dir / "config.json").read_text("utf-8"))
    if not config.get("nondeployable"):
        raise RuntimeError("Smoke candidate is not marked nondeployable")
    if not (run_dir / "candidate.pt").is_file() or not (run_dir / "limitations.txt").is_file():
        raise RuntimeError("Candidate output or limitations record is missing")
    return {"bundle_files_verified": len(checksums), "candidate_sha256": file_fingerprint(run_dir / "candidate.pt"),
            "checkpoint_sha256": pointer["sha256"], "architecture": architecture,
            "class_map": class_map, "preprocessing": manifest["model_preprocess"]}


def main() -> int:
    if not AUDIT.is_file():
        raise FileNotFoundError(f"Stage 1 protected-artifact audit is missing: {AUDIT}")
    audit = json.loads(AUDIT.read_text("utf-8"))
    expected_hashes = audit.get("model_artifacts_sha256", {})
    if len(expected_hashes) != 7:
        raise RuntimeError("Expected seven audited protected artifact hashes")
    before = {relative: file_fingerprint(REPO / relative) for relative in expected_hashes}
    if before != expected_hashes:
        raise RuntimeError("Protected model artifact fingerprints do not match Stage 1 audit before smoke")

    smoke_root = REPO / "data" / "uniform_candidate_smoke" / uuid.uuid4().hex
    smoke_root.mkdir(parents=True, mode=0o700)
    os.chmod(smoke_root, 0o700)
    result = {"run_id": None, "status": "preparing", "created_at": time.time(),
              "smoke_root": str(smoke_root), "bundle": None, "stages": [],
              "process_metadata": {}, "protected_artifact_sha256_before": before,
              "protected_artifact_sha256_after": None, "failure": None}
    persist(result)
    service = None
    run_id = None

    def stage(name: str, **details):
        result["stages"].append({"name": name, "at": time.time(), **details})
        persist(result)

    try:
        dataset = UniformDataset(smoke_root / "stage1")
        categories = ("campus_uniform", "organizational_shirt", "other_clothing")
        for class_index, category in enumerate(categories):
            for sample_index in range(2):
                rng = np.random.default_rng(1000 + class_index * 10 + sample_index)
                pixels = rng.integers(0, 256, size=(80, 96, 3), dtype=np.uint8)
                fixture = smoke_root / f"fixture-{class_index}-{sample_index}.png"
                Image.fromarray(pixels).save(fixture, format="PNG")
                image_id = dataset.import_paths([fixture], category, capture_group=f"smoke-camera-{class_index}")[0]
                catalog = dataset._load()
                record = next(row for row in catalog["records"] if row["image_id"] == image_id)
                record["sources"][0]["kind"] = "synthetic_smoke"
                dataset._save(catalog)
                dataset.review(image_id, category=category,
                               crop=[0, 0, record["width"], record["height"]], status="approved", flags=[])
        stage("synthetic_dataset_ready", records=6)

        service = CandidateService(dataset, candidates_root=smoke_root / "candidates",
                                   base_weights=REPO / "yolov8n-cls.pt")
        summary = service.summary(min_per_class=2)
        if not summary["eligible"]:
            raise RuntimeError(f"Synthetic fixture did not pass eligibility: {summary['reasons']}")
        run_id = service.start({"epochs": 10, "batch": 2, "threads": 1,
                                "min_per_class": 2, "synthetic_smoke": True})
        run_dir = Path(service.candidates_root) / run_id
        result.update(run_id=run_id, bundle=str(run_dir), status="running")
        persist(result)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status = service.status(run_id)
            result["process_metadata"] = {k: status.get(k) for k in ("process_id", "run_uuid", "started_at", "worker_started_at")}
            result["last_status"] = status
            persist(result)
            if status.get("status") == "running" and status.get("epoch", 0) >= 1:
                stage("first_epoch_complete", status=status)
                service.cancel(run_id)
                stage("cancel_requested", status=service.status(run_id))
                break
            if status.get("status") in {"failed", "completed", "cancelled"}:
                raise RuntimeError(f"Unexpected first phase status: {status}")
            time.sleep(0.01)
        else:
            raise TimeoutError("Candidate did not reach its first completed epoch")
        while time.monotonic() < deadline:
            status = service.status(run_id)
            result["last_status"] = status
            persist(result)
            if status.get("status") == "cancelled":
                stage("cancelled_at_checkpoint", status=status)
                break
            if status.get("status") in {"failed", "completed"}:
                raise RuntimeError(f"Unexpected cancellation status: {status}")
            time.sleep(0.01)
        else:
            raise TimeoutError("Worker did not honor cancellation")

        # Terminal status is written before the child releases its OS lock.
        while service.is_busy() and time.monotonic() < deadline:
            time.sleep(0.05)
        if service.is_busy():
            raise TimeoutError("Cancelled worker did not release its training lock")

        # Recreate the controller as a reopened UI/process would; the immutable
        # bundle and persistent status/checkpoint must be sufficient to recover.
        service = CandidateService(dataset, candidates_root=smoke_root / "candidates",
                                  base_weights=REPO / "yolov8n-cls.pt")
        service.resume(run_id)
        stage("resumed", status=service.status(run_id))
        while time.monotonic() < deadline:
            status = service.status(run_id)
            result["last_status"] = status
            result["process_metadata"] = {k: status.get(k) for k in ("process_id", "run_uuid", "started_at", "worker_started_at")}
            persist(result)
            if status.get("status") == "completed":
                break
            if status.get("status") in {"failed", "cancelled"}:
                raise RuntimeError(f"Unexpected resumed status: {status}")
            time.sleep(0.01)
        else:
            raise TimeoutError("Resumed candidate did not finish")

        service._verify_bundle(run_dir)
        bundle_check = verified_bundle(run_dir)
        after = {relative: file_fingerprint(REPO / relative) for relative in expected_hashes}
        if before != after:
            raise RuntimeError("A protected Stage 1-audited artifact changed during isolated smoke")
        result.update(status="completed", verification=bundle_check,
                      protected_artifact_sha256_after=after, finished_at=time.time())
        stage("smoke_complete", result_status="completed")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        result.update(status="failed", failure=f"{type(exc).__name__}: {exc}", finished_at=time.time())
        if run_id and service is not None:
            try:
                current = service.status(run_id)
                if current.get("status") in {"preparing", "running", "cancel_requested"}:
                    service.cancel(run_id)
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        current = service.status(run_id)
                        result["last_status"] = current
                        if current.get("status") not in {"preparing", "running", "cancel_requested"}:
                            break
                        time.sleep(0.01)
                    if current.get("status") in {"preparing", "running", "cancel_requested"}:
                        result["cancel_cleanup_timeout"] = True
            except Exception as cancel_error:
                result["cancel_cleanup_error"] = f"{type(cancel_error).__name__}: {cancel_error}"
        persist(result)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
