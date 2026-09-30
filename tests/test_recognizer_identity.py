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
    def test_recognition_uses_preview_scale_when_large_input_misses_close_face(self):
        r = RecognitionEvidenceTests().recognizer()
        r._app = object()
        keypoints = np.ones((1, 5, 2))
        detector = MagicMock(side_effect=[(np.empty((0, 5)), None),
                                         (np.array([[10, 20, 60, 80, .9]]), keypoints)])
        r._detector = SimpleNamespace(detect=detector)
        def embed(frame, face):
            np.testing.assert_array_equal(face.kps, keypoints[0])
            face.normed_embedding = np.array([1., 0.])
        r._recognition = SimpleNamespace(get=embed)
        with patch.dict('sys.modules', {'insightface.app.common': SimpleNamespace(Face=SimpleNamespace)}):
            faces = r._detect(np.zeros((100, 100, 3), np.uint8))
        self.assertEqual(len(faces), 1)
        self.assertEqual([c.kwargs['input_size'] for c in detector.call_args_list], [(640, 640), (320, 320)])

    def test_detection_remains_available_during_recognition_inference(self):
        r = RecognitionEvidenceTests().recognizer()
        entered, release = threading.Event(), threading.Event()
        def recognition(frame, face):
            entered.set()
            release.wait(2)
            face.normed_embedding = np.array([1., 0.])
        r._detector = SimpleNamespace(detect=lambda *a, **kw: (np.array([[10,20,60,80,.9]]), np.zeros((1,5,2))))
        r._app = SimpleNamespace(det_model=r._detector)
        r._recognition = SimpleNamespace(get=recognition)
        frame = np.zeros((100,100,3), np.uint8)
        with patch.dict("sys.modules", {"insightface.app.common": SimpleNamespace(Face=SimpleNamespace)}):
            worker = threading.Thread(target=r._detect, args=(frame,))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(len(r.detect_faces(frame)), 1)
            finally:
                release.set()
                worker.join(2)

    def test_concurrent_first_load_finishes_once_per_component(self):
        r = RecognitionEvidenceTests().recognizer()
        r._models_loaded = False
        counts = {"detection": 0, "recognition": 0}
        def load(filename, task):
            counts[task] += 1
            time.sleep(.01)
            return SimpleNamespace(detect=lambda *a, **kw: (np.empty((0,5)), None),
                                   get_feat=lambda frame: np.ones((1,512)))
        with patch.object(r, "_local_model", side_effect=load):
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda _: r._ensure_models(), range(12)))
        self.assertTrue(all(results))
        self.assertEqual(counts, {"detection": 1, "recognition": 1})


if __name__ == "__main__":
    unittest.main()
