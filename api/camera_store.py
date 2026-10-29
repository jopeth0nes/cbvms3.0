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
    # Keep saved stream configuration separate from the verified active source.
    # In particular, a resolved Keychain URL must never reach this file.
    safe = {k: v for k, v in preference.items() if k in ('id', 'type', 'index', 'label')}
    _write_json(_PREFERENCE_PATH, safe)


def resolve_camera_source(preference: dict[str, Any], *, cancel_event=None) -> dict[str, Any]:
    """Worker-only lookup of a saved source, including legacy Keychain references."""
    source = dict(preference)
    if source.get('type') not in ('rj45', 'ip'):
        return source
    camera_id = str(source.get('id', '')).removeprefix('ip_')
    saved = next((c for c in get_saved_ip_cameras() if str(c['id']) == camera_id), None)
    if saved is not None:
        source.update(saved, id=preference['id'], type='rj45')
    if source.get('secret_ref'):
        from core.camera_secrets import get_secret
        source['url'] = get_secret(source['secret_ref'], cancel_event=cancel_event)
    if not source.get('url'):
        raise ValueError('Saved camera unavailable. Open Camera Settings.')
    return source


_VIEW_PREFERENCE_PATH = _DATA_DIR / 'camera_view_preference.json'


def get_camera_view_preference():
    return _read_json(_VIEW_PREFERENCE_PATH, {})


def save_camera_view_preference(keys, destination):
    # Presentation has a separate preference: never alter the analysis source.
    safe = [[kind, value] for kind, value in keys
            if (kind == 'usb' and isinstance(value, int)) or
               (kind == 'ip' and isinstance(value, str) and '://' not in value)]
    _write_json(_VIEW_PREFERENCE_PATH, {'sources': safe, 'destination': destination})
