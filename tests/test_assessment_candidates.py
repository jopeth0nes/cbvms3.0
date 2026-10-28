import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from core.assessment_candidates import ClearFrameRouter
from core.live_pipeline import LiveWorker, MonitorTask, TrackedFace
from core.live_state import FrameContext


def make_result(seq, *, presence="p1", torso=True, two=False, captured=None, generation=4):
    captured = time.monotonic() if captured is None else captured
    frame = np.random.default_rng(seq).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    rows = [TrackedFace((80, 20, 140, 80), .99, (), 1, presence, 0,
                        (50, 10, 170, 220) if torso else None,
                        (60, 90, 160, 170) if torso else None, "Checking")]
    if two:
        rows.append(TrackedFace((210, 20, 270, 80), .99, (), 2, "p2", 1,
                                (180, 10, 300, 220), (190, 90, 290, 170), "Checking"))
    task = MonitorTask(FrameContext(generation, ("session", seq), captured), frame, 1_800_000_000. + seq,
        threading.Event(), camera_generation=9, source_id="camera", source_label="Camera")
    return SimpleNamespace(task=task, observations=tuple(rows))


class ClearFrameRouterTests(unittest.TestCase):
    def test_unusable_visibility_wait_remains_in_passage_entry_latency(self):
        from dataclasses import replace
        router = ClearFrameRouter(fresh_selection=True)
        first = make_result(301)
        first.observations = (replace(first.observations[0], box=(-50,20,50,80)),)
        router.observe(first)
        self.assertIsNone(router.next())
        later = make_result(302)
        router.observe(later)
        candidate = router.next()
        self.assertEqual(candidate.task.passage_entered_at, first.task.context.captured_at)
        self.assertEqual(candidate.task.first_usable_at, later.task.context.captured_at)
        self.assertGreater(candidate.task.first_usable_at, candidate.task.passage_entered_at)

    def test_fresh_completion_discards_old_sibling_work_without_cancelling_new_frame(self):
        router = ClearFrameRouter(fresh_selection=True, refresh_interval=0)
        for seq in (201, 202):
            router.observe(make_result(seq, two=True), select=False)
        candidate = router.next()
        self.assertEqual(candidate.task.context.frame_id[1], 202)
        router.observe(make_result(203, two=True), select=False)
        self.assertTrue(candidate.task.valid())
        router.complete(candidate.ticket_id, object(), 'completed')
        following = router.next()
        self.assertEqual(following.task.context.frame_id[1], 203)
        router.complete(following.ticket_id, object(), 'completed')
        self.assertIsNone(router.next())

    def test_fresh_selection_replaces_equal_quality_waiting_frames(self):
        router = ClearFrameRouter(fresh_selection=True, refresh_interval=0)
        first = make_result(101)
        # Equal pixels/quality, distinct captured observations. This is a queue
        # freshness test, not additional independent recognition evidence.
        later = make_result(102)
        later.task = MonitorTask(later.task.context, first.task.frame,
            later.task.observed_at, threading.Event(), camera_generation=9,
            source_id="camera")
        with patch.object(router, '_quality', return_value=(.8, (80,20,140,80),
                          (50,10,170,220), (60,90,160,170), {})):
            router.observe(first)
            router.observe(later)
        candidate = router.next()
        self.assertEqual(candidate.task.context.frame_id, later.task.context.frame_id)
        router.complete(candidate.ticket_id, object(), "completed")
        self.assertIsNone(router.next(), "older evidence must not follow newer evidence")

    def test_dispatch_requires_completion_and_preserves_original_context(self):
        result = make_result(1)
        router = ClearFrameRouter()
        router.observe(result)
        candidate = router.next()
        self.assertTrue(candidate.task.valid())
        self.assertIs(candidate.task.frame, result.task.frame)
        self.assertEqual(candidate.task.context.frame_id, result.task.context.frame_id)
        self.assertEqual(candidate.task.observed_at, result.task.observed_at)
        self.assertEqual(candidate.task.source_id, "camera")
        self.assertEqual(router.next(), None)
        self.assertTrue(router.complete(candidate.ticket_id, object(), "completed"))
        self.assertTrue(candidate.task.valid())
        self.assertFalse(router.complete(candidate.ticket_id, object(), "completed"))
        gone = make_result(14)
        gone.observations = ()
        router.observe(gone)
        self.assertFalse(candidate.task.valid())

    def test_same_immutable_frame_never_supplies_second_confirmation(self):
        result = make_result(2)
        router = ClearFrameRouter()
        router.observe(result)
        router.observe(result)
        candidate = router.next()
        self.assertIsNotNone(candidate)
        router.complete(candidate.ticket_id, object(), "completed")
        self.assertIsNone(router.next())
        self.assertEqual(router.stats()["candidate_count"], 0)

    def test_face_only_candidate_remains_identity_usable_without_torso(self):
        router = ClearFrameRouter()
        router.observe(make_result(3, torso=False))
        candidate = router.next()
        self.assertIsNotNone(candidate)
        self.assertIsNone(candidate.torso_box)
        self.assertIsNone(candidate.body_box)

    def test_disappearance_cancels_dispatched_candidate_without_session_cancel(self):
        router = ClearFrameRouter()
        router.observe(make_result(4))
        candidate = router.next()
        gone = make_result(5)
        gone.observations = ()
        router.observe(gone)
        self.assertTrue(candidate.cancelled.is_set())
        self.assertFalse(candidate.task.cancelled.is_set())
        self.assertFalse(candidate.task.valid())
        router.complete(candidate.ticket_id, None, "passage_invalidated")
        self.assertEqual(router.stats()["retained_bytes"], 0)

    def test_per_passage_and_global_byte_bounds_replace_and_expire(self):
        frame_size = 320 * 240 * 3
        router = ClearFrameRouter(per_passage=1, max_candidates=8, max_bytes=frame_size,
                                  max_queue=8, ttl=.5)
        first, second = make_result(6, two=True), make_result(7, two=True)
        router.observe(first, select=False)
        router.observe(second, select=False)
        stats = router.stats()
        self.assertLessEqual(stats["candidate_count"], 2)
        self.assertLessEqual(stats["retained_bytes"], frame_size)
        self.assertLessEqual(stats["candidate_count"], 2 * router.per_passage)
        # Every candidate expires by its own capture time; newest is not a proxy.
        expired = router.next(now=second.task.context.captured_at + 1)
        self.assertIsNone(expired)
        self.assertEqual(router.stats()["candidate_count"], 0)
        self.assertEqual(router.stats()["retained_bytes"], 0)

    def test_gallery_generation_change_cancels_queued_ticket(self):
        router = ClearFrameRouter()
        router.observe(make_result(8))
        candidate = router.next()
        router.observe(make_result(9, generation=5))
        self.assertTrue(candidate.cancelled.is_set())
        self.assertEqual(router.stats()["queued"], 1)  # the new generation may offer fresh work

    def test_generation_and_missing_observations_invalidate_completed_results(self):
        router = ClearFrameRouter()
        router.observe(make_result(80))
        completed = router.next()
        router.complete(completed.ticket_id, object(), "completed")
        self.assertTrue(completed.task.valid())
        router.observe(make_result(81, generation=5))
        self.assertFalse(completed.task.valid())
        current = router.next()
        router.complete(current.ticket_id, object(), "completed")
        absent = make_result(82, generation=5)
        absent.observations = None
        router.observe(absent)
        self.assertFalse(current.task.valid())

    def test_gallery_reload_reset_invalidates_pending_and_dispatched_passages(self):
        router = ClearFrameRouter()
        router.observe(make_result(12))
        dispatched = router.next()
        router.observe(make_result(13, presence="p2"))
        router.reset()
        self.assertTrue(dispatched.cancelled.is_set())
        self.assertEqual(router.stats()["queued"], 0)
        self.assertEqual(router.stats()["retained_bytes"], 0)

    def test_active_candidate_is_counted_and_never_evicted_by_new_frame_pressure(self):
        frame_size = 320 * 240 * 3
        router = ClearFrameRouter(per_passage=1, max_candidates=1,
            max_bytes=frame_size, max_queue=1, refresh_interval=0)
        router.observe(make_result(20))
        active = router.next()
        router.observe(make_result(21))
        self.assertFalse(active.cancelled.is_set())
        self.assertTrue(active.task.valid())
        self.assertLessEqual(router.stats()["candidate_count"], 1)
        self.assertLessEqual(router.stats()["retained_bytes"], frame_size)

    def test_overlapping_torso_is_sanitized_before_real_processor_assessment(self):
        from dataclasses import replace
        from tests.test_live_pipeline import PipelineFixture, detection
        local = make_result(24, two=True)
        first, second = local.observations
        local.observations = (first, replace(second, torso_box=(156, 90, 256, 170)))
        router = ClearFrameRouter()
        router.observe(local)
        candidate = router.next()
        self.assertEqual(candidate.presence_id, "p1")
        self.assertIsNone(candidate.task.observations[0].torso_box)
        self.assertIsNone(candidate.task.observations[1].torso_box)
        fixture = PipelineFixture()
        fixture.processor.observation_only = True
        fixture.recognizer.recognize_faces.side_effect = lambda frame, detections: [
            dict(detection(sid="", x=det["box"][0]), box=det["box"], matched=False,
                 student_id="", name="Unknown", gender="") for det in detections]
        result = fixture.processor.analyze(candidate.task)
        self.assertIsNotNone(result)
        self.assertIsNone(result.assessments[0].torso_box)
        router.complete(candidate.ticket_id, result)

    def test_torso_loss_invalidates_completed_geometry_and_restarts_face_only_token(self):
        router = ClearFrameRouter(refresh_interval=0)
        router.observe(make_result(83))
        completed = router.next()
        router.complete(completed.ticket_id, object(), "completed")
        self.assertTrue(completed.task.valid())
        router.observe(make_result(84, torso=False))
        self.assertFalse(completed.task.valid())
        face_only = router.next()
        self.assertIsNotNone(face_only)
        self.assertTrue(face_only.task.valid())
        self.assertIsNone(face_only.torso_box)

    def test_target_priority_reorders_actual_recognition_and_keeps_owner_mapping(self):
        from tests.test_live_pipeline import PipelineFixture, detection
        local = make_result(25, two=True)
        task = local.task.for_observation_candidate(local.observations, "p2",
            time.monotonic(), "priority-ticket", threading.Event(), threading.Event())
        fixture = PipelineFixture()
        fixture.processor.observation_only = True
        fixture.recognizer.recognize_faces.side_effect = lambda frame, detections: [
            dict(detection(sid="", x=det["box"][0]), box=det["box"], matched=False,
                 student_id="", name="Unknown", gender="") for det in detections]
        result = fixture.processor.analyze(task)
        detection_boxes = [row["box"] for row in fixture.recognizer.recognize_faces.call_args.kwargs["detections"]]
        self.assertEqual(detection_boxes[0], local.observations[1].box)
        self.assertEqual([row.presence_id for row in result.task.observations], ["p1", "p2"])
        self.assertEqual([row.presence_id for row in result.assessments], ["p1", "p2"])

    def test_first_passage_latencies_emit_once_but_compute_latency_emits_each_time(self):
        from core.performance import PerformanceMetrics
        router = ClearFrameRouter(refresh_interval=0)
        metrics = PerformanceMetrics(enabled=True)
        with patch("core.assessment_candidates.PERF", metrics):
            for seq in (22, 23):
                router.observe(make_result(seq))
                candidate = router.next()
                router.complete(candidate.ticket_id, object(), "completed")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["passage_entry_to_assessment"]["count"], 1)
        self.assertEqual(snapshot["passage_entry_to_first_assessment"]["count"], 1)
        self.assertEqual(snapshot["usable_to_first_assessment"]["count"], 1)
        self.assertEqual(snapshot["candidate_to_assessment"]["count"], 2)

    def test_worker_acknowledges_candidate_on_success_and_error(self):
        class Processor:
            def __init__(self, fail=False): self.fail, self.started = fail, threading.Event()
            def analyze(self, task):
                self.started.set()
                if self.fail: raise RuntimeError("test")
                return SimpleNamespace(task=task)
        for fail, expected in ((False, "completed"), (True, "analysis_failed")):
            processor = Processor(fail)
            worker = LiveWorker(processor, observation_only=True)
            outcomes = []
            idle_states = []
            task = make_result(10 + int(fail)).task.for_observation_candidate(
                (), "p1", time.monotonic(), "ticket", threading.Event())
            from copy import copy
            task = copy(task)
            object.__setattr__(task, "candidate_on_complete",
                               lambda ticket, result, reason: outcomes.append((ticket, result, reason)))
            worker.start()
            worker.on_idle = lambda: idle_states.append(worker.active_task)
            self.assertTrue(worker.offer(task))
            self.assertTrue(processor.started.wait(2))
            deadline = time.monotonic() + 2
            while not outcomes and time.monotonic() < deadline: time.sleep(.01)
            worker.stop(); worker.done.wait(2)
            self.assertEqual(outcomes[0][0], "ticket")
            self.assertEqual(outcomes[0][2], expected)
            self.assertIn(None, idle_states)

    def test_worker_busy_and_stop_drops_ack_candidates(self):
        class Processor:
            observation_only = True
            def analyze(self, task): return object()
        worker = LiveWorker(Processor(), observation_only=True)
        outcomes = []
        task = make_result(30).task.for_observation_candidate(
            (), "p1", time.monotonic(), "busy", threading.Event())
        from copy import copy
        task = copy(task)
        object.__setattr__(task, "candidate_on_complete",
                           lambda ticket, result, reason: outcomes.append((ticket, result, reason)))
        worker.active_task = make_result(31).task
        self.assertFalse(worker.offer(task))
        self.assertEqual(outcomes[-1][2], "worker_busy")
        worker.active_task = None
        stopped = copy(task)
        object.__setattr__(stopped, "candidate_ticket_id", "stopped")
        object.__setattr__(stopped, "candidate_on_complete",
                           lambda ticket, result, reason: outcomes.append((ticket, result, reason)))
        self.assertTrue(worker.offer(stopped))
        worker.stop()
        self.assertEqual(outcomes[-1][0], "stopped")
        self.assertEqual(outcomes[-1][2], "worker_stopped")

    def test_expired_ticket_releases_worker_before_idle_dispatch(self):
        class Processor:
            def analyze(self, task):
                return SimpleNamespace(task=task)
        worker = LiveWorker(Processor(), observation_only=True)
        completed = threading.Event()
        offers = []
        from copy import copy
        expired = make_result(88, captured=time.monotonic()-4).task.for_observation_candidate(
            (), "p1", time.monotonic(), "expired", threading.Event())
        fresh = copy(make_result(89).task.for_observation_candidate(
            (), "p2", time.monotonic(), "fresh", threading.Event()))
        object.__setattr__(fresh, "candidate_on_complete",
            lambda ticket, result, reason: completed.set() if reason == "completed" else None)
        def on_idle():
            if not offers:
                offers.append(worker.offer(fresh))
        worker.on_idle = on_idle
        self.assertTrue(worker.offer(expired))
        worker.start()
        try:
            self.assertTrue(completed.wait(2))
            self.assertEqual(offers, [True])
        finally:
            worker.on_idle = None
            worker.stop()
            self.assertTrue(worker.done.wait(2))

    def test_overload_preserves_new_entrant_before_redundant_confirmation(self):
        router = ClearFrameRouter(max_queue=1, per_passage=2)
        quality = router._quality
        def equal_quality(*args):
            measured = quality(*args)
            return (.5,) + measured[1:]
        with patch.object(router, '_quality', side_effect=equal_quality):
            router.observe(make_result(92))
            first = router.next()
            router.complete(first.ticket_id, object())
            router.observe(make_result(93))
            router.observe(make_result(94, two=True))
        selected = router.next()
        self.assertEqual(selected.presence_id, 'p2')
        self.assertLessEqual(router.stats()['queued'], 1)

    def test_worker_dequeue_claim_is_atomic_against_candidate_offer(self):
        class SlowProcessor:
            observation_only = True
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()
            def analyze(self, task):
                self.started.set()
                self.release.wait(2)
                return SimpleNamespace(task=task)
        processor = SlowProcessor()
        worker = LiveWorker(processor, observation_only=True)
        outcomes = []
        from copy import copy
        first = copy(make_result(90).task.for_observation_candidate(
            (), "p1", time.monotonic(), "first", threading.Event()))
        object.__setattr__(first, "candidate_on_complete",
            lambda ticket, result, reason: outcomes.append((ticket, reason)))
        second = copy(make_result(91).task.for_observation_candidate(
            (), "p2", time.monotonic(), "second", threading.Event()))
        object.__setattr__(second, "candidate_on_complete",
            lambda ticket, result, reason: outcomes.append((ticket, reason)))
        worker.start()
        self.assertTrue(worker.offer(first))
        self.assertTrue(processor.started.wait(2))
        self.assertIsNotNone(worker.active_task)
        self.assertFalse(worker.offer(second))
        self.assertIn(("second", "worker_busy"), outcomes)
        processor.release.set()
        deadline = time.monotonic()+2
        while not any(ticket == "first" for ticket, _ in outcomes) and time.monotonic() < deadline:
            time.sleep(.01)
        worker.stop(); worker.done.wait(2)
        self.assertIn(("first", "completed"), outcomes)


if __name__ == "__main__":
    unittest.main()
