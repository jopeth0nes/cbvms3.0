"""Torso regression cases: close webcam framing, exposed arms, and ownership."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from core.live_state import UNIFORM_NOT_ASSESSED, validate_torso
from core.person_detector import PersonDetector, MAX_TORSO_SKIN_FRACTION, skin_fraction
from tests.test_live_pipeline import PipelineFixture


class TorsoRegionTests(unittest.TestCase):
    def setUp(self):
        self.detector = PersonDetector()
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.face = [484, 30, 768, 421]
        self.body = [190, 0, 1080, 720]

    def test_seated_sleeveless_shirt_excludes_neck_and_bare_arms(self):
        self.frame[:, 190:1080] = (90, 135, 185)  # exposed skin
        cv2.fillPoly(self.frame, [np.array([
            [405, 465], [470, 450], [510, 630], [745, 630],
            [785, 450], [850, 465], [925, 720], [330, 720],
        ])], (230, 230, 230))
        old_crop = self.frame[460:720, 190:1080]
        self.assertGreater(skin_fraction(old_crop), MAX_TORSO_SKIN_FRACTION)
        region, _ = self.detector.chest_region(self.frame, self.face, self.body)
        valid, reason = validate_torso(self.face, self.body, region, self.frame.shape)
        self.assertTrue(valid, reason)
        x1, y1, x2, y2 = region
        self.assertLess(skin_fraction(self.frame[y1:y2, x1:x2]), MAX_TORSO_SKIN_FRACTION)

    def test_bright_white_fabric_counts_in_skin_ratio_denominator(self):
        crop = np.full((100, 100, 3), 255, np.uint8)
        crop[:20] = (90, 135, 185)
        self.assertAlmostEqual(skin_fraction(crop), .20)
        crop[:] = (90, 135, 185)
        self.assertGreater(skin_fraction(crop), MAX_TORSO_SKIN_FRACTION)

    def test_face_only_and_thin_visible_shirt_strip_abstain(self):
        for bottom in (440, 560, 640):
            frame = self.frame[:bottom]
            region, _ = self.detector.chest_region(frame, self.face, self.body)
            self.assertIsNone(region, bottom)

    def test_warm_white_shirt_shadows_do_not_count_as_skin(self):
        # BGR samples from the actual shirt, neck and arm in the webcam view.
        for colour in ((138, 151, 156), (146, 150, 160), (127, 139, 147)):
            crop = np.full((80, 80, 3), colour, np.uint8)
            self.assertEqual(skin_fraction(crop), 0., colour)
        for colour in ((67, 88, 126), (92, 119, 143)):
            crop = np.full((80, 80, 3), colour, np.uint8)
            self.assertGreater(skin_fraction(crop), MAX_TORSO_SKIN_FRACTION, colour)

    def test_visible_neckline_is_allowed_when_most_of_crop_is_clothing(self):
        crop = np.full((100, 100, 3), (127, 139, 147), np.uint8)
        crop[:45] = (67, 88, 126)
        self.assertLess(skin_fraction(crop), MAX_TORSO_SKIN_FRACTION)
        crop[:60] = (67, 88, 126)
        self.assertGreater(skin_fraction(crop), MAX_TORSO_SKIN_FRACTION)

    def test_full_body_crop_stops_above_legs_and_excludes_arms(self):
        face, body = [470, 50, 550, 150], [350, 20, 680, 710]
        region, _ = self.detector.chest_region(self.frame, face, body)
        self.assertTrue(validate_torso(face, body, region, self.frame.shape)[0])
        self.assertGreater(region[0], body[0])
        self.assertLess(region[2], body[2])
        self.assertLess(region[3], 450)

    def test_neighbour_overlap_still_rejects_clothing_region(self):
        region, _ = self.detector.chest_region(self.frame, self.face, self.body)
        self.assertFalse(validate_torso(
            self.face, self.body, region, self.frame.shape,
            other_bodies=[[600, 0, 1250, 720]])[0])

    def test_optional_pose_cannot_reintroduce_neck_or_arms(self):
        face_region, _ = self.detector.chest_region(self.frame, self.face, self.body)
        self.detector.use_pose = True
        with patch.object(self.detector, 'pose_torso_box', return_value=self.body):
            pose_region, _ = self.detector.chest_region(self.frame, self.face, self.body)
        self.assertEqual(pose_region, face_region)


class TorsoAssessmentTests(unittest.TestCase):
    def test_skin_abstention_keeps_visible_crop_but_never_adds_evidence(self):
        fx = PipelineFixture()
        for seq in range(1, 6):
            task = fx.task(seq)
            with patch('core.live_pipeline.time.monotonic', side_effect=lambda: fx.now), \
                    patch('core.live_pipeline.skin_fraction', return_value=.95):
                result = fx.processor.analyze(task)
        assessment = result.assessments[0]
        self.assertEqual(assessment.state, UNIFORM_NOT_ASSESSED)
        self.assertIsNotNone(assessment.torso_box)
        self.assertFalse(assessment.accepted_categories)
        self.assertIn('show more', assessment.reason)
        fx.trainer.predict_proba.assert_not_called()
        fx.persist(result)
        fx.database.log_violation.assert_not_called()

    def test_unavailable_classifier_keeps_validated_geometry(self):
        fx = PipelineFixture()
        fx.trainer.predict_proba.return_value = None
        for seq in range(1, 5):
            result = fx.analyze(seq)
        self.assertEqual(result.assessments[0].state, UNIFORM_NOT_ASSESSED)
        self.assertIsNotNone(result.assessments[0].torso_box)
        self.assertFalse(result.assessments[0].accepted_categories)

    def test_body_inference_failure_is_distinct_from_missing_body(self):
        fx = PipelineFixture()
        fx.detector.detect_persons.return_value = []
        fx.detector.last_error = 'inference failed'
        fx.analyze(1)
        assessment = fx.analyze(2).assessments[0]
        self.assertIn('Body detection failed', assessment.reason)
        self.assertEqual(assessment.state, UNIFORM_NOT_ASSESSED)
        self.assertIsNone(assessment.torso_box)

    def test_no_visible_shirt_explains_how_to_reframe(self):
        fx = PipelineFixture()
        fx.detector.chest_region.side_effect = None
        fx.detector.chest_region.return_value = (None, 'none')
        fx.analyze(1)
        assessment = fx.analyze(2).assessments[0]
        self.assertIn('Move back', assessment.reason)
        self.assertEqual(assessment.state, UNIFORM_NOT_ASSESSED)
        self.assertIsNone(assessment.torso_box)


if __name__ == '__main__':
    unittest.main()
