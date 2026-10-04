"""Live Monitor orchestration tests with synthetic frames and model/DB doubles."""
from dataclasses import replace
from datetime import datetime, timezone
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from core.camera import CameraSample
from core.live_pipeline import LiveProcessor, LiveWorker, MonitorResult, MonitorTask, MotionProjection
from core.live_state import (FrameContext, CHECKING_UNIFORM,
                             SUSPECTED_VIOLATION, UNIFORM_COMPLIANT, UNIFORM_NOT_ASSESSED)


def detection(sid="S-1", x=80, *, embedding=0, **changes):
    vector = [0.] * 4
    vector[embedding] = 1.
    result = dict(box=[x, 20, x+50, 90], name=f"Student {sid}", student_id=sid,
                  gender="Male", matched=bool(sid), embedding=tuple(vector))
    result.update(changes)
    return result


class PipelineFixture:
    def __init__(self):
        self.now = 100.
        self.latest = None
        self.cancelled = threading.Event()
        self.database = MagicMock()
        self.database.get_student_by_student_id.side_effect = lambda sid: dict(
            student_id=sid, name=f"Student {sid}", student_status="Enrolled", registration_pending=False)
        self.database.get_active_suspension.return_value = None
        self.database.record_attendance.return_value = True
        self.database.record_premises_entry.return_value = True
        self.database.log_violation.return_value = 7
        self.database.log_security_event.return_value = 8
        self.recognizer = MagicMock()
        self.recognizer.recognize_faces.return_value = [detection()]
        self.detector = MagicMock()
        self.detector.detect_persons.return_value = [[50, 0, 160, 300]]
        self.detector.chest_region.side_effect = lambda frame, face, body: (
            [face[0]-15, 100, face[2]+15, 210], "synthetic")
        self.trainer = MagicMock()
        self.trainer.is_trained.side_effect = lambda module: module == "uniform"
        self.trainer.predict_proba.return_value = {"correct_uniform": .10}
        self.trainer.predict.return_value = ("no_earring", .9)
        self.matcher = MagicMock()
        self.matcher.is_loaded.return_value = False
        self.notifier = MagicMock()
        self.processor = LiveProcessor(self.database, self.recognizer, self.detector,
                                       self.trainer, self.matcher, self.notifier,
                                       latest_sample=lambda: self.latest)

    def task(self, seq, *, generation=1, cancelled=None, **changes):
        self.now = 100. + seq * .25
        frame = np.zeros((320, 640, 3), np.uint8)
        frame[:, :250] = (160, 50, 20)   # distinct left and right people/crop markers
        frame[:, 250:] = (20, 60, 170)
        frame[0, 0] = (seq, seq, seq)
        frame.setflags(write=False)
        task = MonitorTask(FrameContext(generation, ("camera", seq), self.now-.1), frame,
                           1_800_000_000. + seq, self.cancelled if cancelled is None else cancelled)
        task = replace(task, **changes)
        self.latest = CameraSample(task.frame, task.context.frame_id, task.context.captured_at)
        return task

    def analyze(self, seq, **changes):
        task = self.task(seq, **changes)
        with patch("core.live_pipeline.time.monotonic", side_effect=lambda: self.now), \
                patch("core.live_pipeline.skin_fraction", return_value=0.):
            return self.processor.analyze(task)

    def persist(self, result):
        with patch("core.live_pipeline.time.monotonic", side_effect=lambda: self.now):
            self.processor.persist(result)

    def confirmed(self):
        for seq in range(1, 5):
            result = self.analyze(seq)
        return result


class LivePipelineTests(unittest.TestCase):
    def test_monitor_task_owns_a_frozen_copy_of_captured_pixels(self):
        source = np.zeros((32, 48, 3), np.uint8)
        task = MonitorTask(FrameContext(1, ("camera", 1), 100.), source, 1000., threading.Event())
        source[:] = 255
        self.assertFalse(task.frame.flags.writeable)
        self.assertEqual(int(task.frame.sum()), 0)

    def test_correct_face_body_uniform_pipeline_produces_confirmed_assessment(self):
        fx = PipelineFixture()
        result = fx.confirmed()
        assessment = result.assessments[0]
        self.assertTrue(assessment.reliable_identity)
        self.assertEqual(assessment.student_id, "S-1")
        self.assertEqual(assessment.state, SUSPECTED_VIOLATION)
        self.assertEqual(assessment.accepted_categories, ("wrong_uniform",))
        self.assertEqual(assessment.torso_box, (65, 100, 145, 210))
        self.assertEqual(fx.trainer.predict_proba.call_count, 4)

    def test_every_person_gets_its_own_crop_and_uniform_result(self):
        fx = PipelineFixture()
        fx.recognizer.recognize_faces.return_value = [detection(), detection("S-2", 360, embedding=1)]
        fx.detector.detect_persons.return_value = [[50, 0, 160, 300], [330, 0, 450, 300]]
        fx.trainer.predict_proba.side_effect = lambda _, crop: {"correct_uniform": .9 if crop[0, 0, 0] > 100 else .1}
        result = fx.confirmed()
        self.assertEqual([a.student_id for a in result.assessments], ["S-1", "S-2"])
        self.assertEqual([a.state for a in result.assessments], [UNIFORM_COMPLIANT, SUSPECTED_VIOLATION])
        self.assertEqual(fx.trainer.predict_proba.call_count, 8)
        fx.persist(result)
        self.assertEqual(fx.database.log_violation.call_args.kwargs["student_id"], "S-2")
        fx.notifier.notify.assert_called_once()
        self.assertEqual(fx.notifier.notify.call_args.args, ("Student S-2", "Suspected uniform violation"))
        self.assertEqual(fx.notifier.notify.call_args.kwargs["observed_at"], result.task.observed_at)
        self.assertTrue(callable(fx.notifier.notify.call_args.kwargs["valid_if"]))

    def test_snapshot_student_category_and_time_all_come_from_accepted_frame(self):
        fx = PipelineFixture()
        result = fx.confirmed()
        fx.persist(result)
        call = fx.database.log_violation.call_args.kwargs
        self.assertEqual(call["student_id"], result.assessments[0].student_id)
        self.assertEqual(call["violation_code"], result.assessments[0].accepted_categories[0])
        self.assertEqual(call["detected_at"], datetime.fromtimestamp(result.task.observed_at, timezone.utc))
        self.assertEqual(call["status"], "pending_review")
        image = cv2.imdecode(np.frombuffer(call["snapshot_jpeg"], np.uint8), cv2.IMREAD_COLOR)
        body = result.assessments[0].body_box
        expected = result.task.frame[body[1]:body[3], body[0]:body[2]]
        self.assertEqual(image.shape, expected.shape)
        self.assertLess(np.abs(image.astype(float)-expected).mean(), 2.)
        with patch("core.live_pipeline.time.monotonic", return_value=fx.now):
            self.assertTrue(call["valid_if"]())
        fx.database.record_attendance.assert_called_once()

    def test_duplicate_frame_does_not_supply_evidence_or_identity_twice(self):
        fx = PipelineFixture()
        first = fx.analyze(1)
        self.assertFalse(first.assessments[0].reliable_identity)
        duplicate = fx.analyze(1)
        self.assertTrue(duplicate is None or not duplicate.assessments)
        second = fx.analyze(2)
        self.assertTrue(second.assessments[0].reliable_identity)
        self.assertEqual(second.assessments[0].state, CHECKING_UNIFORM)
        for _ in range(5):
            duplicate = fx.analyze(2)
            self.assertTrue(duplicate is None or not duplicate.assessments)
        self.assertFalse(fx.analyze(3).assessments[0].accepted_categories)
        self.assertTrue(fx.analyze(4).assessments[0].accepted_categories)

    def test_duplicate_frames_skip_expensive_inference(self):
        fx = PipelineFixture()
        fx.analyze(1)
        fx.analyze(1)
        fx.analyze(1)
        self.assertEqual(fx.recognizer.recognize_faces.call_count, 1)
        self.assertEqual(fx.detector.detect_persons.call_count, 1)
        fx.analyze(2)
        self.assertEqual(fx.recognizer.recognize_faces.call_count, 2)

    def test_failure_missing_models_and_no_associated_body_never_become_ok(self):
        for cause in ("disabled", "models", "body", "exception", "uncertain"):
            fx = PipelineFixture()
            changes = {}
            if cause == "disabled":
                changes["uniform_enabled"] = False
            elif cause == "models":
                fx.trainer.is_trained.return_value = False
                fx.trainer.is_trained.side_effect = None
            elif cause == "body":
                fx.detector.detect_persons.return_value = [[400, 0, 640, 320]]
            elif cause == "exception":
                fx.trainer.predict_proba.side_effect = RuntimeError("classifier failed")
            elif cause == "uncertain":
                fx.trainer.predict_proba.return_value = {"correct_uniform": .5}
            for seq in range(1, 6):
                result = fx.analyze(seq, **changes)
            self.assertEqual(result.assessments[0].state, UNIFORM_NOT_ASSESSED, cause)
            fx.persist(result)
            fx.database.log_violation.assert_not_called()
            fx.notifier.notify.assert_not_called()

    def test_unknown_people_have_distinct_security_events_without_student_violation(self):
        fx = PipelineFixture()
        fx.recognizer.recognize_faces.return_value = [detection(), detection("", 300, embedding=1), detection("", 510, embedding=2)]
        fx.trainer.predict_proba.return_value = {"correct_uniform": .9}
        result = fx.confirmed()
        fx.persist(result)
        fx.database.log_violation.assert_not_called()
        self.assertEqual(fx.database.log_security_event.call_count, 2)
        keys = [call.args[0] for call in fx.database.log_security_event.call_args_list]
        self.assertEqual(len(set(keys)), 2)
        self.assertTrue(all(key.startswith("person:") for key in keys))
        self.assertEqual(fx.database.record_attendance.call_args.args, ("S-1",))

    def test_failed_writes_retry_but_success_consumes_per_student_category_cooldown(self):
        fx = PipelineFixture()
        result = fx.confirmed()
        fx.database.log_violation.side_effect = [RuntimeError("locked"), None, 99, 100]
        for seq in range(4, 8):
            if seq != 4:
                result = fx.analyze(seq)
            fx.persist(result)
        self.assertEqual(fx.database.log_violation.call_count, 3)
        self.assertEqual(fx.processor.write_count, 1)
        fx.notifier.notify.assert_called_once()
        # Losing temporary tracks must not bypass the student's category cooldown.
        fx.processor.state.reset(2)
        for seq in range(8, 12):
            result = fx.analyze(seq, generation=2)
        fx.persist(result)
        self.assertEqual(fx.database.log_violation.call_count, 3)

    def test_attendance_independent_of_uniform_and_only_after_reliable_identity(self):
        fx = PipelineFixture()
        first = fx.analyze(1, uniform_enabled=False)
        fx.persist(first)
        fx.database.record_attendance.assert_not_called()
        second = fx.analyze(2, uniform_enabled=False)
        fx.persist(second)
        third = fx.analyze(3, uniform_enabled=False)
        fx.persist(third)
        fx.database.record_attendance.assert_called_once()
        self.assertEqual(fx.database.record_attendance.call_args.args, ("S-1",))
        fx.database.log_violation.assert_not_called()

    def test_graduate_presence_pending_registration_and_suspension_eligibility(self):
        for status, pending in (("Graduate", False), ("Unenrolled", False), ("Enrolled", True)):
            fx = PipelineFixture()
            fx.database.get_student_by_student_id.side_effect = lambda sid: dict(
                student_id=sid, student_status=status, registration_pending=pending)
            result = fx.confirmed()
            fx.persist(result)
            fx.database.record_attendance.assert_called_once()
            fx.database.log_violation.assert_not_called()
            self.assertEqual(fx.database.record_premises_entry.call_count, int(not pending))
        fx = PipelineFixture()
        fx.database.get_active_suspension.return_value = {"ends_at": "2027-01-01"}
        first = fx.analyze(1)
        fx.persist(first)
        fx.notifier.notify.assert_not_called()
        for seq in (2, 3):
            fx.persist(fx.analyze(seq))
        fx.notifier.notify.assert_called_once()
        self.assertEqual(fx.notifier.notify.call_args.args, ("Student S-1", "Suspended until 2027-01-01 UTC"))
        self.assertTrue(callable(fx.notifier.notify.call_args.kwargs["valid_if"]))

    def test_earring_category_independent_of_torso_and_gender(self):
        fx = PipelineFixture()
        fx.trainer.is_trained.side_effect = lambda module: module == "earring"
        fx.trainer.predict.return_value = ("with_earring", .9)
        result = fx.confirmed()
        self.assertEqual(result.assessments[0].state, UNIFORM_NOT_ASSESSED)
        self.assertEqual(result.assessments[0].accepted_categories, ("earring",))
        fx.persist(result)
        self.assertEqual(fx.database.log_violation.call_args.kwargs["violation_code"], "earring")
        fx.recognizer.recognize_faces.return_value = [detection(gender="Female")]
        self.assertFalse(fx.analyze(5).assessments[0].accepted_categories)

    def test_cancelled_old_session_disconnected_and_expired_results_have_no_side_effect(self):
        for cause in ("cancelled", "source", "stale", "disconnected", "camera_late"):
            fx = PipelineFixture()
            result = fx.confirmed()
            if cause == "cancelled":
                fx.cancelled.set()
            elif cause == "source":
                fx.latest = CameraSample(result.task.frame, ("other", 4), fx.now-.1)
            elif cause == "stale":
                fx.now += 5.
            elif cause == "disconnected":
                fx.latest = None
            else:
                fx.latest = CameraSample(result.task.frame, ("camera", 5), fx.now-2.)
            fx.persist(result)
            fx.database.log_violation.assert_not_called()
            fx.database.record_attendance.assert_not_called()
            fx.notifier.notify.assert_not_called()

    def test_movement_during_analysis_requires_a_safe_projection_before_side_effects(self):
        fx = PipelineFixture()
        result = fx.confirmed()
        fx.latest = CameraSample(np.zeros_like(result.task.frame), ("camera", 5), fx.now-.01)
        with patch("core.live_pipeline.MotionProjection") as projection:
            projection.return_value.advance.return_value = None
            fx.persist(result)
        fx.database.log_violation.assert_not_called()
        fx.database.record_attendance.assert_not_called()
        fx.notifier.notify.assert_not_called()

    def test_session_invalidated_inside_database_write_prevents_notifications_and_next_write(self):
        fx = PipelineFixture()
        result = fx.confirmed()
        def record(*args, valid_if, **kwargs):
            fx.cancelled.set()
            return valid_if()
        fx.database.record_attendance.side_effect = record
        fx.persist(result)
        fx.database.log_violation.assert_not_called()
        fx.notifier.notify.assert_not_called()
        self.assertFalse(fx.processor.attendance_cooldowns)

    def test_disconnect_during_database_work_is_rechecked_by_transaction_guard(self):
        fx = PipelineFixture()
        result = fx.confirmed()
        def record(*args, valid_if, **kwargs):
            fx.latest = None
            return valid_if()
        fx.database.record_attendance.side_effect = record
        fx.persist(result)
        fx.database.log_violation.assert_not_called()
        fx.notifier.notify.assert_not_called()
        self.assertFalse(fx.processor.attendance_cooldowns)

    def test_recognition_finishing_after_invalidation_does_not_update_state(self):
        fx = PipelineFixture()
        def recognize(_frame):
            fx.cancelled.set()
            return [detection()]
        fx.recognizer.recognize_faces.side_effect = recognize
        self.assertIsNone(fx.analyze(1))
        fx.database.get_student_by_student_id.assert_not_called()
        fx.detector.detect_persons.assert_not_called()
        fx.recognizer.recognize_faces.side_effect = None
        fx.cancelled = threading.Event()
        first = fx.analyze(2, generation=2)
        self.assertFalse(first.assessments[0].reliable_identity)

    def test_torso_inference_finishing_after_frame_expires_does_not_update_evidence(self):
        fx = PipelineFixture()
        def classify(*_args):
            fx.now += 4.
            return {"correct_uniform": .1}
        fx.trainer.predict_proba.side_effect = classify
        self.assertIsNone(fx.analyze(1))
        fx.trainer.predict_proba.side_effect = None
        first_valid = fx.analyze(2)
        self.assertFalse(first_valid.assessments[0].reliable_identity)

    def test_failed_recognition_frame_breaks_evidence_without_recycling_track(self):
        fx = PipelineFixture()
        previous = fx.confirmed()
        fx.recognizer.recognize_faces.side_effect = RuntimeError("Inference failed")
        task = fx.task(5)
        with patch("core.live_pipeline.time.monotonic", side_effect=lambda: fx.now):
            with self.assertRaises(RuntimeError):
                fx.processor.analyze(task)
            fx.processor.assessment_failed(task)
        fx.recognizer.recognize_faces.side_effect = None
        recovered = fx.analyze(6)
        self.assertEqual(previous.assessments[0].track_id, recovered.assessments[0].track_id)
        self.assertFalse(recovered.assessments[0].reliable_identity)
        self.assertFalse(recovered.assessments[0].accepted_categories)
        self.assertFalse(fx.analyze(7).assessments[0].accepted_categories)

    def test_analysis_does_not_publish_mutable_recognizer_dictionaries(self):
        fx = PipelineFixture()
        rows = fx.recognizer.recognize_faces.return_value
        original_keys = set(rows[0])
        result = fx.confirmed()
        self.assertEqual(set(rows[0]), original_keys)
        rows[0]["name"] = "Rebound identity"
        rows[0]["box"][0] = 999
        self.assertEqual(result.assessments[0].name, "Student S-1")
        self.assertEqual(result.assessments[0].face_box[0], 80)


class LiveWorkerTests(unittest.TestCase):
    def task(self, number, cancelled=None):
        now = time.monotonic()
        return MonitorTask(FrameContext(1, ("camera", number), now),
                           np.zeros((10, 10, 3), np.uint8), time.time(),
                           threading.Event() if cancelled is None else cancelled)

    def test_worker_processes_latest_pending_frame_and_bounded_output(self):
        entered, release = threading.Event(), threading.Event()
        seen = []
        processor = MagicMock()
        def analyze(task):
            seen.append(task.context.frame_id[1])
            if len(seen) == 1:
                entered.set()
                release.wait(2.)
            return MonitorResult(task, (), time.monotonic())
        processor.analyze.side_effect = analyze
        worker = LiveWorker(processor)
        worker.start()
        try:
            worker.offer(self.task(1))
            self.assertTrue(entered.wait(1.))
            for number in range(2, 12):
                worker.offer(self.task(number))
            self.assertEqual(worker.requests.qsize(), 1)
            release.set()
            limit = time.monotonic()+1.
            while len(seen) < 2 and time.monotonic() < limit:
                time.sleep(.005)
            self.assertEqual(seen, [1, 11])
            self.assertLessEqual(worker.results.qsize(), 1)
        finally:
            release.set()
            worker.stop()
            self.assertTrue(worker.done.wait(1.))
            worker.thread.join(1.)

    def test_stop_during_analysis_prevents_persistence_of_inflight_work(self):
        entered, release = threading.Event(), threading.Event()
        processor = MagicMock()
        def analyze(task):
            entered.set()
            release.wait(2.)
            return MonitorResult(task, (), time.monotonic())
        processor.analyze.side_effect = analyze
        worker = LiveWorker(processor)
        worker.start()
        try:
            worker.offer(self.task(1))
            self.assertTrue(entered.wait(1.))
            worker.stop()
            release.set()
            self.assertTrue(worker.done.wait(1.))
            processor.persist.assert_not_called()
            self.assertTrue(worker.results.empty())
        finally:
            release.set()
            worker.stop()
            worker.thread.join(1.)

    def test_session_cancelled_during_analysis_drops_all_work(self):
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        processor = MagicMock()
        def analyze(task):
            entered.set()
            release.wait(2.)
            return MonitorResult(task, (), time.monotonic())
        processor.analyze.side_effect = analyze
        worker = LiveWorker(processor)
        worker.start()
        try:
            worker.offer(self.task(1, cancelled))
            self.assertTrue(entered.wait(1.))
            cancelled.set()
            release.set()
        finally:
            worker.stop()
            release.set()
            self.assertTrue(worker.done.wait(1.))
            worker.thread.join(1.)
        processor.persist.assert_not_called()
        self.assertTrue(worker.results.empty())

    def test_failed_analysis_invalidates_evidence_and_publishes_unavailable(self):
        processor = MagicMock()
        processor.analyze.side_effect = RuntimeError("synthetic inference failure")
        worker = LiveWorker(processor)
        task = self.task(1)
        worker.start()
        try:
            worker.offer(task)
            result = worker.results.get(timeout=1.)
            self.assertEqual(result.detail, "Assessment unavailable")
            self.assertEqual(result.assessments, ())
            processor.assessment_failed.assert_called_once_with(task)
            processor.persist.assert_not_called()
        finally:
            worker.stop()
            self.assertTrue(worker.done.wait(1.))
            worker.thread.join(1.)


class MotionProjectionTests(unittest.TestCase):
    def test_low_texture_or_changed_resolution_abstains(self):
        flat = np.zeros((240, 320, 3), np.uint8)
        projection = MotionProjection(flat, (60, 30, 160, 160))
        self.assertIsNone(projection.advance(flat))
        self.assertIsNone(projection.advance(np.zeros((120, 160, 3), np.uint8)))

    def test_textured_face_translation_follows_same_pixels(self):
        rng = np.random.default_rng(5)
        frame = np.zeros((240, 320, 3), np.uint8)
        frame[30:160, 60:160] = rng.integers(0, 255, (130, 100, 3), dtype=np.uint8)
        projection = MotionProjection(frame, (60, 30, 160, 160))
        shifted = cv2.warpAffine(frame, np.array([[1., 0, 6], [0, 1., 3]]), (320, 240))
        projected = projection.advance(shifted)
        self.assertIsNotNone(projected)
        np.testing.assert_allclose(projected, (66, 33, 166, 163), atol=1.)


if __name__ == "__main__":
    unittest.main()
