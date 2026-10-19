"""OpenCV camera capture primitives for CBVMS."""

from __future__ import annotations

import platform
import threading
import time
import uuid
from dataclasses import dataclass

import cv2
import numpy as np

# Local USB/AVFoundation exclusivity only. Remote streams never take this lock.
CAMERA_DEVICE_LOCK = threading.Lock()


def usb_camera_label(index: int) -> str:
    if index == 0 and platform.system() == "Darwin":
        return "MacBook Camera"
    return f"USB Camera {index}"


def usb_camera_backends() -> tuple:
    if platform.system() == "Windows":
        return (cv2.CAP_DSHOW, cv2.CAP_MSMF)
    if platform.system() == "Darwin":
        return (cv2.CAP_AVFOUNDATION,)
    return (None,)


@dataclass(frozen=True)
class CameraSample:
    frame: np.ndarray
    frame_id: tuple
    captured_at: float
    captured_wall_time: float | None = None


class CameraCapture:
    """
    Camera handle intended to be owned by one worker thread for open/read/release.
    Keeping the full lifecycle on one thread is important for AVFoundation on macOS.
    """

    def __init__(
        self,
        camera_index: int | None = None,
        *,
        source_url: str | None = None,
        width: int = 1280,
        height: int = 720,
        fps_cap: int | None = None,
        cancel_event: threading.Event | None = None,
        transport: str = 'tcp',
    ) -> None:
        self._preferred_index = camera_index
        self._source_url = source_url.strip() if source_url else None
        self.cancel_event = cancel_event or threading.Event()
        self.transport = transport
        self._preferred_width = int(width)
        self._preferred_height = int(height)
        self._preferred_fps_cap = int(fps_cap) if fps_cap is not None else None
        self.camera_index = 0
        self.source_url: str | None = self._source_url
        self._cap: cv2.VideoCapture | None = None
        self.is_open = False
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._frame_sequence = 0
        self._frame_time = 0.0
        self._frame_wall_time = None
        self._source_token = uuid.uuid4().hex

    def _try_open_index(self, index: int) -> cv2.VideoCapture | None:
        for backend in usb_camera_backends():
            if self.cancel_event.is_set():
                return None
            cap = self._open_index_backend(index, backend)
            if cap is not None:
                return cap
        return None

    def _open_index_backend(self, index: int, backend: int | None) -> cv2.VideoCapture | None:
        cap = (
            cv2.VideoCapture(index, backend)
            if backend is not None
            else cv2.VideoCapture(index)
        )
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            return None

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._preferred_width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._preferred_height))
        if self._preferred_fps_cap is not None:
            try:
                cap.set(cv2.CAP_PROP_FPS, float(self._preferred_fps_cap))
            except Exception:
                pass

        warmed = False
        for _ in range(40):
            if self.cancel_event.is_set():
                cap.release()
                return None
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                self._remember(frame)
                warmed = True
                break
            time.sleep(0.03)

        if not warmed:
            cap.release()
            return None

        return cap

    def _try_open_url(self, url: str) -> cv2.VideoCapture | None:
        # Open via FFMPEG with TCP transport + timeouts so an unreachable network
        # stream fails in seconds instead of blocking the UI for 30s+.
        from core.network_capture import NetworkStream
        cap = NetworkStream(url, cancel_event=self.cancel_event, transport=self.transport)
        if not cap.open():
            self.last_error = cap.last_error
            return None
        ok, frame = cap.read()
        if not ok:
            self.last_error = cap.last_error or 'Stream opened but no video frames arrived.'
            cap.release()
            return None
        self._remember(frame, cap.captured_at, cap.captured_wall_time)
        return cap

    def open(self) -> bool:
        self.release()
        self._source_token = uuid.uuid4().hex
        self._frame_sequence = 0
        if self.cancel_event.is_set():
            self.last_error = 'Connection cancelled.'
            return False
        if self._source_url:
            cap = self._try_open_url(self._source_url)
            if cap is not None:
                self._cap = cap
                self.is_open = True
                self.last_error = None
                return True
            self.is_open = False
            self.last_error = self.last_error or 'Camera stream unavailable.'
            return False

        # A dropdown selection is an explicit device request: never silently show a
        # different camera while leaving the selected label/preference unchanged.
        indices = [self._preferred_index] if self._preferred_index is not None else [0, 1]

        for index in indices:
            cap = self._try_open_index(index)
            if cap is not None:
                self._cap = cap
                self.camera_index = index
                self.is_open = True
                self.last_error = None
                return True

        self.is_open = False
        if self._preferred_index is not None:
            self.last_error = f"Could not open USB camera {self._preferred_index}"
        else:
            self.last_error = "Could not open camera (tried indices 0 and 1)"
        return False

    def read(self) -> np.ndarray | None:
        if self._cap is None or not self.is_open:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None or frame.size == 0:
            if self._source_url and not self._cap.isOpened():
                self.is_open = False
                self.last_error = self._cap.last_error or 'The camera stopped delivering frames.'
            return None
        self._remember(frame, self._cap.captured_at if self._source_url else None,
                       self._cap.captured_wall_time if self._source_url else None)
        return frame

    def _remember(self, frame, captured_at=None, wall_time=None):
        with self._lock:
            self._latest_frame = frame
            self._frame_sequence += 1
            self._frame_time = captured_at or time.monotonic()
            self._frame_wall_time = wall_time or time.time()
            if self._frame_sequence == 1 or self._frame_sequence % 30 == 0:
                from core.diagnostics import event
                event("frames_captured", source=str(self._source_token), count=self._frame_sequence)

    def cancel(self):
        self.cancel_event.set()

    def get_latest_sample(self) -> CameraSample | None:
        """Atomic pixels + freshness metadata for enrollment consumers."""
        with self._lock:
            if self._latest_frame is None:
                return None
            return CameraSample(self._latest_frame.copy(), (self._source_token, self._frame_sequence),
                                self._frame_time, self._frame_wall_time)

    def get_settings(self) -> dict:
        """Backend-reported settings; call on the camera owner thread after open.

        Some devices report zero for FPS. Consumers must measure frame delivery
        rather than treat either the requested or reported rate as achieved FPS.
        """
        if self._cap is None:
            return {}
        if self._source_url:
            return self._cap.get_settings()
        return {
            "backend": self._cap.getBackendName(),
            "width": self._cap.get(cv2.CAP_PROP_FRAME_WIDTH),
            "height": self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
            "fps": self._cap.get(cv2.CAP_PROP_FPS),
            "requested_fps": self._preferred_fps_cap,
        }

    def get_latest_frame(self) -> np.ndarray | None:
        """Return the most recent frame without reading the device again."""
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.is_open = False
        with self._lock:
            self._latest_frame = None


class CameraThread(threading.Thread):
    """Legacy threaded capture — prefer CameraCapture for GUI apps on macOS."""

    def __init__(self, camera_index: int | None = None) -> None:
        super().__init__(daemon=True)
        self._capture = CameraCapture(camera_index)
        self.camera_index = 0
        self.is_open = False
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._running = False

    def open_capture(self) -> bool:
        ok = self._capture.open()
        self.is_open = ok
        self.camera_index = self._capture.camera_index
        self.last_error = self._capture.last_error
        return ok

    def run(self) -> None:
        if not self.is_open and not self.open_capture():
            return
        self._running = True
        interval = 1.0 / 30.0
        while self._running:
            t0 = time.perf_counter()
            frame = self._capture.read()
            if frame is not None:
                with self._lock:
                    self._latest_frame = frame
            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        self._capture.release()
        self.is_open = False

    def start(self) -> None:
        if not self.is_alive():
            super().start()

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def stop(self) -> None:
        self._running = False
        if self.is_alive():
            self.join(timeout=2.0)
        self._capture.release()
        self.is_open = False
