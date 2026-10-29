"""Single-owner, latest-frame-only companion camera. No inference or writes."""
from __future__ import annotations
import threading
import time


def source_key(source):
    if source.get('type') == 'usb':
        return ('usb', int(source.get('index', 0)))
    return ('ip', str(source.get('id') or source.get('url', '')))


class PreviewSession:
    def __init__(self, source, *, capture_factory=None, resolver=None, device_lock=None):
        self.source = dict(source)
        self.key = source_key(source)
        self.stop_event = threading.Event()
        self.done = threading.Event()
        self.status = 'Connecting'
        self._capture = None
        self._factory, self._resolver, self._device_lock = capture_factory, resolver, device_lock
        self.thread = threading.Thread(target=self._run, daemon=True, name='companion-preview')

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        return self.done

    def sample(self):
        cap = self._capture
        if self.stop_event.is_set() or cap is None:
            return None
        sample = cap.get_latest_sample()
        if sample is None or time.monotonic() - sample.captured_at > 1:
            return None
        return sample

    def _run(self):
        cap = None
        acquired = False
        lock = self._device_lock
        try:
            factory = self._factory
            if factory is None:
                from core.camera import CameraCapture, CAMERA_DEVICE_LOCK
                factory = CameraCapture
                lock = CAMERA_DEVICE_LOCK
            is_usb = self.source.get('type') == 'usb'
            if is_usb and lock is not None:
                deadline = time.monotonic() + 3
                while not self.stop_event.is_set() and time.monotonic() < deadline:
                    acquired = lock.acquire(timeout=.05)
                    if acquired:
                        break
                if not acquired:
                    self.status = 'Camera busy — retry when released'
                    return
            if self.stop_event.is_set():
                return
            kwargs = dict(camera_index=int(self.source.get('index', 0)),
                          width=1280, height=720, fps_cap=30, cancel_event=self.stop_event)
            if not is_usb:
                resolver = self._resolver
                if resolver is None:
                    from api.camera_store import resolve_camera_source
                    resolver = resolve_camera_source
                resolved = resolver(self.source, cancel_event=self.stop_event)
                kwargs.update(source_url=resolved['url'], transport=resolved.get('transport', 'tcp'))
            cap = factory(**kwargs)
            if not cap.open():
                self.status = 'Unavailable — check connection and retry'
                return
            self._capture = cap
            self.status = 'Preview only · no analysis or logs'
            last = time.monotonic()
            while not self.stop_event.is_set():
                frame = cap.read()
                if frame is not None:
                    last = time.monotonic()
                elif not cap.is_open or time.monotonic() - last > 3:
                    self.status = 'Disconnected — Retry preview'
                    break
                else:
                    self.stop_event.wait(.005)
        except Exception:
            # Camera/credential exceptions may contain passwords or stream URLs.
            self.status = 'Unavailable — check saved camera settings'
        finally:
            self._capture = None
            try:
                if cap is not None:
                    cap.release()
            finally:
                if acquired:
                    lock.release()
                self.done.set()
