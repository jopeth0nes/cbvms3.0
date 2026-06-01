"""OpenCV camera capture for CBVMS (main-thread friendly for macOS + Tk)."""

from __future__ import annotations

import platform
import threading
import time

import cv2
import numpy as np


class CameraCapture:
    """
    Camera handle for reading on the UI thread.
    On macOS, VideoCapture must be opened and read from the same thread as Tk.
    """

    def __init__(
        self,
        camera_index: int | None = None,
        *,
        source_url: str | None = None,
        width: int = 1280,
        height: int = 720,
        fps_cap: int | None = None,
    ) -> None:
        self._preferred_index = camera_index
        self._source_url = source_url.strip() if source_url else None
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

    def _backend(self) -> int | None:
        if platform.system() == "Darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
            return cv2.CAP_AVFOUNDATION
        return None

    def _try_open_index(self, index: int) -> cv2.VideoCapture | None:
        backend = self._backend()
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
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                warmed = True
                break
            time.sleep(0.03)

        if not warmed:
            cap.release()
            return None

        return cap

    def _try_open_url(self, url: str) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(url)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            return None

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        warmed = False
        for _ in range(40):
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                warmed = True
                break
            time.sleep(0.03)

        if not warmed:
            cap.release()
            return None
        return cap

    def open(self) -> bool:
        self.release()
        if self._source_url:
            cap = self._try_open_url(self._source_url)
            if cap is not None:
                self._cap = cap
                self.is_open = True
                self.last_error = None
                return True
            self.is_open = False
            self.last_error = f"Could not open stream: {self._source_url}"
            return False

        indices: list[int] = []
        if self._preferred_index is not None:
            indices.append(self._preferred_index)
        for idx in (0, 1):
            if idx not in indices:
                indices.append(idx)

        for index in indices:
            cap = self._try_open_index(index)
            if cap is not None:
                self._cap = cap
                self.camera_index = index
                self.is_open = True
                self.last_error = None
                return True

        self.is_open = False
        self.last_error = "Could not open camera (tried indices 0 and 1)"
        return False

    def read(self) -> np.ndarray | None:
        if self._cap is None or not self.is_open:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None or frame.size == 0:
            return None
        with self._lock:
            self._latest_frame = frame
        return frame

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
