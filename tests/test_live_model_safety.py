"""Shared model concurrency without loading models, cameras, or real weights."""

from collections import Counter
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from core.person_detector import PersonDetector
from core.trainer import ViolationTrainer


class _ConcurrentCalls:
    def __init__(self, result):
        self.result = result
        self.active = 0
        self.maximum = 0
        self.count = 0
        self.lock = threading.Lock()

    def __call__(self, *_args, **_kwargs):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.count += 1
        try:
            # Yield while a model operation is active so simultaneous callers
            # would overlap without the instance lock.
            threading.Event().wait(.005)
            return self.result
        finally:
            with self.lock:
                self.active -= 1


class LiveModelSafetyTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((100, 100, 3), dtype=np.uint8)

    def run_concurrently(self, callbacks):
        barrier = threading.Barrier(len(callbacks) + 1)
        errors = []
        results = []

        def run(callback):
            try:
                barrier.wait(timeout=2)
                results.append(callback())
            except BaseException as exc:
                errors.append(exc)

        workers = [threading.Thread(target=run, args=(callback,), daemon=True)
                   for callback in callbacks]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
        self.assertFalse(any(worker.is_alive() for worker in workers), "model lock deadlocked")
        if errors:
            raise errors[0]
        return results

    @staticmethod
    def classification_result():
        return [SimpleNamespace(
            probs=SimpleNamespace(top1=0, top1conf=.9, data=np.array([.9, .1])),
            names={0: "correct_uniform", 1: "wrong_uniform"},
        )]

    def test_person_detection_and_pose_never_overlap_shared_model_calls(self):
        detector = PersonDetector()
        predict = _ConcurrentCalls([])
        detector._model = SimpleNamespace(predict=predict)
        detector._pose_model = SimpleNamespace(predict=predict)
        self.run_concurrently([
            lambda: detector.detect_persons(self.frame),
            lambda: detector.pose_torso_box(self.frame),
        ] * 6)
        self.assertEqual(predict.count, 12)
        self.assertEqual(predict.maximum, 1)

    def test_person_prewarm_and_inference_load_each_model_once(self):
        detector = PersonDetector()
        loads = Counter()
        model = SimpleNamespace(predict=lambda *_args, **_kwargs: [])

        def factory(path):
            loads[path] += 1
            threading.Event().wait(.005)
            return model

        with patch.dict("sys.modules", {"ultralytics": SimpleNamespace(YOLO=factory)}):
            self.run_concurrently([
                detector._ensure_model,
                detector._ensure_pose_model,
                lambda: detector.detect_persons(self.frame),
                lambda: detector.pose_torso_box(self.frame),
            ] * 3)
        self.assertEqual(loads, {detector._MODEL_PATH: 1, detector._POSE_MODEL_PATH: 1})

    def test_trainer_prewarm_and_both_prediction_apis_share_one_load(self):
        trainer = ViolationTrainer()
        predict = _ConcurrentCalls(self.classification_result())
        loads = []

        def factory(path):
            loads.append(path)
            threading.Event().wait(.005)
            return SimpleNamespace(predict=predict)

        with patch.dict("sys.modules", {"ultralytics": SimpleNamespace(YOLO=factory)}), \
                patch.object(trainer, "is_trained", return_value=True):
            self.run_concurrently([
                lambda: trainer._get_model("uniform"),
                lambda: trainer.predict("uniform", self.frame),
                lambda: trainer.predict_proba("uniform", self.frame),
            ] * 4)
        self.assertEqual(len(loads), 1)
        self.assertEqual(predict.count, 8)
        self.assertEqual(predict.maximum, 1)

    def test_evaluation_and_live_prediction_are_serialized(self):
        trainer = ViolationTrainer()
        predict = _ConcurrentCalls(self.classification_result())
        trainer._models["uniform"] = SimpleNamespace(predict=predict)
        with patch.object(trainer, "is_trained", return_value=True), \
                patch.object(trainer, "_held_out_files", return_value={
                    "correct_uniform": [Path("mock-1.jpg"), Path("mock-2.jpg")],
                    "wrong_uniform": [],
                }), patch("core.trainer.cv2.imread", return_value=self.frame):
            results = self.run_concurrently([
                lambda: trainer.evaluate("uniform", lambda _message: None),
                lambda: trainer.predict_proba("uniform", self.frame),
                lambda: trainer.predict("uniform", self.frame),
            ])
        evaluation = next(value for value in results if isinstance(value, dict) and "accuracy" in value)
        self.assertEqual(evaluation["accuracy"], 1.0)
        self.assertEqual(predict.count, 4)
        self.assertEqual(predict.maximum, 1)

    def test_publish_waits_for_inference_then_replaces_weights_and_cache(self):
        trainer = ViolationTrainer()
        entered = threading.Event()
        release = threading.Event()
        publish_started = threading.Event()
        published = threading.Event()
        errors = []

        def predict(*_args, **_kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test inference was not released")
            return self.classification_result()

        old_model = SimpleNamespace(predict=predict)
        trainer._models["uniform"] = old_model
        with tempfile.TemporaryDirectory(prefix="cbvms_model_lock_") as directory:
            root = Path(directory)
            target = root / "models" / "uniform_cls.pt"
            target.parent.mkdir()
            target.write_bytes(b"old weights")
            source = root / "best.pt"
            source.write_bytes(b"new weights")

            def publish():
                try:
                    publish_started.set()
                    trainer._publish_model("uniform", source)
                    published.set()
                except BaseException as exc:
                    errors.append(exc)

            with patch("core.trainer.ROOT", root):
                inference = threading.Thread(target=trainer.predict,
                                             args=("uniform", self.frame), daemon=True)
                publisher = threading.Thread(target=publish, daemon=True)
                inference.start()
                self.assertTrue(entered.wait(2))
                publisher.start()
                try:
                    self.assertTrue(publish_started.wait(2))
                    self.assertFalse(published.wait(.03))
                    self.assertEqual(target.read_bytes(), b"old weights")
                    self.assertIs(trainer._models["uniform"], old_model)
                finally:
                    release.set()
                    inference.join(2)
                    publisher.join(2)
                self.assertFalse(inference.is_alive() or publisher.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(published.is_set())
                self.assertEqual(target.read_bytes(), b"new weights")
                self.assertNotIn("uniform", trainer._models)
                self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_failed_model_copy_preserves_current_weights_and_cache(self):
        trainer = ViolationTrainer()
        old_model = object()
        trainer._models["uniform"] = old_model
        with tempfile.TemporaryDirectory(prefix="cbvms_model_copy_") as directory:
            root = Path(directory)
            target = root / "models" / "uniform_cls.pt"
            target.parent.mkdir()
            target.write_bytes(b"old weights")

            def fail_copy(_source, temporary):
                temporary.write_bytes(b"partial weights")
                raise OSError("copy interrupted")

            with patch("core.trainer.ROOT", root), patch("core.trainer.shutil.copy", side_effect=fail_copy):
                with self.assertRaisesRegex(OSError, "copy interrupted"):
                    trainer._publish_model("uniform", root / "best.pt")
            self.assertEqual(target.read_bytes(), b"old weights")
            self.assertIs(trainer._models["uniform"], old_model)
            self.assertEqual(list(target.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
