"""Identity evidence and shared-model safety without a camera or downloaded model."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from core.recognizer import FaceRecognizer


class IdentityAssignmentTests(unittest.TestCase):
    def assign(self, rows):
        return FaceRecognizer._assign_identities(np.asarray(rows, dtype=np.float32), .5)

    def test_losing_claim_never_becomes_second_available_student(self):
        self.assertEqual(self.assign([[.10, .30], [.20, .40]]), [0, -1])

    def test_close_competing_claims_both_remain_unknown(self):
        self.assertEqual(self.assign([[.20, .80], [.22, .70]]), [-1, -1])

    def test_clear_winner_only_and_order_independent(self):
        rows = [[.10, .80], [.25, .80], [.35, .80]]
        self.assertEqual(self.assign(rows), [0, -1, -1])
        self.assertEqual(self.assign(rows[::-1]), [-1, -1, 0])

    def test_close_student_distances_are_uncertain_even_below_threshold(self):
        self.assertEqual(self.assign([[.20, .23]]), [-1])

    def test_genuine_unique_students_and_unknown_preserved(self):
        self.assertEqual(self.assign([[.10, .80], [.80, .20], [.95, .90]]), [0, 1, -1])

    def test_no_identity_is_forced_at_or_above_absolute_threshold(self):
        self.assertEqual(self.assign([[.50, .90], [.70, .90]]), [-1, -1])

    def test_invalid_evidence_cannot_match(self):
        self.assertEqual(self.assign([[np.nan, .20], [np.inf, .10]]), [-1, -1])

    def test_empty_gallery_or_frame(self):
        for shape, expected in (((0, 2), []), ((2, 0), [-1, -1])):
            self.assertEqual(FaceRecognizer._assign_identities(np.empty(shape), .5), expected)

    def test_ambiguity_margin_is_configurable(self):
        matrix = np.array([[.20, .23]])
        self.assertEqual(FaceRecognizer._assign_identities(matrix, .5, .02), [0])
        self.assertEqual(FaceRecognizer._assign_identities(matrix, .5, .04), [-1])


class RecognitionEvidenceTests(unittest.TestCase):
    def recognizer(self):
        db = MagicMock()
        db.get_all_students.return_value = []
        recognizer = FaceRecognizer(db)
        recognizer._models_loaded = True
        recognizer._known = [
            ({"name": "One", "student_id": "S1", "gender": "Female"}, np.array([[1., 0.]])),
            ({"name": "Two", "student_id": "S2", "gender": "Male"}, np.array([[0., 1.]])),
        ]
        return recognizer

    def test_evidence_normalized_immutable_and_independent_of_input(self):
        recognizer = self.recognizer()
        embedding = np.array([3., 0.], np.float32)
        box = [10, 20, 60, 80]
        with patch.object(recognizer, "_detect", return_value=[(box, embedding, .9, "F")]):
            result = recognizer.recognize_faces(np.zeros((100, 100, 3), np.uint8))[0]
        embedding[:] = 0
        box[:] = [0, 0, 0, 0]
        self.assertEqual(result["embedding"], (1., 0.))
        self.assertIsInstance(result["embedding"], tuple)
        self.assertEqual(result["box"], [10, 20, 60, 80])
        self.assertEqual(result["student_id"], "S1")
        self.assertAlmostEqual(result["match_distance"], 0.)
        self.assertFalse(result["identity_uncertain"])

    def test_unknown_and_ambiguous_have_distinct_states(self):
        recognizer = self.recognizer()
        faces = [([i, 0, i+10, 10], np.array([1., 0.]), .9, "M") for i in range(3)]
        with patch.object(recognizer, "_detect", return_value=faces), patch.object(
            recognizer, "_min_distance_to_student", side_effect=[.10, .70, .20, .40, .90, .80]
        ):
            results = recognizer.recognize_faces(np.zeros((100, 100, 3), np.uint8))
        self.assertEqual([r["student_id"] for r in results], ["S1", "", ""])
        self.assertEqual([r["identity_uncertain"] for r in results], [False, True, False])
        self.assertEqual([r["matched"] for r in results], [True, False, False])
        self.assertEqual(results[1]["name"], "Unknown")
        self.assertEqual(results[2]["gender"], "Male")

    def test_close_gallery_match_is_uncertain(self):
        recognizer = self.recognizer()
        face = ([10, 20, 60, 80], np.array([1., 0.]), .9, "F")
        with patch.object(recognizer, "_detect", return_value=[face]), patch.object(
            recognizer, "_min_distance_to_student", side_effect=[.20, .22]
        ):
            result = recognizer.recognize_faces(np.zeros((100, 100, 3), np.uint8))[0]
        self.assertTrue(result["identity_uncertain"])
        self.assertFalse(result["matched"])

    def test_invalid_embeddings_are_never_known(self):
        recognizer = self.recognizer()
        for embedding in (np.zeros(2), np.array([np.nan, 1.]), np.array([np.inf, 1.])):
            face = ([10, 20, 60, 80], embedding, .9, "F")
            with patch.object(recognizer, "_detect", return_value=[face]):
                result = recognizer.recognize_faces(np.zeros((100, 100, 3), np.uint8))[0]
            self.assertEqual(result["embedding"], ())
            self.assertIsNone(result["match_distance"])
            self.assertFalse(result["matched"])
            self.assertTrue(result["identity_uncertain"])

    def test_no_gallery_preserves_unknown_embedding_for_tracking(self):
        recognizer = self.recognizer()
        recognizer._known = []
        face = ([10, 20, 60, 80], np.array([1., 0.]), .9, "M")
        with patch.object(recognizer, "_detect", return_value=[face]):
            result = recognizer.recognize_faces(np.zeros((100, 100, 3), np.uint8))[0]
        self.assertEqual(result["embedding"], (1., 0.))
        self.assertIsNone(result["match_distance"])
        self.assertFalse(result["matched"])
        self.assertFalse(result["identity_uncertain"])


class SharedModelTests(unittest.TestCase):
    def test_detection_recognition_and_enrollment_inference_do_not_overlap(self):
        recognizer = RecognitionEvidenceTests().recognizer()
        counts = {"active": 0, "max_active": 0, "calls": 0}
        lock = threading.Lock()

        def inference(detection_only=False):
            with lock:
                counts["active"] += 1
                counts["calls"] += 1
                counts["max_active"] = max(counts["max_active"], counts["active"])
            time.sleep(.01)
            with lock:
                counts["active"] -= 1
            if detection_only:
                return np.array([[10, 20, 60, 80, .9]]), None
            return [SimpleNamespace(bbox=np.array([10, 20, 60, 80]),
                                    normed_embedding=np.array([1., 0.]), det_score=.9, sex="F")]

        recognizer._app = SimpleNamespace(
            get=lambda _frame: inference(),
            det_model=SimpleNamespace(detect=lambda _frame, **_kwargs: inference(True)),
        )
        frame = np.zeros((100, 100, 3), np.uint8)
        methods = [recognizer.detect_faces, recognizer.recognize_faces,
                   recognizer.enrollment_faces, recognizer.encode_face] * 3
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(method, frame) for method in methods]
            for future in futures:
                self.assertTrue(future.result(timeout=2))
        self.assertEqual(counts["calls"], 12)
        self.assertEqual(counts["max_active"], 1)

    def test_concurrent_first_load_finishes_once_without_lock_deadlock(self):
        recognizer = RecognitionEvidenceTests().recognizer()
        recognizer._models_loaded = False
        recognizer._app = SimpleNamespace(
            get=lambda _frame: [],
            det_model=SimpleNamespace(detect=lambda _frame, **_kwargs: (np.empty((0, 5)), None)),
        )

        def load():
            time.sleep(.02)
            recognizer._models_loaded = True
            return True

        frame = np.zeros((100, 100, 3), np.uint8)
        with patch.object(recognizer, "_load_models", side_effect=load) as loader:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(method, frame) for method in (
                    recognizer.detect_faces, recognizer.recognize_faces, recognizer.enrollment_faces
                )]
                for future in futures:
                    self.assertEqual(future.result(timeout=2), [])
            loader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
