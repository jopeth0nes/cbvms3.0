"""Serialize access to shared model objects used by background workers."""

from functools import wraps


def locked_model(method):
    """Hold an instance's reentrant model lock through load/predict/result read."""
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._model_lock:
            return method(self, *args, **kwargs)
    return locked
