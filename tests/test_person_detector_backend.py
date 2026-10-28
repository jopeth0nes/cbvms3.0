import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from core.person_detector import PersonDetector


class FakeModel:
    def __init__(self, *, fail_mps_init=False, fail_mps_predict=False, fail_cpu_move=False):
        self.placement = "cpu"
        self.to_calls = []
        self.predict_calls = []
        self.fail_mps_init = fail_mps_init
        self.fail_mps_predict = fail_mps_predict
        self.fail_cpu_move = fail_cpu_move

    def to(self, device):
        name = str(device)
        self.to_calls.append(name)
        if name == "mps" and self.fail_mps_init:
            raise RuntimeError("simulated MPS transfer failure")
        if name == "cpu" and self.fail_cpu_move:
            raise RuntimeError("simulated CPU transfer failure")
        self.placement = name
        return self

    def predict(self, frame, **kwargs):
        self.predict_calls.append(kwargs["device"])
        if kwargs["device"] == "mps" and self.fail_mps_predict:
            raise RuntimeError("simulated MPS inference failure")
        return []


class PersonDetectorBackendTests(unittest.TestCase):
    def fake_torch(self):
        return SimpleNamespace(backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
                               device=lambda value: value)

    def test_requested_mps_initialization_failure_falls_back_once_to_cpu(self):
        detector = PersonDetector(backend="mps")
        detector._model = FakeModel(fail_mps_init=True)
        with patch.dict(sys.modules, {"torch": self.fake_torch()}), \
             patch.object(PersonDetector, "_model_device", staticmethod(lambda model: model.placement)):
            detector._initialize_backend(detector._model)
        self.assertEqual(detector.backend_requested, "mps")
        self.assertEqual(detector.backend_active, "cpu")
        self.assertEqual(detector._model.to_calls, ["mps", "cpu"])
        self.assertTrue(detector.backend_fallback_reason.startswith("initialization:"))

    def test_mps_inference_failure_drops_frame_and_uses_cpu_once_afterward(self):
        detector = PersonDetector(backend="mps")
        detector._model = FakeModel(fail_mps_predict=True)
        detector._model.placement = "mps"
        detector.backend_active = "mps"
        detector._backend_initialized = True
        frame = np.zeros((80, 100, 3), np.uint8)
        with patch.object(PersonDetector, "_model_device", staticmethod(lambda model: model.placement)), \
             patch.object(PersonDetector, "_sync_mps"):
            first = detector.detect_persons(frame)
            second = detector.detect_persons(frame)
        self.assertEqual(first, [])  # no duplicate CPU retry for the failed source frame
        self.assertEqual(second, [])
        self.assertEqual(detector._model.predict_calls, ["mps", "cpu"])
        self.assertEqual(detector._model.to_calls, ["cpu"])
        self.assertEqual(detector.backend_active, "cpu")
        self.assertTrue(detector.backend_status()["fallback_reason"].startswith("inference:"))

    def test_invalid_backend_is_explicitly_rejected_and_keeps_cpu_default(self):
        detector = PersonDetector(backend="cuda")
        self.assertEqual(detector.backend_requested, "cpu")
        self.assertIn("Unsupported", detector.last_error)

    def test_failed_cpu_fallback_is_latched_and_fails_closed(self):
        detector = PersonDetector(backend="mps")
        detector._model = FakeModel(fail_mps_predict=True, fail_cpu_move=True)
        detector._model.placement = "mps"
        detector.backend_active = "mps"
        detector._backend_initialized = True
        frame = np.zeros((80, 100, 3), np.uint8)
        with patch.object(PersonDetector, "_model_device", staticmethod(lambda model: model.placement)), \
             patch.object(PersonDetector, "_sync_mps"):
            detector.detect_persons(frame)
            detector.detect_persons(frame)
        self.assertEqual(detector._model.predict_calls, ["mps"])
        self.assertEqual(detector._model.to_calls, ["cpu"])
        self.assertEqual(detector.backend_active, "unavailable")
        self.assertTrue(detector._fallback_attempted)


if __name__ == "__main__":
    unittest.main()
