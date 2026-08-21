"""Thread-safe active camera handle for API streaming and selection."""

from __future__ import annotations

import platform
import threading
from typing import Any

import cv2

from api.camera_store import get_camera_preference, get_saved_ip_cameras, save_camera_preference
from core.camera import CAMERA_DEVICE_LOCK, CameraCapture
from core.ip_camera import open_stream, probe_camera

_USB_SCAN_MAX = 10


def _backend() -> int | None:
    if platform.system() == "Darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
        return cv2.CAP_AVFOUNDATION
    return None


def test_ip_camera(url: str, *, timeout_frames: int = 15) -> bool:
    """Timeout-hardened reachability check for a known stream URL."""
    url = url.strip()
    if not url:
        return False
    with CAMERA_DEVICE_LOCK:
        cap = open_stream(url)
        if cap is None:
            return False
        ok = False
        try:
            for _ in range(timeout_frames):
                ret, frame = cap.read()
                if ret and frame is not None and getattr(frame, "size", 0) > 0:
                    ok = True
                    break
        finally:
            cap.release()
        return ok


def probe_ip_camera(address: str, username: str = "", password: str = "",
                    *, path: str | None = None, on_progress=None) -> str | None:
    """Auto-detect a working stream URL from IP + credentials (None if not found)."""
    with CAMERA_DEVICE_LOCK:
        return probe_camera(address, username, password, path=path, on_progress=on_progress)


def scan_usb_cameras() -> list[dict[str, Any]]:
    cameras: list[dict[str, Any]] = []
    backend = _backend()
    with CAMERA_DEVICE_LOCK:
        for index in range(_USB_SCAN_MAX):
            cap = (
                cv2.VideoCapture(index, backend)
                if backend is not None
                else cv2.VideoCapture(index)
            )
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                continue
            ret, _ = cap.read()
            if ret:
                cameras.append(
                    {
                        "id": f"usb_{index}",
                        "index": index,
                        "type": "usb",
                        "label": f"USB Camera {index}",
                        "status": "available",
                    }
                )
            cap.release()
    return cameras


class CameraManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Opening a camera can take several seconds.  Serialise selections without
        # holding ``_lock`` so readers of ``active_camera`` never block behind I/O and
        # a successful selection cannot recursively acquire the same lock.
        self._selection_lock = threading.Lock()
        self._capture: CameraCapture | None = None
        self._active: dict[str, Any] | None = None

    @property
    def active_camera(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._active) if self._active else None

    def scan_all(self) -> list[dict[str, Any]]:
        cameras = scan_usb_cameras()
        active_id = (self.active_camera or {}).get("id")

        for cam in get_saved_ip_cameras():
            cam_id = f"ip_{cam['id']}"
            reachable = test_ip_camera(str(cam["url"]))
            cameras.append(
                {
                    "id": cam_id,
                    "type": "rj45",
                    "label": cam.get("label", "IP Camera"),
                    "url": cam["url"],
                    "status": "available" if reachable else "unreachable",
                }
            )
            if active_id == cam_id and reachable:
                with self._lock:
                    if self._active:
                        self._active["status"] = "available"

        if active_id:
            for item in cameras:
                if item["id"] == active_id:
                    item["is_active"] = True
                else:
                    item["is_active"] = False
        return cameras

    def test_url(self, url: str) -> tuple[bool, str]:
        if test_ip_camera(url):
            return True, "Camera reachable"
        cap = cv2.VideoCapture(url.strip())
        if cap.isOpened():
            cap.release()
            return False, "Stream opened but no frame"
        return False, "Cannot connect to camera"

    def select(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open and select a source without holding the state lock during I/O.

        The old implementation held ``_lock`` and returned ``self.active_camera``
        from inside it.  ``active_camera`` acquires ``_lock`` too, causing a permanent
        self-deadlock after every successful open.
        """
        cam_type = str(payload.get("type", "")).lower()
        cam_id = str(payload.get("id", ""))

        if cam_type == "usb":
            index = payload.get("index")
            if index is None and cam_id.startswith("usb_"):
                try:
                    index = int(cam_id.split("_", 1)[1])
                except (IndexError, ValueError):
                    index = 0
            index = int(index if index is not None else 0)
            candidate = CameraCapture(camera_index=index)
            active = {
                "id": cam_id or f"usb_{index}",
                "type": "usb",
                "index": index,
                "label": payload.get("label") or f"USB Camera {index}",
                "status": "connected",
            }
        elif cam_type in ("rj45", "ip"):
            url = str(payload.get("url", "")).strip()
            if not url:
                raise ValueError("URL is required for IP cameras")
            candidate = CameraCapture(source_url=url)
            active = {
                "id": cam_id,
                "type": "rj45",
                "label": payload.get("label") or "IP Camera",
                "url": url,
                "status": "connected",
            }
        else:
            raise ValueError("Unknown camera type")

        with self._selection_lock:
            # Detach and release the previous handle before opening the replacement;
            # many USB drivers allow only one owner.  Slow release/open operations do
            # not hold the small state lock.
            with self._lock:
                previous = self._capture
                self._capture = None
                self._active = None
            if previous is not None:
                previous.release()

            try:
                opened = candidate.open()
            except Exception as exc:
                candidate.release()
                return {
                    "success": False,
                    "message": f"Failed to open camera: {exc}",
                    "active": None,
                }
            if not opened:
                candidate.release()
                return {"success": False, "message": "Failed to open camera", "active": None}

            with self._lock:
                self._capture = candidate
                self._active = active

            save_camera_preference(active)
            return {"success": True, "message": "Camera selected", "active": dict(active)}

    def clear_active_if(self, camera_id: str) -> None:
        with self._selection_lock:
            with self._lock:
                if self._active and self._active.get("id") == camera_id:
                    capture = self._capture
                    self._capture = None
                    self._active = None
                else:
                    capture = None
            if capture is not None:
                capture.release()

    def restore_preference(self) -> dict[str, Any] | None:
        pref = get_camera_preference()
        if not pref:
            return None
        try:
            result = self.select(pref)
            if result.get("success"):
                return result.get("active")
        except (ValueError, TypeError):
            pass
        return None

    def read_frame(self) -> Any | None:
        # Serialise read against selection/clear, but do not hold the state lock while
        # a backend read blocks; active_camera remains immediately observable.
        with self._selection_lock:
            with self._lock:
                cap = self._capture
            if cap is None or not cap.is_open:
                return None
            return cap.read()

    def read_frame_jpeg(self, quality: int = 80, *, annotate: bool = True) -> bytes | None:
        frame = self.read_frame()
        if frame is None:
            return None
        if annotate:
            try:
                from api.detection_service import detection_service

                frame = detection_service.process_stream_frame(frame)
            except Exception:
                pass
        ok, encoded = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        )
        return encoded.tobytes() if ok else None


camera_manager = CameraManager()
