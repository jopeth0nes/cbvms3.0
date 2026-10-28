import unittest
import threading
from types import SimpleNamespace

from scripts.replay_camera_assessment import ReplayMetrics
from core.recognizer import FaceRecognizer


class CameraReplayMetricsTests(unittest.TestCase):
    def observation(self, box):
        return SimpleNamespace(box=box)

    def assessment(self, student_id=None, reliable=False, uniform="unassessed"):
        return SimpleNamespace(student_id=student_id, reliable_identity=reliable,
            uniform_assessment=SimpleNamespace(value=uniform))

    def test_prior_frame_labels_are_joined_to_selected_frame_and_first_confirmation_sticks(self):
        metrics = ReplayMetrics({"sid-a": "alias-a", "sid-b": "alias-b"})
        prior = [{"passage_alias": "alias-a", "face_box": [10, 10, 30, 30],
                  "identity_label": "alias-a", "uniform_label": "correct_uniform"}]
        other = [{"passage_alias": "alias-b", "face_box": [100, 10, 120, 30],
                  "identity_label": "alias-b", "uniform_label": "correct_uniform"}]
        metrics.observe_labels(10, 333.3, prior)
        metrics.observe_labels(11, 366.7, other)
        metrics.record_usable(10, 333.3, prior, [self.observation((10, 10, 30, 30))])
        metrics.record_assessments(10, 333.3, prior, [self.observation((10, 10, 30, 30))],
                                   [self.assessment("sid-a", True, "correct_uniform")])
        metrics.record_assessments(10, 333.3, prior, [self.observation((10, 10, 30, 30))],
                                   [self.assessment("sid-b", True, "correct_uniform")])
        output = metrics.report()
        self.assertEqual(output["confirmed_correct_passages"], 1)
        self.assertEqual(output["wrong_identity_passages"], 1)
        self.assertAlmostEqual(output["passage_entry_to_correct_confirmation_ms"][0], 0)
        self.assertEqual(output["passage_count"], 2)

    def test_missing_authorized_identity_labels_produce_null_accuracy(self):
        metrics = ReplayMetrics()
        people = [{"passage_alias": "p", "face_box": [0, 0, 20, 20],
                   "identity_label": "expected"}]
        metrics.observe_labels(0, 0, people)
        metrics.record_assessments(0, 0, people, [self.observation((0, 0, 20, 20))],
                                   [self.assessment("raw-id", True)])
        report = metrics.report()
        self.assertIsNone(report["identity_accuracy"])
        self.assertIsNone(report["uniform_accuracy"])
        self.assertIsNotNone(report["identity_accuracy_reason"])
        self.assertEqual(report["identity_observations"]["unscorable"], 1)

    def test_detection_miss_is_counted_even_without_assessment(self):
        metrics = ReplayMetrics()
        people = [{"passage_alias": "missed", "face_box": [0, 0, 20, 20]}]
        metrics.observe_labels(1, 33, people)
        metrics.record_detections(1, 33, people, [])
        self.assertEqual(metrics.report()["missed_passages"], 1)

    def test_gallery_reload_advances_revision_and_notifies_candidate_invalidator(self):
        recognizer = FaceRecognizer.__new__(FaceRecognizer)
        recognizer._lock = threading.RLock()
        recognizer._db = SimpleNamespace(get_all_students=lambda: [],
                                         recognition_capture_encodings=lambda: {})
        recognizer._known = []
        recognizer._gallery_listeners = []
        recognizer.gallery_revision = 0
        revisions = []
        recognizer.add_gallery_listener(revisions.append)
        recognizer.load_known_faces()
        self.assertEqual(recognizer.gallery_revision, 1)
        self.assertEqual(revisions, [1])

    def test_gallery_listener_can_be_removed_when_monitor_closes(self):
        recognizer = FaceRecognizer.__new__(FaceRecognizer)
        recognizer._lock = threading.RLock()
        recognizer._db = SimpleNamespace(get_all_students=lambda: [],
                                         recognition_capture_encodings=lambda: {})
        recognizer._known = []
        recognizer._gallery_listeners = []
        recognizer.gallery_revision = 0
        revisions = []
        listener = revisions.append
        recognizer.add_gallery_listener(listener)
        recognizer.remove_gallery_listener(listener)
        recognizer.load_known_faces()
        self.assertEqual(revisions, [])

    def test_wrong_uniform_is_not_erased_by_later_correct_and_requires_correct_identity(self):
        metrics = ReplayMetrics({"sid-a": "alias-a", "sid-b": "alias-b"})
        people = [{"passage_alias": "alias-a", "face_box": [10, 10, 30, 30],
                   "identity_label": "alias-a", "uniform_label": "correct_uniform"}]
        observations = [self.observation((10, 10, 30, 30))]
        metrics.observe_labels(0, 0, people)
        metrics.record_usable(0, 0, people, observations)
        metrics.record_assessments(0, 0, people, observations,
            [self.assessment("sid-a", True, "wrong")], completion_ms=100)
        metrics.record_assessments(0, 0, people, observations,
            [self.assessment("sid-a", True, "correct")], completion_ms=200)
        metrics.record_assessments(0, 0, people, observations,
            [self.assessment("sid-b", True, "wrong")], completion_ms=300)
        report = metrics.report()
        self.assertEqual(report["false_uniform_violations"], 1)
        self.assertEqual(report["missed_uniform_violations"], 0)
        self.assertEqual(report["passage_entry_to_correct_confirmation_ms"], [100.0])
        self.assertEqual(report["passage_entry_to_joint_identity_uniform_confirmation_ms"], [200.0])
        self.assertEqual(report["wrong_identity_passages"], 1)


if __name__ == "__main__":
    unittest.main()
