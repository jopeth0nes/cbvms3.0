"""Regression for same-frame handoff in the isolated timing harness."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from core.live_pipeline import MonitorTask, TrackedFace
from core.live_state import FrameContext
from scripts.profile_uniform_pipeline import assessment_task


class ProfileHandoffTests(unittest.TestCase):
    def test_face_landmarks_and_torso_reach_assessment_without_redetection(self):
        task = MonitorTask(FrameContext(1, ('fixture', 1), 100.),
            np.zeros((80, 80, 3), dtype=np.uint8), 100., threading.Event(),
            observation_only=True)
        observations = (TrackedFace((10, 2, 30, 20), .99, ((15, 8),), 1,
            'person:1', 0, (0, 0, 50, 80), (5, 22, 45, 65)),)
        session = Mock()
        session.observe.side_effect = lambda annotated, faces: annotated
        result = assessment_task(session, SimpleNamespace(task=task, observations=observations))
        self.assertEqual(result.observations, observations)
        self.assertIs(result.frame, task.frame)
        self.assertIsNone(task.observations)
        self.assertIs(result.cancelled, task.cancelled)
        self.assertEqual(result.context, task.context)
        session.observe.assert_called_once_with(result, observations)


if __name__ == '__main__':
    unittest.main()
