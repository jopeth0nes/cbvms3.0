"""Performance regressions that also protect frame ownership and state expiry."""
from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from core.live_pipeline import MonitorTask, TrackedFace
from core.live_state import FrameContext
from tests.test_live_pipeline import PipelineFixture
from core.performance import PerformanceMetrics


class MonitorPerformanceTests(unittest.TestCase):
    def test_localized_handoff_reuses_owned_pixels_and_cancellation(self):
        source = np.ones((720, 1280, 3), np.uint8)
        task = MonitorTask(FrameContext(1, ("synthetic", 1), time.monotonic()),
                           source, time.time(), threading.Event())
        observation = TrackedFace((10, 10, 40, 50), .9, (), 1, "synthetic:1")
        localized = task.with_observations((observation,))
        self.assertIsNot(localized, task)
        self.assertIs(localized.frame, task.frame)
        self.assertIs(localized.cancelled, task.cancelled)
        self.assertEqual(localized.context, task.context)
        self.assertIsNone(task.observations)
        source[:] = 0
        self.assertTrue(np.all(localized.frame == 1))
        with self.assertRaises(ValueError):
            localized.frame[0, 0] = 0
        task.cancelled.set()
        self.assertFalse(localized.valid())
        # Arbitrary construction/replacement must still take an owned snapshot.
        replacement = replace(task, frame=source)
        source[:] = 2
        self.assertTrue(np.all(replacement.frame == 0))

    def test_empty_scene_does_not_read_student_term_or_audio_settings(self):
        fx = PipelineFixture()
        fx.recognizer.recognize_faces.return_value = []
        result = fx.analyze(1)
        self.assertEqual(result.assessments, ())
        fx.database.attendance_term_snapshot.assert_not_called()
        fx.database.connect.assert_not_called()
        fx.database.get_student_by_student_id.assert_not_called()
        fx.database.enrollment_context.assert_not_called()
        fx.database.get_active_suspension.assert_not_called()

    def test_metrics_are_opt_in_bounded_and_reject_unapproved_fields(self):
        disabled = PerformanceMetrics()
        disabled.observe("tracking", .1)
        self.assertEqual(disabled.snapshot(), {})
        metrics = PerformanceMetrics(enabled=True, capacity=4)
        for value in range(10):
            metrics.observe("tracking", value/1000)
            metrics.observe("student-private-id", value)
        metrics.observe("tracking", float("nan"))
        metrics.observe("tracking", -1)
        metrics.observe("tracking", b"image")
        result = metrics.snapshot()
        self.assertEqual(set(result), {"tracking"})
        self.assertEqual(result["tracking"]["count"], 10)
        self.assertEqual(result["tracking"]["retained"], 4)
        self.assertEqual(result["tracking"]["p50_ms"], 7.5)
        self.assertEqual(result["tracking"]["p95_ms"], 8.85)
        self.assertEqual(result["tracking"]["p99_ms"], 8.97)

    def test_metrics_do_not_capture_task_or_identity_fields(self):
        metrics = PerformanceMetrics(enabled=True)
        with patch("core.diagnostics.PERF", metrics), patch("core.diagnostics.LOG"):
            from core.diagnostics import event
            event("stage_complete", stage="recognition", elapsed=.2,
                  student_id="PRIVATE", embedding=[1, 2], frame=b"PRIVATE_IMAGE")
        self.assertEqual(set(metrics.snapshot()), {"recognition"})
        self.assertNotIn("PRIVATE", str(metrics.snapshot()))
