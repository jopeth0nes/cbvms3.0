"""Child process for CPU-only experimental uniform candidate training."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import random
import re
import sys
import tempfile
import traceback
from pathlib import Path
from datetime import datetime, timezone


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, sort_keys=True, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError(f"Candidate metadata cannot be a symlink: {path.name}")
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {path.name}")
    return value


def _status(run_dir: Path, **changes) -> dict:
    path = run_dir / "status.json"
    value = _read(path)
    value.update(changes, updated_at=_utc())
    _atomic_json(path, value)
    return value


def _verify(run_dir: Path, config: dict, manifest: dict) -> None:
    if _sha256(run_dir / "dataset_manifest.json") != config["dataset_manifest_sha256"]:
        raise RuntimeError("Frozen dataset manifest hash mismatch")
    if config.get("mapping_version") != "uniform-garments-v1":
        raise RuntimeError("Class mapping version changed")
    expected_class_map = {"mapping_version": "uniform-garments-v1", "classes": [
        {"index": 0, "category": "campus_uniform"},
        {"index": 1, "category": "organizational_shirt"},
        {"index": 2, "category": "other_clothing"}]}
    if _read(run_dir / "class_map.json") != expected_class_map:
        raise RuntimeError("Class map/index order mismatch")
    entries = manifest.get("entries", [])
    if not entries:
        raise RuntimeError("Frozen manifest is empty")
    base_name = config.get("base_weights_file", "")
    base_rel = Path(base_name)
    if base_rel.is_absolute() or ".." in base_rel.parts:
        raise RuntimeError("Base weight path escapes the candidate bundle")
    immutable = _read(run_dir / "immutable_checksums.json")
    expected_paths = {"base_weights.pt", "class_map.json", "dataset_manifest.json", "config.json"}
    expected_paths.update(entry.get("prepared_path") for entry in entries)
    if set(immutable) != expected_paths:
        raise RuntimeError("Immutable checksum inventory is incomplete")
    for relative, expected in immutable.items():
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(f"Unsafe frozen input path: {relative}")
        path = run_dir / relative_path
        cursor = run_dir
        for component in relative_path.parts:
            cursor = cursor / component
            if cursor.is_symlink():
                raise RuntimeError(f"Frozen input path contains a symlink: {relative}")
        resolved = path.resolve()
        if path.is_symlink() or run_dir.resolve() not in resolved.parents or not resolved.is_file() or _sha256(resolved) != expected:
            raise RuntimeError(f"Frozen input integrity check failed: {relative}")
    if manifest.get("mapping_version") != config.get("mapping_version"):
        raise RuntimeError("Class mapping changed during candidate run")


def _atomic_torch_save(torch, value, path: Path) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        torch.save(value, name)
        with open(name, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _checkpoint(torch, random, np, model, optimizer, epoch, run_dir, metadata):
    path = run_dir / f"checkpoint_epoch_{epoch:06d}.pt"
    _atomic_torch_save(torch, {
        **metadata, "epoch": epoch, "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(), "torch_rng_state": torch.get_rng_state(),
        "python_rng_state": random.getstate(), "numpy_rng_state": np.random.get_state(),
    }, path)
    digest = _sha256(path)
    sidecar = {**metadata, "epoch": epoch, "sha256": digest}
    _atomic_json(run_dir / f"{path.name}.sha256.json", sidecar)
    _atomic_json(run_dir / "last_checkpoint.json", {"file": path.name, "epoch": epoch, "sha256": digest})


def _cancelled(run_dir: Path) -> bool:
    return (run_dir / "cancel.flag").exists()


def _bundle_checksums(run_dir: Path) -> dict:
    files = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name in {"bundle_checksums.json", "train.log", "status.json", "cancel.flag"}:
            continue
        if path.name.endswith(".tmp"):
            continue
        files[str(path.relative_to(run_dir))] = _sha256(path)
    return files


def train(run_dir: Path, lock_fd: int, mode: str) -> int:
    # The parent acquired this advisory lock before snapshotting and passed its
    # open file description into this process. Keep it open for this worker's life.
    os.fstat(lock_fd)
    _status(run_dir, process_id=os.getpid(), worker_started_at=_utc(), nondeployable=True)
    import cv2  # noqa: F401 -- fixed preprocessing dependencies already loaded by frozen tensors
    import numpy as np
    import torch
    import torch.nn as nn
    from ultralytics.nn.tasks import ClassificationModel

    config = _read(run_dir / "config.json")
    manifest = _read(run_dir / "dataset_manifest.json")
    _verify(run_dir, config, manifest)
    torch.set_num_threads(config["threads"])
    try:
        torch.set_num_interop_threads(config["threads"])
    except RuntimeError:
        pass
    seed = int(run_dir.name[:8], 16)
    if mode == "start":
        random.seed(seed)
        np.random.seed(seed % (2 ** 32))
        torch.manual_seed(seed)
    device = torch.device("cpu")
    base_relative = Path(config.get("base_weights_file", ""))
    if base_relative.is_absolute() or ".." in base_relative.parts:
        raise RuntimeError("Base weights path escapes the candidate bundle")
    checkpoint_path = run_dir / base_relative
    if checkpoint_path.is_symlink() or run_dir.resolve() not in checkpoint_path.resolve().parents:
        raise RuntimeError("Base weights cannot be a symlink")
    base = torch.load(checkpoint_path, map_location=device, weights_only=False)
    base_model = base.get("model") if isinstance(base, dict) else None
    if not isinstance(base_model, ClassificationModel):
        raise RuntimeError("Trusted local weights are not an Ultralytics ClassificationModel")
    if base_model.yaml.get("scale") != "n" or Path(base_model.yaml.get("yaml_file", "")).name != "yolov8n-cls.yaml":
        raise RuntimeError("Trusted weights are not the expected YOLOv8n classification architecture")
    model = base_model.float().to(device)
    head = model.model[-1]
    if not hasattr(head, "linear") or not isinstance(head.linear, nn.Linear):
        raise RuntimeError("Trusted local classifier head does not match expected YOLOv8n architecture")
    head.linear = nn.Linear(head.linear.in_features, 3)
    model.names = {0: "campus_uniform", 1: "organizational_shirt", 2: "other_clothing"}
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    head_parameter_ids = {id(parameter) for parameter in head.parameters()}
    if not any(parameter.requires_grad and id(parameter) not in head_parameter_ids
               for parameter in model.parameters()):
        raise RuntimeError("YOLOv8n classifier backbone has no trainable parameters")
    if not any(parameter.requires_grad for parameter in head.parameters()):
        raise RuntimeError("Three-class classifier head has no trainable parameters")
    architecture_record = {"architecture": "ultralytics-yolov8n-classification",
                           "yaml_file": Path(model.yaml.get("yaml_file", "")).name,
                           "scale": model.yaml.get("scale"), "mapping_version": config["mapping_version"],
                           "class_indices": {str(k): v for k, v in model.names.items()},
                           "trainable_parameter_count": sum(p.numel() for p in model.parameters() if p.requires_grad),
                           "trainable_backbone_parameter_count": sum(
                               p.numel() for p in model.parameters() if p.requires_grad and id(p) not in head_parameter_ids),
                           "trainable_head_parameter_count": sum(p.numel() for p in head.parameters() if p.requires_grad)}
    architecture_path = run_dir / "architecture.json"
    if mode == "resume" and architecture_path.is_file():
        if _read(architecture_path) != architecture_record:
            raise RuntimeError("Loaded model architecture or trainable parameter inventory changed")
    else:
        _atomic_json(architecture_path, architecture_record)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"])
    config_hash = _sha256(run_dir / "config.json")
    checkpoint_metadata = {"run_uuid": run_dir.name, "mapping_version": config["mapping_version"],
                           "dataset_manifest_sha256": config["dataset_manifest_sha256"],
                           "base_weights_sha256": config["base_weights_sha256"], "config_sha256": config_hash}
    start_epoch = 0
    if mode == "resume":
        pointer = _read(run_dir / "last_checkpoint.json")
        checkpoint_file = Path(pointer.get("file", ""))
        if (checkpoint_file.is_absolute() or ".." in checkpoint_file.parts
                or not re.fullmatch(r"checkpoint_epoch_[0-9]{6}\.pt", checkpoint_file.name)):
            raise RuntimeError("Checkpoint path escapes the bundle")
        sidecar_path = run_dir / f"{checkpoint_file.name}.sha256.json"
        checkpoint_path = run_dir / checkpoint_file
        for candidate_path in (checkpoint_path, sidecar_path):
            cursor = run_dir
            for component in candidate_path.relative_to(run_dir).parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    raise RuntimeError("Checkpoint path contains a symlink")
        sidecar = _read(sidecar_path)
        if any(sidecar.get(k) != v for k, v in checkpoint_metadata.items()):
            raise RuntimeError("Checkpoint sidecar metadata does not match this run")
        if (sidecar.get("sha256") != pointer.get("sha256") or not checkpoint_path.is_file()
                or _sha256(checkpoint_path) != sidecar.get("sha256")):
            raise RuntimeError("Checkpoint SHA256 verification failed")
        if checkpoint_file.name != f"checkpoint_epoch_{int(sidecar.get('epoch', -1)):06d}.pt":
            raise RuntimeError("Checkpoint filename does not match its epoch metadata")
        saved = torch.load(checkpoint_path, map_location=device, weights_only=False)
        if any(saved.get(key) != value for key, value in checkpoint_metadata.items()):
            raise RuntimeError("Checkpoint run, dataset, base weights, or mapping does not match this bundle")
        model.load_state_dict(saved["model_state_dict"], strict=True)
        optimizer.load_state_dict(saved["optimizer_state_dict"])
        start_epoch = int(saved["epoch"])
        torch.set_rng_state(saved["torch_rng_state"])
        random.setstate(saved["python_rng_state"])
        np.random.set_state(saved["numpy_rng_state"])
    else:
        _checkpoint(torch, random, np, model, optimizer, 0, run_dir, checkpoint_metadata)
    entries = manifest["entries"]
    if not entries:
        raise RuntimeError("Frozen candidate manifest contains no examples")
    labels = torch.tensor([int(entry["class_index"]) for entry in entries], dtype=torch.long)
    paths = []
    for entry in entries:
        path = (run_dir / entry["prepared_path"]).resolve()
        if run_dir.resolve() not in path.parents or _sha256(path) != entry["prepared_sha256"]:
            raise RuntimeError("Prepared tensor changed after the manifest was frozen")
        paths.append(path)
    criterion = nn.CrossEntropyLoss()
    for epoch in range(start_epoch, config["epochs"]):
        if _cancelled(run_dir):
            _status(run_dir, status="cancelled", epoch=start_epoch, resumable=True,
                    message="Cancelled at epoch boundary; last complete epoch checkpoint is preserved.")
            return 0
        model.train()
        permutation = torch.randperm(len(paths)).tolist()
        loss_sum = 0.0
        examples = 0
        cancelled = False
        for offset in range(0, len(permutation), config["batch"]):
            if _cancelled(run_dir):
                cancelled = True
                break
            batch_indices = permutation[offset:offset + config["batch"]]
            batch_np = np.stack([np.load(paths[i], allow_pickle=False) for i in batch_indices])
            batch = torch.from_numpy(np.moveaxis(batch_np, -1, 1).copy()).to(dtype=torch.float32, device=device)
            target = labels[batch_indices]
            optimizer.zero_grad(set_to_none=True)
            logits = model(batch)
            if not isinstance(logits, torch.Tensor) or logits.shape != (len(batch_indices), 3):
                raise RuntimeError(f"Classifier returned an unexpected output shape: {getattr(logits, 'shape', None)}")
            loss = criterion(logits, target)
            if not torch.isfinite(loss):
                raise RuntimeError("Training produced a non-finite loss")
            loss.backward()
            optimizer.step()
            loss_sum += float(loss.detach()) * len(batch_indices)
            examples += len(batch_indices)
        if cancelled or _cancelled(run_dir):
            _status(run_dir, status="cancelled", epoch=start_epoch, resumable=True,
                    message="Cancelled during epoch; the partial epoch will be retrained from the last complete checkpoint.")
            return 0
        completed_epoch = epoch + 1
        _checkpoint(torch, random, np, model, optimizer, completed_epoch, run_dir, checkpoint_metadata)
        average_loss = loss_sum / max(1, examples)
        _status(run_dir, status="running", epoch=completed_epoch, epochs=config["epochs"],
                train_loss=average_loss, resumable=True,
                message=f"Completed epoch {completed_epoch}/{config['epochs']}; no validation metrics are produced.")
        print(f"[{_utc()}] run_uuid={run_dir.name} epoch={completed_epoch} train_loss={average_loss:.8f}", flush=True)
        start_epoch = completed_epoch
    weights = {"architecture": "ultralytics-yolov8n-classification", "mapping_version": config["mapping_version"],
               "classes": model.names, "state_dict": model.state_dict(), "input_size": 224,
               "preprocessing": manifest["model_preprocess"], "nondeployable": True}
    _atomic_torch_save(torch, weights, run_dir / "candidate.pt")
    limitations = ("EXPERIMENTAL TRAIN-ONLY candidate. No validation or evaluation was performed.\n"
                   "Training loss is not an accuracy measure. Candidate is not activated and is not a drop-in live model.\n")
    (run_dir / "limitations.txt").write_text(limitations, encoding="utf-8")
    checksums = _bundle_checksums(run_dir)
    _atomic_json(run_dir / "bundle_checksums.json", checksums)
    _status(run_dir, status="completed", epoch=config["epochs"], epochs=config["epochs"],
            resumable=False, message="Candidate training completed; no validation/evaluation or activation was performed.",
            candidate_file="candidate.pt", nondeployable=True,
            bundle_checksums_sha256=_sha256(run_dir / "bundle_checksums.json"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("usage: python -m core.uniform_candidate_worker RUN_DIR LOCK_FD start|resume", file=sys.stderr)
        return 2
    run_dir = Path(args[0]).resolve()
    try:
        lock_fd, mode = int(args[1]), args[2]
        if mode not in {"start", "resume"}:
            raise ValueError("Invalid worker mode")
        return train(run_dir, lock_fd, mode)
    except BaseException as exc:
        try:
            state = _read(run_dir / "status.json")
            checkpoint = run_dir / "last_checkpoint.json"
            state.update(status="failed", message=f"{type(exc).__name__}: {exc}",
                         resumable=checkpoint.is_file(), nondeployable=True, updated_at=_utc())
            limitations = run_dir / "limitations.txt"
            if not limitations.exists():
                limitations.write_text("NONDEPLOYABLE candidate. Training failure; no evaluation or activation.\n", encoding="utf-8")
            _atomic_json(run_dir / "status.json", state)
        except Exception:
            pass
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
