"""Serialize access to shared model objects used by background workers."""

from functools import wraps
from core.performance import PERF, timed_lock


def locked_model(method):
    """Hold an instance's reentrant model lock through load/predict/result read."""
    @wraps(method)
    def locked(self, *args, **kwargs):
        if not PERF.enabled:
            with self._model_lock:
                return method(self, *args, **kwargs)
        owner = "person" if type(self).__name__ == "PersonDetector" else "trainer"
        with timed_lock(self._model_lock, "lock_"+owner):
            return method(self, *args, **kwargs)
    return locked
