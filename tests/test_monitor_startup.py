"""Synthetic checks for the shared, bounded monitor startup bundle."""
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import numpy as np

from core.monitor_models import MonitorModels


def wait_until(predicate, timeout=2.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Timed out waiting for model preparation")


class MonitorStartupTests(unittest.TestCase):
    def test_concurrent_bundle_construction_publishes_one_complete_registry(self):
        recognizer = MagicMock()
        detector, trainer, matcher = MagicMock(), MagicMock(), MagicMock()
        with ThreadPoolExecutor(max_workers=4) as pool:
            bundles = list(pool.map(lambda _: MonitorModels(recognizer, detector, trainer, matcher), range(12)))
        self.assertTrue(all(bundle is bundles[0] for bundle in bundles))
        self.assertEqual(set(bundles[0].readiness.snapshot()), {"face", "recognition", "body", "uniform", "earring"})

    def make_bundle(self):
        recognizer = MagicMock()
        recognizer._ensure_detector.return_value = True
        recognizer._ensure_recognition.return_value = True
        detector = MagicMock()
        detector._ensure_model.return_value = object()
        detector.detect_persons.return_value = []
        detector.last_error = None
        trainer = MagicMock()
        trainer.is_trained.side_effect = lambda module: module == "uniform"
        trainer._get_model.return_value = object()
        trainer.predict_proba.return_value = {"correct_uniform": .5}
        matcher = MagicMock()
        matcher.is_loaded.return_value = True
        return MonitorModels(recognizer, detector, trainer, matcher), recognizer, detector, trainer, matcher

    def test_optional_preparation_is_available_at_login_start_and_resources_are_reused(self):
        bundle, recognizer, detector, trainer, matcher = self.make_bundle()
        second = MonitorModels(recognizer, detector, trainer, matcher)
        self.assertIs(bundle.readiness, second.readiness)
        self.assertIs(bundle.trainer, second.trainer)
        self.assertIs(bundle.uniform_matcher, second.uniform_matcher)
        bundle.start()
        wait_until(lambda: all(bundle.readiness.snapshot()[name].state in ("ready", "failed")
                               for name in ("face", "recognition", "body", "uniform", "earring")))
        self.assertTrue(bundle.readiness.ready("body"))
        self.assertTrue(bundle.readiness.ready("uniform"))
        self.assertEqual(bundle.readiness.snapshot()["earring"].state, "failed")
        detector.detect_persons.assert_called_once()
        trainer.predict_proba.assert_called_once_with("uniform", unittest.mock.ANY)
        matcher.is_uniform.assert_called_once()
        recognizer._ensure_detector.assert_called_once()
        recognizer._ensure_recognition.assert_called_once()

    def test_face_priority_and_optional_concurrency_are_bounded(self):
        bundle, recognizer, detector, trainer, matcher = self.make_bundle()
        gate = threading.Event()
        face_entered = threading.Event()
        recognition_entered = threading.Event()
        optional_entered = []
        def face():
            face_entered.set()
            gate.wait(1)
            return True
        def recognition():
            recognition_entered.set()
            gate.wait(1)
            return True
        recognizer._ensure_detector.side_effect = face
        recognizer._ensure_recognition.side_effect = recognition
        detector._ensure_model.side_effect = lambda: optional_entered.append("body") or object()
        bundle.start()
        try:
            self.assertTrue(face_entered.wait(1))
            self.assertTrue(recognition_entered.wait(1))
            time.sleep(.03)
            self.assertEqual(optional_entered, [])
            self.assertLessEqual(len(bundle.readiness._running), 2)
        finally:
            gate.set()
        wait_until(lambda: all(s.state in ("ready", "failed")
                               for s in bundle.readiness.snapshot().values()))
        self.assertTrue(optional_entered)

    def test_stalled_optional_owner_cannot_be_retried_concurrently(self):
        bundle, _, detector, _, _ = self.make_bundle()
        entered, release = threading.Event(), threading.Event()
        detector._ensure_model.side_effect = lambda: (entered.set(), release.wait(1), object())[2]
        # Make this the only queued model so the synchronization tests ownership itself.
        bundle.readiness._status.update({name: type(status)("ready")
                                         for name, status in bundle.readiness.snapshot().items()
                                         if name != "body"})
        bundle.readiness.timeout = .01
        bundle.start()
        try:
            self.assertTrue(entered.wait(1))
            wait_until(lambda: bundle.readiness.snapshot()["body"].state == "stalled")
            bundle.start(retry=True)
            self.assertEqual(detector._ensure_model.call_count, 1)
        finally:
            release.set()
        wait_until(lambda: bundle.readiness.ready("body"))

    def test_classifier_warms_without_optional_colour_reference(self):
        bundle, _, _, trainer, matcher = self.make_bundle()
        matcher.is_loaded.return_value = False
        matcher.load.return_value = False
        self.assertTrue(bundle._load_uniform())
        trainer.predict_proba.assert_called_once()
        matcher.is_uniform.assert_not_called()

    def test_second_default_bundle_construction_reuses_original_resources(self):
        bundle, recognizer, detector, _, _ = self.make_bundle()
        second = MonitorModels(recognizer, detector)
        self.assertIs(second, bundle)
        self.assertIs(second.trainer, bundle.trainer)
        self.assertIs(second.uniform_matcher, bundle.uniform_matcher)
        self.assertIs(second.readiness, bundle.readiness)

    def test_shutdown_cancels_active_loader_successors(self):
        bundle, _, detector, trainer, _ = self.make_bundle()
        registry = bundle.readiness
        registry.max_concurrent = 1
        with registry._lock:
            for name in ("face", "recognition"):
                registry._status[name] = type(registry._status[name])("ready")
        entered, release = threading.Event(), threading.Event()
        detector._ensure_model.side_effect = lambda: (entered.set(), release.wait(1), object())[2]
        bundle.start()
        self.assertTrue(entered.wait(1))
        bundle.cancel_pending()
        release.set()
        bundle.wait(1)
        self.assertEqual(registry.snapshot()["body"].state, "ready")
        self.assertEqual(registry.snapshot()["uniform"].state, "cancelled")
        self.assertEqual(registry.snapshot()["earring"].state, "cancelled")
        trainer.predict_proba.assert_not_called()

    def test_retry_queues_every_failure_and_prioritizes_face_when_slot_opens(self):
        from core.model_readiness import ModelReadiness
        registry = ModelReadiness(max_concurrent=2)
        optional_gate, optional_entered, release_optional = threading.Event(), threading.Event(), threading.Event()
        attempts = {"face": 0, "body": 0, "uniform": 0}
        def loader(name):
            def run():
                attempts[name] += 1
                if attempts[name] == 1:
                    raise RuntimeError("initial failure")
                if name == "body" and optional_gate.is_set():
                    optional_entered.set()
                    release_optional.wait(1)
                return True
            return run
        # First run all fail; then retry with two occupied/released slots to prove
        # failures beyond capacity remain queued and face is selected first.
        registry.add("face", loader("face"), priority=0)
        registry.add("body", loader("body"), priority=10)
        registry.add("uniform", loader("uniform"), priority=11)
        registry.start()
        wait_until(lambda: all(status.state == "failed" for status in registry.snapshot().values()))
        optional_gate.set()
        registry.start(retry=True)
        wait_until(lambda: registry.ready("face") and registry.ready("uniform")
                   and registry.ready("body"))
        self.assertEqual(attempts, {"face": 2, "body": 2, "uniform": 2})

    def test_thread_start_failure_is_a_failed_component_not_permanent_loading(self):
        from core.model_readiness import ModelReadiness
        registry = ModelReadiness(max_concurrent=1)
        registry.add("face", lambda: True, priority=0)
        with unittest.mock.patch("threading.Thread.start", side_effect=RuntimeError("thread denied")):
            registry.start()
        self.assertEqual(registry.snapshot()["face"].state, "failed")
        self.assertFalse(registry._running)

    def test_missing_optional_weights_are_explicit_failures(self):
        bundle, _, _, trainer, _ = self.make_bundle()
        trainer.is_trained.side_effect = lambda module: False
        with self.assertRaisesRegex(RuntimeError, "No trained earring weights"):
            bundle._load_earring()

    def test_shutdown_wait_does_not_start_preparation_after_login_abort(self):
        bundle, recognizer, detector, _, _ = self.make_bundle()
        bundle.wait(.01)
        recognizer._ensure_detector.assert_not_called()
        recognizer._ensure_recognition.assert_not_called()
        detector._ensure_model.assert_not_called()
        self.assertTrue(all(status.state == "waiting"
                            for status in bundle.readiness.snapshot().values()))

    def test_dashboard_entrypoint_accepts_shared_bundle_and_standalone_defaults(self):
        from ui import dashboard as dashboard_module
        def fake_app():
            app = MagicMock()
            app._live_worker.attendance_writer = None
            app._live_worker.processor.security_writer = None
            app._logout_requested = False
            return app
        bundle, recognizer, detector, _, _ = self.make_bundle()
        app = fake_app()
        with unittest.mock.patch.object(dashboard_module, "CBVMSDashboard", return_value=app) as ctor:
            self.assertFalse(dashboard_module.open_dashboard(
                "admin", recognizer=recognizer, person_detector=detector,
                monitor_models=bundle))
            self.assertIs(ctor.call_args.kwargs["monitor_models"], bundle)
        app = fake_app()
        with unittest.mock.patch.object(dashboard_module, "CBVMSDashboard", return_value=app) as ctor:
            self.assertFalse(dashboard_module.open_dashboard())
            self.assertIsNone(ctor.call_args.kwargs["monitor_models"])


if __name__ == "__main__":
    unittest.main()
