"""Persistence for IP cameras and last-selected camera preference."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_IP_CAMERAS_PATH = _DATA_DIR / "ip_cameras.json"
_PREFERENCE_PATH = _DATA_DIR / "camera_preference.json"


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default.copy()
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default.copy()
    except (json.JSONDecodeError, OSError):
        return default.copy()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _ensure_data_dir()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_saved_ip_cameras() -> list[dict[str, Any]]:
    payload = _read_json(_IP_CAMERAS_PATH, {"cameras": []})
    cameras = payload.get("cameras", [])
    if not isinstance(cameras, list):
        return []
    return [c for c in cameras if isinstance(c, dict) and c.get("id") and c.get("url")]


def add_ip_camera(label: str, url: str) -> dict[str, Any]:
    label = label.strip()
    url = url.strip()
    if not label or not url:
        raise ValueError("Label and URL are required")

    payload = _read_json(_IP_CAMERAS_PATH, {"cameras": []})
    cameras: list[dict[str, Any]] = list(payload.get("cameras", []))
    entry = {
        "id": uuid.uuid4().hex[:12],
        "label": label,
        "url": url,
    }
    cameras.append(entry)
    _write_json(_IP_CAMERAS_PATH, {"cameras": cameras})
    return entry


def delete_ip_camera(camera_id: str) -> bool:
    payload = _read_json(_IP_CAMERAS_PATH, {"cameras": []})
    cameras: list[dict[str, Any]] = list(payload.get("cameras", []))
    new_cameras = [c for c in cameras if str(c.get("id")) != camera_id]
    if len(new_cameras) == len(cameras):
        return False
    _write_json(_IP_CAMERAS_PATH, {"cameras": new_cameras})
    return True


def get_camera_preference() -> dict[str, Any] | None:
    data = _read_json(_PREFERENCE_PATH, {})
    if not data.get("id"):
        return None
    return data


def save_camera_preference(preference: dict[str, Any]) -> None:
    _write_json(_PREFERENCE_PATH, preference)
