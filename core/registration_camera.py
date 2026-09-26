"""Registration camera ownership and preview diagnostics (no Tk or inference)."""
from collections import deque
import queue
import threading
import time

import numpy as np

from core.camera import CAMERA_DEVICE_LOCK, CameraCapture


class RegistrationCamera:
    """One owner opens, pumps, and releases the device, including late-open cleanup.

    Only the newest sample is retained. Stop never releases a camera on the UI
    thread. A reopening waits for the previous owner via CAMERA_DEVICE_LOCK.
    """
    def __init__(self, *, camera_factory=CameraCapture):
        self._factory = camera_factory
        self.stop_event = threading.Event()
        self.done = threading.Event()
        self.events = queue.Queue()
        self._lock = threading.Lock()
        self._latest = None
        self._thread = None
        self.frame_times = deque(maxlen=300)

    def start(self):
        if self._thread is not None or self.stop_event.is_set():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="registration-camera")
        self._thread.start()

    def latest(self):
        with self._lock:
            return self._latest

    def stop(self):
        self.stop_event.set()
        with self._lock:
            self._latest = None

    def _run(self):
        camera = None
        acquired = False
        try:
            while not self.stop_event.is_set():
                if CAMERA_DEVICE_LOCK.acquire(timeout=.1):
                    acquired = True
                    break
            if not acquired or self.stop_event.is_set():
                return
            # CameraCapture chooses the native platform backend, requests 720p/30,
            # and keeps the device's negotiated resolution/rate if unsupported.
            for index in range(4):
                if self.stop_event.is_set():
                    return
                camera = self._factory(camera_index=index, width=1280, height=720, fps_cap=30)
                if camera.open():
                    break
                camera.release()
                camera = None
            if camera is None:
                self.events.put(("error", "No camera detected. Close and reopen to retry."))
                return
            if self.stop_event.is_set():
                return
            try:
                settings = camera.get_settings()
            except Exception:
                settings = {"requested_fps": 30, "reported_settings": "unavailable"}
            self.events.put(("opened", settings))
            while not self.stop_event.is_set():
                frame = camera.read()
                if frame is None:
                    self.stop_event.wait(.01)
                    continue
                sample = camera.get_latest_sample()
                with self._lock:
                    if not self.stop_event.is_set():
                        self._latest = sample
                        self.frame_times.append(sample.captured_at)
        except Exception as exc:
            self.events.put(("error", f"Camera unavailable: {exc}"))
        finally:
            try:
                if camera is not None:
                    camera.release()
            except Exception as exc:
                self.events.put(("error", f"Camera release failed: {exc}"))
            finally:
                with self._lock:
                    self._latest = None
                if acquired:
                    CAMERA_DEVICE_LOCK.release()
                self.done.set()


class PreviewMetrics:
    """Measure actual unique-frame renders, not callback count or requested FPS."""
    def __init__(self):
        self.render_times = deque(maxlen=300)
        self.inference_seconds = deque(maxlen=100)
        self.last_frame_id = None

    def rendered(self, sample, now):
        if sample.frame_id != self.last_frame_id:
            self.render_times.append(now)
            self.last_frame_id = sample.frame_id

    @staticmethod
    def rate(times):
        times = list(times)
        return (len(times)-1)/(times[-1]-times[0]) if len(times) > 1 and times[-1] > times[0] else 0.0

    def summary(self, source_times):
        times = list(self.render_times)
        gaps = np.diff(times) * 1000
        inference = list(self.inference_seconds)
        return {"source_fps": round(self.rate(source_times), 2),
                "preview_fps": round(self.rate(times), 2),
                "rendered_frames": len(times),
                "preview_gap_p95_ms": round(float(np.percentile(gaps, 95)), 1) if len(gaps) else None,
                "inference_p95_ms": round(float(np.percentile(inference, 95))*1000, 1) if inference else None}
