"""Isolated child process for experimental uniform candidate training."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
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


def _checkpoint(torch, random, np, model, optimizer, epoch, run_dir, metadata, device=None,
                training_state=None):
    device = device or torch.device("cpu")
    device_rng_state = None
    if device.type == "cuda" and torch.cuda.is_available():
        device_rng_state = torch.cuda.get_rng_state_all()
    elif device.type == "mps" and hasattr(torch, "mps") and hasattr(torch.mps, "get_rng_state"):
        device_rng_state = torch.mps.get_rng_state()
    path = run_dir / f"checkpoint_epoch_{epoch:06d}.pt"
    _atomic_torch_save(torch, {
        **metadata, "epoch": epoch, "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(), "torch_rng_state": torch.get_rng_state(),
        "device": str(device), "device_rng_state": device_rng_state,
        "python_rng_state": random.getstate(), "numpy_rng_state": np.random.get_state(),
        "training_state": training_state,
    }, path)
    digest = _sha256(path)
    sidecar = {**metadata, "epoch": epoch, "sha256": digest}
    _atomic_json(run_dir / f"{path.name}.sha256.json", sidecar)
    _atomic_json(run_dir / "last_checkpoint.json", {"file": path.name, "epoch": epoch, "sha256": digest})


def _cpu_state_dict(model):
    return {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}


def _write_best_artifact(torch, run_dir, training_state):
    """Rebuild the convenience best file solely from authoritative epoch state."""
    state = training_state or {}
    model_state = state.get("best_model_state_dict")
    epoch = state.get("best_epoch")
    loss = state.get("best_validation_loss")
    if model_state is None or epoch is None or loss is None:
        return
    path = run_dir / "best_checkpoint.pt"
    _atomic_torch_save(torch, {"epoch": int(epoch), "validation_loss": float(loss),
                               "model_state_dict": model_state}, path)
    _atomic_json(run_dir / "best_checkpoint.json", {
        "epoch": int(epoch), "validation_loss": float(loss),
        "early_stopping_wait": int(state.get("early_stopping_wait", 0)),
        "sha256": _sha256(path)})


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


def classification_metrics(labels, predictions, class_count=3) -> dict:
    """Return reproducible macro metrics with zero-safe per-class values."""
    matrix = [[0 for _ in range(class_count)] for _ in range(class_count)]
    for expected, actual in zip(labels, predictions):
        matrix[int(expected)][int(actual)] += 1
    precision, recall, f1 = [], [], []
    for index in range(class_count):
        tp = matrix[index][index]
        fp = sum(matrix[row][index] for row in range(class_count)) - tp
        fn = sum(matrix[index]) - tp
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        precision.append(p); recall.append(r)
        f1.append(2 * p * r / (p + r) if p + r else 0.0)
    total = sum(map(sum, matrix))
    return {"accuracy": sum(matrix[i][i] for i in range(class_count)) / total if total else None,
            "per_class_precision": precision, "per_class_recall": recall,
            "macro_f1": sum(f1) / class_count, "confusion_matrix": matrix, "examples": total}


def _logits(output, batch_size: int):
    if isinstance(output, (tuple, list)):
        tensors = [value for value in output if hasattr(value, "shape")]
        output = next((value for value in reversed(tensors)
                       if tuple(value.shape) == (batch_size, 3)), tensors[-1] if tensors else output)
    return output


def _device_supported(torch, device_name: str) -> bool:
    if device_name == "cpu": return True
    if device_name == "cuda": return torch.cuda.is_available()
    if device_name == "mps":
        return (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
                and hasattr(torch, "mps") and hasattr(torch.mps, "get_rng_state")
                and hasattr(torch.mps, "set_rng_state"))
    return False


def _evaluate(model, paths, labels, indices, np, torch, nn, device, batch_size):
    if not indices:
        return {"loss": None, "metrics": classification_metrics([], [])}
    model.eval()
    criterion = nn.CrossEntropyLoss()
    all_labels, all_predictions, loss_sum = [], [], 0.0
    with torch.no_grad():
        for offset in range(0, len(indices), batch_size):
            chosen = indices[offset:offset + batch_size]
            values = np.stack([np.load(paths[i], allow_pickle=False) for i in chosen])
            batch = torch.from_numpy(np.moveaxis(values, -1, 1).copy()).to(dtype=torch.float32, device=device)
            target = torch.tensor([labels[i] for i in chosen], dtype=torch.long, device=device)
            logits = _logits(model(batch), len(chosen))
            loss_sum += float(criterion(logits, target).detach()) * len(chosen)
            all_labels.extend(target.cpu().tolist())
            all_predictions.extend(logits.argmax(dim=1).cpu().tolist())
    return {"loss": loss_sum / len(indices), "metrics": classification_metrics(all_labels, all_predictions)}


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
    requested_threads = int(config["threads"])
    requested_batch = int(config["batch"])
    cpu_count = os.cpu_count() or 1
    safe_cpu_threads = max(1, min(requested_threads, cpu_count // 2 or 1, 4))
    torch.set_num_threads(safe_cpu_threads)
    try:
        torch.set_num_interop_threads(safe_cpu_threads)
    except RuntimeError:
        pass
    torch.use_deterministic_algorithms(True, warn_only=True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    seed = int(run_dir.name[:8], 16)
    if mode == "start":
        random.seed(seed)
        np.random.seed(seed % (2 ** 32))
        torch.manual_seed(seed)
    runtime_device_path = run_dir / "runtime_device.json"
    if mode == "resume":
        runtime_device = _read(runtime_device_path).get("device", "cpu")
        if not _device_supported(torch, runtime_device):
            raise RuntimeError(f"The original {runtime_device} device is unavailable; resume on the original device to preserve deterministic recovery.")
        device = torch.device(runtime_device)
    else:
        device = torch.device("cpu")
        if config.get("allow_accelerator"):
            if _device_supported(torch, "cuda"):
                device = torch.device("cuda")
            elif _device_supported(torch, "mps"):
                device = torch.device("mps")
        if device.type == "cuda": torch.cuda.manual_seed_all(seed)
        elif device.type == "mps": torch.mps.manual_seed(seed)
    base_relative = Path(config.get("base_weights_file", ""))
    if base_relative.is_absolute() or ".." in base_relative.parts:
        raise RuntimeError("Base weights path escapes the candidate bundle")
    checkpoint_path = run_dir / base_relative
    if checkpoint_path.is_symlink() or run_dir.resolve() not in checkpoint_path.resolve().parents:
        raise RuntimeError("Base weights cannot be a symlink")
    base = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    base_model = base.get("model") if isinstance(base, dict) else None
    if not isinstance(base_model, ClassificationModel):
        raise RuntimeError("Trusted local weights are not an Ultralytics ClassificationModel")
    if base_model.yaml.get("scale") != "n" or Path(base_model.yaml.get("yaml_file", "")).name != "yolov8n-cls.yaml":
        raise RuntimeError("Trusted weights are not the expected YOLOv8n classification architecture")
    model = base_model.float()
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    head = model.model[-1]
    if not hasattr(head, "linear") or not isinstance(head.linear, nn.Linear):
        raise RuntimeError("Trusted local classifier head does not match expected YOLOv8n architecture")
    head.linear = nn.Linear(head.linear.in_features, 3)
    model.names = {0: "campus_uniform", 1: "organizational_shirt", 2: "other_clothing"}
    if mode == "start" and device.type != "cpu":
        try:
            model = model.to(device)
            model.eval()
            probe = torch.zeros((1, 3, 224, 224), dtype=torch.float32, device=device)
            probe_logits = _logits(model(probe), 1)
            if not isinstance(probe_logits, torch.Tensor) or tuple(probe_logits.shape) != (1, 3):
                raise RuntimeError("Accelerator classifier output is incompatible")
            probe_logits.sum().backward()
            model.zero_grad(set_to_none=True)
            model.train()
        except Exception as exc:
            if device.type == "cuda": torch.cuda.empty_cache()
            model = model.to("cpu")
            device = torch.device("cpu")
            torch.manual_seed(seed)
            model.train()
            _status(run_dir, accelerator_fallback=f"Accelerator probe failed; using CPU ({type(exc).__name__}).")
        _atomic_json(runtime_device_path, {"device": str(device), "seed": seed})
    elif mode == "start":
        model = model.to(device)
        _atomic_json(runtime_device_path, {"device": str(device), "seed": seed})
    else:
        model = model.to(device)
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
    backbone_parameters = [p for p in model.parameters() if p.requires_grad and id(p) not in head_parameter_ids]
    head_parameters = [p for p in head.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW([{"params": backbone_parameters, "lr": 3e-5},
                                   {"params": head_parameters, "lr": 3e-4}],
                                  weight_decay=config["weight_decay"])
    config_hash = _sha256(run_dir / "config.json")
    checkpoint_metadata = {"run_uuid": run_dir.name, "mapping_version": config["mapping_version"],
                           "dataset_manifest_sha256": config["dataset_manifest_sha256"],
                           "base_weights_sha256": config["base_weights_sha256"], "config_sha256": config_hash,
                           "device": str(device)}
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
        saved = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        if any(saved.get(key) != value for key, value in checkpoint_metadata.items()):
            raise RuntimeError("Checkpoint run, dataset, base weights, or mapping does not match this bundle")
        model.load_state_dict(saved["model_state_dict"], strict=True)
        optimizer.load_state_dict(saved["optimizer_state_dict"])
        start_epoch = int(saved["epoch"])
        torch.set_rng_state(saved["torch_rng_state"].cpu())
        if device.type == "cuda" and saved.get("device_rng_state") is not None:
            torch.cuda.set_rng_state_all(saved["device_rng_state"])
        elif device.type == "mps" and saved.get("device_rng_state") is not None:
            torch.mps.set_rng_state(saved["device_rng_state"])
        random.setstate(saved["python_rng_state"])
        np.random.set_state(saved["numpy_rng_state"])
    else:
        _checkpoint(torch, random, np, model, optimizer, 0, run_dir, checkpoint_metadata, device,
                    {"best_validation_loss": None, "best_epoch": None, "early_stopping_wait": 0,
                     "best_model_state_dict": None})
    entries = manifest["entries"]
    if not entries:
        raise RuntimeError("Frozen candidate manifest contains no examples")
    labels = [int(entry["class_index"]) for entry in entries]
    train_indices = [i for i, entry in enumerate(entries) if entry.get("split") == "train"]
    validation_indices = [i for i, entry in enumerate(entries) if entry.get("split") == "validation"]
    evaluation_indices = [i for i, entry in enumerate(entries) if entry.get("split") == "evaluation"]
    if not train_indices:
        raise RuntimeError("Group-aware split produced no training examples")
    paths = []
    for entry in entries:
        path = (run_dir / entry["prepared_path"]).resolve()
        if run_dir.resolve() not in path.parents or _sha256(path) != entry["prepared_sha256"]:
            raise RuntimeError("Prepared tensor changed after the manifest was frozen")
        paths.append(path)
    class_counts = [sum(labels[i] == index for i in train_indices) for index in range(3)]
    class_weights = [((sum(class_counts) / max(1, count)) ** 0.5) for count in class_counts]
    weight_tensor = torch.tensor(class_weights, dtype=torch.float32, device=device)
    criterion = nn.CrossEntropyLoss(weight=weight_tensor)
    best_val_loss = float("inf")
    patience = 0
    best_checkpoint_path = run_dir / "best_checkpoint.pt"
    best_checkpoint_epoch = None
    best_model_state = None
    best_pointer = run_dir / "best_checkpoint.json"
    if mode == "resume":
        selection = saved.get("training_state") or {}
        best_val_loss = (float(selection["best_validation_loss"])
                         if selection.get("best_validation_loss") is not None else float("inf"))
        best_checkpoint_epoch = selection.get("best_epoch")
        patience = int(selection.get("early_stopping_wait", 0))
        best_model_state = selection.get("best_model_state_dict")
        _write_best_artifact(torch, run_dir, selection)
    batch_size = max(1, min(requested_batch, 4 if device.type == "cpu" else 8))
    resources = {"requested_threads": requested_threads, "actual_threads": safe_cpu_threads,
                 "requested_batch": requested_batch, "actual_batch": batch_size,
                 "cpu_count": cpu_count, "device": str(device), "accelerator_opt_in": bool(config.get("allow_accelerator"))}
    _atomic_json(run_dir / "runtime_resources.json", resources)
    _status(run_dir, runtime_resources=resources)
    for epoch in range(start_epoch, config["epochs"]):
        # A crash after the stopping checkpoint must not add another epoch.
        if best_model_state is not None and patience >= config.get("early_stopping_patience", 5):
            break
        if _cancelled(run_dir):
            _status(run_dir, status="cancelled", epoch=start_epoch, resumable=True,
                    message="Cancelled at epoch boundary; last complete epoch checkpoint is preserved.")
            return 0
        model.train()
        group_sizes = {}
        for i in train_indices:
            key = entries[i].get("sampling_group_id", entries[i]["image_id"])
            group_sizes[key] = group_sizes.get(key, 0) + 1
        sample_weights = torch.tensor([1.0 / group_sizes[entries[i].get("sampling_group_id", entries[i]["image_id"])]
                                       for i in train_indices], dtype=torch.double)
        draws = torch.multinomial(sample_weights, len(train_indices), replacement=True).tolist()
        permutation = [train_indices[i] for i in draws]
        loss_sum = 0.0
        examples = 0
        cancelled = False
        for offset in range(0, len(permutation), batch_size):
            if _cancelled(run_dir):
                cancelled = True
                break
            batch_indices = permutation[offset:offset + batch_size]
            batch_np = np.stack([np.load(paths[i], allow_pickle=False) for i in batch_indices])
            batch = torch.from_numpy(np.moveaxis(batch_np, -1, 1).copy()).to(dtype=torch.float32, device=device)
            # Mild photometric jitter is applied only to training data.
            brightness = (torch.rand((len(batch_indices), 1, 1, 1), device=device) - 0.5) * 0.16
            contrast = 1.0 + (torch.rand((len(batch_indices), 1, 1, 1), device=device) - 0.5) * 0.16
            batch = ((batch - 0.5) * contrast + 0.5 + brightness).clamp_(0.0, 1.0)
            target = torch.tensor([labels[i] for i in batch_indices], dtype=torch.long, device=device)
            optimizer.zero_grad(set_to_none=True)
            logits = _logits(model(batch), len(batch_indices))
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
        average_loss = loss_sum / max(1, examples)
        validation = _evaluate(model, paths, labels, validation_indices, np, torch, nn, device, batch_size)
        improved = validation["loss"] is not None and validation["loss"] < best_val_loss - config.get("minimum_improvement", 0.001)
        if improved:
            best_val_loss = validation["loss"]
            patience = 0
            best_checkpoint_epoch = completed_epoch
        elif validation["loss"] is not None:
            patience += 1
        if improved:
            best_model_state = _cpu_state_dict(model)
        selection = {"best_validation_loss": None if best_val_loss == float("inf") else best_val_loss,
                     "best_epoch": best_checkpoint_epoch, "early_stopping_wait": patience,
                     "best_model_state_dict": best_model_state}
        _checkpoint(torch, random, np, model, optimizer, completed_epoch, run_dir, checkpoint_metadata, device, selection)
        _write_best_artifact(torch, run_dir, selection)
        _status(run_dir, status="running", epoch=completed_epoch, epochs=config["epochs"],
                train_loss=average_loss, validation_loss=validation["loss"],
                validation_metrics=validation["metrics"], evaluation_metrics=None,
                best_epoch=best_checkpoint_epoch, early_stopping_wait=patience, resumable=True,
                message=f"Completed epoch {completed_epoch}/{config['epochs']}; validation metrics recorded.")
        print(f"[{_utc()}] run_uuid={run_dir.name} epoch={completed_epoch} train_loss={average_loss:.8f} validation_loss={validation['loss']}", flush=True)
        start_epoch = completed_epoch
        if validation["loss"] is not None and patience >= config.get("early_stopping_patience", 5):
            break
    if best_model_state is not None:
        pointer = _read(best_pointer)
        if (best_checkpoint_path.is_symlink() or not best_checkpoint_path.is_file()
                or _sha256(best_checkpoint_path) != pointer.get("sha256")
                or pointer.get("epoch") != best_checkpoint_epoch):
            raise RuntimeError("Best validation checkpoint integrity check failed before export")
        best = torch.load(best_checkpoint_path, map_location="cpu", weights_only=False)
        if best.get("epoch") != best_checkpoint_epoch or not math.isclose(
                float(best.get("validation_loss")), best_val_loss, rel_tol=0, abs_tol=0):
            raise RuntimeError("Best checkpoint does not match authoritative training state")
        model.load_state_dict(best_model_state, strict=True)
        final_eval = _evaluate(model, paths, labels, evaluation_indices, np, torch, nn, device, batch_size)
        final_validation = _evaluate(model, paths, labels, validation_indices, np, torch, nn, device, batch_size)
    else:
        final_eval = {"loss": None, "metrics": None}
        final_validation = {"loss": None, "metrics": None}
    weights = {"architecture": "ultralytics-yolov8n-classification", "mapping_version": config["mapping_version"],
               "classes": model.names, "state_dict": model.state_dict(), "input_size": 224,
               "preprocessing": manifest["model_preprocess"], "nondeployable": True}
    _atomic_torch_save(torch, weights, run_dir / "candidate.pt")
    metrics = {"validation_loss": final_validation["loss"], "validation": final_validation["metrics"],
               "evaluation_loss": final_eval["loss"], "evaluation": final_eval["metrics"],
               "training_distribution": {str(i): class_counts[i] for i in range(3)},
               "validation_distribution": {str(i): sum(labels[j] == i for j in validation_indices) for i in range(3)},
               "evaluation_distribution": {str(i): sum(labels[j] == i for j in evaluation_indices) for i in range(3)},
               "best_epoch": best_checkpoint_epoch, "evaluation_available": bool(evaluation_indices)}
    _atomic_json(run_dir / "metrics.json", metrics)
    limitations = ("Candidate is isolated, experimental, group-aware and never activated by this service.\n"
                   "Independent evaluation is unavailable for this dataset split.\n" if not evaluation_indices else
                   "Candidate is isolated, experimental, group-aware and never activated by this service.\n")
    (run_dir / "limitations.txt").write_text(limitations, encoding="utf-8")
    checksums = _bundle_checksums(run_dir)
    _atomic_json(run_dir / "bundle_checksums.json", checksums)
    _status(run_dir, status="completed", epoch=start_epoch, epochs=config["epochs"],
            resumable=False, message="Candidate training completed; group-aware metrics recorded; promotion remains disabled.",
            candidate_file="candidate.pt", nondeployable=True,
            best_epoch=best_checkpoint_epoch, evaluation_available=bool(evaluation_indices), metrics_file="metrics.json",
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
