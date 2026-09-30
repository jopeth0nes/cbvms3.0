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
    def __init__(self, timeout=45.):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._loaders = {}
        self._status = {}
        self._running = set()
        self._threads = {}

    def add(self, name, loader):
        with self._lock:
            if name not in self._loaders:
                self._loaders[name] = loader
                self._status[name] = ComponentStatus()

    def start(self, *, retry=False):
        with self._lock:
            names = [n for n, s in self._status.items() if n not in self._running
                     and (s.state == "waiting" or (retry and s.state == "failed"))]
            for name in names:
                self._running.add(name)
                self._status[name] = ComponentStatus("loading", started=time.monotonic())
        for name in names:
            worker=threading.Thread(target=self._load,args=(name,),daemon=True,name=f'model-{name}')
            with self._lock:
                self._threads[name]=worker
                worker.start()

    def wait(self, timeout=5.):
        """Bounded shutdown grace; never claims to cancel a running native call."""
        deadline=time.monotonic()+timeout
        with self._lock:
            workers=tuple(self._threads.values())
        for worker in workers:
            worker.join(max(0,deadline-time.monotonic()))

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
        registry.add("face", recognizer._ensure_detector)
        registry.add("recognition", recognizer._ensure_recognition)
        recognizer.readiness = registry
    return recognizer.readiness
