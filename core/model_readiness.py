"""Independent, single-owner model initialization with explicit failure/retry.

Only local assets are loaded. A deadline marks a still-running native call as
stalled; it does NOT stop that call, release its lock, or permit a second loader.
"""
from dataclasses import dataclass
import threading
import time

from core.diagnostics import event


@dataclass(frozen=True)
class ComponentStatus:
    state: str = "waiting"
    error: str = ""
    started: float = 0.
    elapsed: float = 0.


class ModelReadiness:
    def __init__(self, timeout=45., max_concurrent=None):
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self._lock = threading.RLock()
        self._loaders = {}
        self._status = {}
        self._running = set()
        self._threads = {}
        self._priority = {}
        self._sequence = 0
        self._closed = False

    def add(self, name, loader, *, priority=100):
        with self._lock:
            if name not in self._loaders:
                self._loaders[name] = loader
                self._status[name] = ComponentStatus()
                self._priority[name] = (priority, self._sequence)
                self._sequence += 1

    def _launch_locked(self, retry=False):
        if self._closed:
            return
        if retry:
            # Queue every failed component for this explicit cycle. As each slot
            # opens, face-priority ordering chooses the next item. A failed retry
            # returns to failed and is never automatically requeued.
            for name, status in tuple(self._status.items()):
                if status.state == "failed":
                    self._status[name] = ComponentStatus()
        while self.max_concurrent is None or len(self._running) < self.max_concurrent:
            candidates = [n for n, status in self._status.items()
                          if n not in self._running and status.state == "waiting"]
            if not candidates:
                return
            name = min(candidates, key=lambda n: self._priority[n])
            started = time.monotonic()
            self._running.add(name)
            self._status[name] = ComponentStatus("loading", started=started)
            worker = threading.Thread(target=self._load, args=(name,), daemon=True,
                                      name=f"model-{name}")
            self._threads[name] = worker
            try:
                # Start under the registry lock so wait/close can never observe a
                # reserved loading component whose thread has not started yet.
                worker.start()
            except Exception as exc:
                self._running.remove(name)
                self._status[name] = ComponentStatus("failed", str(exc), started,
                                                     time.monotonic()-started)

    def start(self, *, retry=False):
        with self._lock:
            self._launch_locked(retry=retry)

    def cancel_pending(self):
        """Prevent queued work from launching; running native calls retain ownership."""
        with self._lock:
            self._closed = True
            for name, status in tuple(self._status.items()):
                if name not in self._running and status.state == "waiting":
                    self._status[name] = ComponentStatus(
                        "cancelled", "Preparation cancelled before this component started.")

    def wait(self, timeout=5.):
        """Bounded shutdown grace; never claims to cancel a running native call."""
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            with self._lock:
                workers = tuple(self._threads[n] for n in self._running
                                if n in self._threads)
                if not workers:
                    return
            for worker in workers:
                if worker.ident is not None:
                    worker.join(max(0, deadline-time.monotonic()))
            time.sleep(.001)

    def _load(self, name):
        event("component_load_start", component=name)
        started = time.monotonic()
        state, error = "ready", ""
        try:
            result = self._loaders[name]()
            if result is None or result is False:
                raise RuntimeError("Loader did not return a usable component")
        except Exception as exc:
            state, error = "failed", str(exc)
        elapsed = time.monotonic()-started
        with self._lock:
            self._status[name] = ComponentStatus(state, error, started, elapsed)
            self._running.remove(name)
            self._launch_locked()
        event("component_load_complete" if state == "ready" else "component_load_failed",
              component=name, elapsed=round(elapsed, 3), error=error)

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            for name in self._running:
                s = self._status[name]
                if s.state == "loading" and now-s.started > self.timeout:
                    self._status[name] = ComponentStatus("stalled",
                        "Initialization exceeded deadline; still running. Restart if it does not finish.",
                        s.started, now-s.started)
                    event("component_load_stalled", component=name, elapsed=now-s.started)
            return dict(self._status)

    def ready(self, name):
        return self.snapshot().get(name, ComponentStatus()).state == "ready"

    def message(self):
        labels = {"face": "Face detector", "recognition": "Recognition",
                  "body": "Torso detector", "uniform": "Uniform model", "earring": "Earring model"}
        parts = []
        for name, s in self.snapshot().items():
            label = labels.get(name, name)
            if s.state == "ready":
                parts.append(f"{label} ready")
            elif s.state in ("waiting", "loading"):
                parts.append(f"Loading {label.lower()}")
            else:
                parts.append(f"{label} unavailable: {s.error}")
        return " · ".join(parts)


def face_readiness(recognizer):
    # The same registry travels with the shared recognizer from login to dashboard.
    if not hasattr(recognizer, "readiness"):
        registry = ModelReadiness()
        registry.add("face", recognizer._ensure_detector, priority=0)
        registry.add("recognition", recognizer._ensure_recognition, priority=1)
        recognizer.readiness = registry
    return recognizer.readiness
