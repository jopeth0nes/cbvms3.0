"""Shared model resources and bounded startup preparation for live monitoring."""
from __future__ import annotations

import numpy as np
import threading

from core.model_readiness import ModelReadiness
from core.trainer import ViolationTrainer
from core.uniform_matcher import UniformColorMatcher


_BUNDLE_LOCK = threading.Lock()


class _SharedBundle(type):
    def __call__(cls, recognizer, person_detector, trainer=None, uniform_matcher=None):
        # Publish only a completely bound registry. Concurrent callers cannot
        # start loaders tied to different trainers/matchers for one recognizer.
        with _BUNDLE_LOCK:
            existing = getattr(recognizer, "monitor_models", None)
            if isinstance(existing, cls):
                return existing
            bundle = super().__call__(recognizer, person_detector, trainer, uniform_matcher)
            recognizer.monitor_models = bundle
            return bundle


class MonitorModels(metaclass=_SharedBundle):
    """Own the monitor's reusable model objects and their one readiness registry.

    Two native loaders may run at once. Face detection and recognition have the
    highest queue priority, so optional model work cannot occupy either slot first.
    Once those are launched, body, uniform, and earring preparation use the slots.
    """
    def __init__(self, recognizer, person_detector, trainer=None, uniform_matcher=None):
        self.recognizer = recognizer
        self.person_detector = person_detector
        self.trainer = trainer if trainer is not None else ViolationTrainer()
        self.uniform_matcher = (uniform_matcher if uniform_matcher is not None
                                else UniformColorMatcher())
        self.readiness = getattr(recognizer, "readiness", None)
        if not isinstance(self.readiness, ModelReadiness):
            self.readiness = ModelReadiness(max_concurrent=2)
            self.readiness.add("face", recognizer._ensure_detector, priority=0)
            self.readiness.add("recognition", recognizer._ensure_recognition, priority=1)
            recognizer.readiness = self.readiness
        # If face_readiness was created by an existing caller before this bundle,
        # preserve that shared registry and apply a bound to future work.
        with self.readiness._lock:
            self.readiness.max_concurrent = 2
        self._bind_optional_loaders()

    def _bind_optional_loaders(self):
        self.readiness.add("body", self._load_body, priority=10)
        self.readiness.add("uniform", self._load_uniform, priority=11)
        self.readiness.add("earring", self._load_earring, priority=12)

    def _load_body(self):
        if self.person_detector is None:
            raise RuntimeError("Body detector unavailable")
        self.person_detector._load_failed = False
        if self.person_detector._ensure_model() is None:
            raise RuntimeError(self.person_detector.last_error or "Body detector failed")
        self.person_detector.detect_persons(np.zeros((320, 320, 3), np.uint8))
        if self.person_detector.last_error:
            raise RuntimeError(self.person_detector.last_error)
        return True

    def _load_uniform(self):
        # The colour matcher is supplementary; the trained classifier remains
        # useful on its own when no colour reference has been calibrated.
        if not self.uniform_matcher.is_loaded():
            self.uniform_matcher.load()
        if not self.trainer.is_trained("uniform"):
            raise RuntimeError("Train a uniform classifier in Training; colour reference alone cannot assess garments")
        if self.trainer._get_model("uniform") is None:
            raise RuntimeError(self.trainer.last_error.get("uniform") or "Uniform classifier failed")
        if self.trainer.predict_proba("uniform", np.zeros((224, 224, 3), np.uint8)) is None:
            raise RuntimeError("Uniform classifier inference failed; restore compatible weights and retry")
        if self.uniform_matcher.is_loaded():
            self.uniform_matcher.is_uniform(np.zeros((224, 224, 3), np.uint8))
        return True

    def _load_earring(self):
        if not self.trainer.is_trained("earring"):
            raise RuntimeError("No trained earring weights")
        if self.trainer._get_model("earring") is None:
            raise RuntimeError(self.trainer.last_error.get("earring") or "Earring classifier failed")
        if self.trainer.predict_proba("earring", np.zeros((224, 224, 3), np.uint8)) is None:
            raise RuntimeError("Earring classifier inference failed; restore compatible weights and retry")
        return True

    def start(self, *, retry=False):
        self.readiness.start(retry=retry)

    def wait(self, timeout=5.):
        self.readiness.wait(timeout)

    def cancel_pending(self):
        self.readiness.cancel_pending()
