#!/usr/bin/env python3
"""Sequential model-backed replay through the CBVMS tracking/assessment path.

Use a disposable evaluation database (`--evaluation-db`) containing only the
authorized evaluation gallery and eligible synthetic/evaluation students. The
database is SQLite-backed into a temporary copy before initialization. With no
database, an empty temporary roster exercises real detection/assessment plumbing
but identity/uniform metrics are unavailable. Nothing writes to production DB.

Run `--mode baseline` and `--mode candidate` sequentially with the same video,
annotations, evaluation DB, flags, and model settings. Candidate mode uses
ClearFrameRouter; both modes use FaceTrackingProcessor and LiveProcessor with
the real installed models and the hard observation-only persistence gate.
"""
import argparse
import json
import os
import queue
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cbvms-camera-replay-mpl"))
os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "cbvms-camera-replay-yolo"))


def load_annotations(path):
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row.get("frame_index"), int) or not isinstance(row.get("people"), list):
                    raise ValueError("expected frame_index and people")
                rows.append(row)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid annotation line {line_no}: {exc}") from exc
    return sorted(rows, key=lambda row: row["frame_index"])


def overlap(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2-x1) * max(0, y2-y1)
    union = max(1, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter)
    return inter / union


class ReplayMetrics:
    """Aggregate passage labels without retaining image pixels or raw identities."""
    def __init__(self, student_id_to_alias=None):
        self.student_id_to_alias = {str(k): str(v) for k, v in (student_id_to_alias or {}).items()}
        self.passages = {}
        self.identity_counts = {"correct": 0, "wrong": 0, "abstained": 0, "unscorable": 0}
        self.uniform_counts = {"confirmed": 0, "wrong": 0, "abstained": 0}

    def observe_labels(self, frame_index, media_ms, people):
        for person in people:
            alias = str(person["passage_alias"])
            state = self.passages.setdefault(alias, {
                "entry_frame": int(frame_index), "entry_ms": float(media_ms),
                "expected_identity": person.get("identity_label"),
                "expected_uniform": person.get("uniform_label"), "detected": False,
                "first_detection_ms": None, "first_usable_ms": None,
                "first_assessment_ms": None, "first_correct_confirmation_ms": None,
                "identity_correct": False, "wrong_identity": False,
                "uniform_confirmed": None, "uniform_wrong_seen": False,
                "uniform_correct_seen": False, "uniform_unassessed_seen": False,
                "uniform_attempted": False, "first_joint_correct_confirmation_ms": None})
            if media_ms < state["entry_ms"]:
                state["entry_ms"], state["entry_frame"] = float(media_ms), int(frame_index)

    def _matches(self, people, observations):
        pairs = []
        for li, person in enumerate(people):
            box = person.get("face_box")
            if not box:
                continue
            for oi, observation in enumerate(observations):
                score = overlap(box, observation.box)
                if score >= .25:
                    pairs.append((score, li, oi))
        pairs.sort(reverse=True)
        labels, detected, match = set(), set(), {}
        for _, li, oi in pairs:
            if li not in labels and oi not in detected:
                labels.add(li); detected.add(oi); match[li] = oi
        return match

    def record_detections(self, frame_index, media_ms, people, observations):
        matches = self._matches(people, observations)
        for li, person in enumerate(people):
            state = self.passages[str(person["passage_alias"])]
            if li in matches:
                state["detected"] = True
                if state["first_detection_ms"] is None:
                    state["first_detection_ms"] = float(media_ms)
        return matches

    def record_usable(self, frame_index, media_ms, people, observations):
        for li, person in enumerate(people):
            state = self.passages[str(person["passage_alias"])]
            if li in self._matches(people, observations) and state["first_usable_ms"] is None:
                state["first_usable_ms"] = float(media_ms)

    def record_assessments(self, frame_index, media_ms, people, observations, assessments,
                           completion_ms=None):
        completed_at = float(media_ms if completion_ms is None else completion_ms)
        matches = self._matches(people, observations)
        for li, person in enumerate(people):
            if li not in matches:
                continue
            alias = str(person["passage_alias"])
            state = self.passages[alias]
            oi = matches[li]
            observation = observations[oi]
            assessment = assessments[oi] if oi < len(assessments) else None
            if state["first_usable_ms"] is None:
                # Face-only is usable for identity; torso is needed only for uniform.
                state["first_usable_ms"] = float(media_ms)
            if state["first_assessment_ms"] is None:
                state["first_assessment_ms"] = completed_at
            if assessment is None or not assessment.reliable_identity or not assessment.student_id:
                self.identity_counts["abstained"] += 1
                continue
            actual_alias = self.student_id_to_alias.get(str(assessment.student_id))
            expected_alias = state["expected_identity"]
            if actual_alias is None or not expected_alias:
                self.identity_counts["unscorable"] += 1
                identity_correct = False
            elif actual_alias == str(expected_alias):
                self.identity_counts["correct"] += 1
                state["identity_correct"] = True
                if state["first_correct_confirmation_ms"] is None:
                    state["first_correct_confirmation_ms"] = completed_at
                identity_correct = True
            else:
                self.identity_counts["wrong"] += 1
                state["wrong_identity"] = True
                identity_correct = False
            if not identity_correct:
                continue
            uniform = getattr(assessment.uniform_assessment, "value", assessment.uniform_assessment)
            if uniform == "unassessed":
                self.uniform_counts["abstained"] += 1
                if state["expected_uniform"] in {"correct_uniform", "wrong_uniform"}:
                    state["uniform_attempted"] = True
                    state["uniform_unassessed_seen"] = True
            else:
                uniform = {"correct": "correct_uniform", "wrong": "wrong_uniform"}.get(
                    str(uniform), str(uniform))
                self.uniform_counts["confirmed"] += 1
                state["uniform_confirmed"] = str(uniform)
                state["uniform_attempted"] = state["expected_uniform"] in {
                    "correct_uniform", "wrong_uniform"}
                state["uniform_wrong_seen"] |= uniform == "wrong_uniform"
                state["uniform_correct_seen"] |= uniform == "correct_uniform"
                if state["expected_uniform"] and str(uniform) != str(state["expected_uniform"]):
                    self.uniform_counts["wrong"] += 1
                if (uniform == state["expected_uniform"]
                        and state["first_joint_correct_confirmation_ms"] is None):
                    state["first_joint_correct_confirmation_ms"] = completed_at

    def report(self):
        identity_total = self.identity_counts["correct"] + self.identity_counts["wrong"]
        evaluated = bool(self.student_id_to_alias and identity_total)
        entry_latency = [s["first_correct_confirmation_ms"] - s["entry_ms"]
                         for s in self.passages.values() if s["first_correct_confirmation_ms"] is not None]
        usable_latency = [s["first_correct_confirmation_ms"] - s["first_usable_ms"]
                          for s in self.passages.values() if s["first_correct_confirmation_ms"] is not None
                          and s["first_usable_ms"] is not None]
        joint_latency = [s["first_joint_correct_confirmation_ms"] - s["entry_ms"]
            for s in self.passages.values() if s["first_joint_correct_confirmation_ms"] is not None]
        return {"passage_count": len(self.passages),
            "missed_passages": sum(not s["detected"] for s in self.passages.values()),
            "unable_to_identify_passages": sum(bool(s["expected_identity"])
                and not s["identity_correct"] and not s["wrong_identity"] for s in self.passages.values()),
            "confirmed_correct_passages": sum(s["identity_correct"] for s in self.passages.values()),
            "wrong_identity_passages": sum(s["wrong_identity"] for s in self.passages.values()),
            "identity_observations": dict(self.identity_counts),
            "identity_accuracy": (self.identity_counts["correct"] / identity_total if evaluated else None),
            "identity_accuracy_reason": None if evaluated else "unverified_without_authorized_alias_gallery_and_matches",
            "uniform_observations": dict(self.uniform_counts),
            "uniform_labeled_passages": sum(s["expected_uniform"] in
                {"correct_uniform", "wrong_uniform"} for s in self.passages.values()),
            "uniform_accuracy": (
                sum(s["expected_uniform"] == s["uniform_confirmed"]
                    for s in self.passages.values()
                    if s["expected_uniform"] in {"correct_uniform", "wrong_uniform"}
                    and s["uniform_confirmed"] is not None) /
                max(1, sum(s["uniform_confirmed"] is not None
                    for s in self.passages.values()
                    if s["expected_uniform"] in {"correct_uniform", "wrong_uniform"}))
                if any(s["expected_uniform"] in {"correct_uniform", "wrong_uniform"}
                       and s["uniform_confirmed"] is not None for s in self.passages.values())
                else None),
            "uniform_accuracy_reason": None if any(s["expected_uniform"] in
                {"correct_uniform", "wrong_uniform"} and s["uniform_confirmed"] is not None
                for s in self.passages.values()) else "unverified_without_labeled_confirmed_uniform_assessments",
            "false_uniform_violations": sum(s["expected_uniform"] == "correct_uniform"
                and s["uniform_wrong_seen"] for s in self.passages.values()),
            "missed_uniform_violations": sum(s["expected_uniform"] == "wrong_uniform"
                and not s["uniform_wrong_seen"] for s in self.passages.values()),
            "unable_to_assess_passages": sum(s["uniform_attempted"]
                and s["uniform_unassessed_seen"] for s in self.passages.values()),
            "unable_to_assess_denominator": sum(s["uniform_attempted"] for s in self.passages.values()),
            "unable_to_assess_rate": (
                sum(s["uniform_attempted"] and s["uniform_unassessed_seen"]
                    for s in self.passages.values()) /
                max(1, sum(s["uniform_attempted"] for s in self.passages.values()))
                if any(s["uniform_attempted"] for s in self.passages.values()) else None),
            "passage_entry_to_correct_confirmation_ms": entry_latency,
            "passage_entry_to_joint_identity_uniform_confirmation_ms": joint_latency,
            "first_usable_to_correct_confirmation_ms": usable_latency}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("baseline", "candidate"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evaluation-db", type=Path,
                        help="authorized disposable SQLite evaluation gallery; original is never opened for writes")
    parser.add_argument("--identity-alias-map", type=Path,
                        help="JSON alias->student_id map for authorized identity scoring; IDs are never output")
    parser.add_argument("--max-frames", type=int, default=0, help="0 means the full video")
    parser.add_argument("--clear-frame-selection", action="store_true",
                        help="candidate mode: retain/select clearer original frames per passage")
    parser.add_argument("--prioritized-assessment", action="store_true",
                        help="candidate mode: enable fair prioritized candidate scheduling")
    args = parser.parse_args()
    if not args.video.is_file() or (args.evaluation_db and not args.evaluation_db.is_file()):
        parser.error("video and optional evaluation DB must be local files")
    if args.identity_alias_map and not args.evaluation_db:
        parser.error("identity scoring requires an authorized disposable --evaluation-db")
    if args.identity_alias_map and not args.identity_alias_map.is_file():
        parser.error("identity alias map must be a local JSON file")
    if args.max_frames < 0:
        parser.error("--max-frames cannot be negative")
    try:
        labels = load_annotations(args.annotations)
    except ValueError as exc:
        parser.error(str(exc))

    # Delayed model imports keep --help usable without loading native runtimes.
    from database.db_manager import CBVMSDatabase
    from core.assessment_candidates import ClearFrameRouter
    from core.live_pipeline import FaceTrackingProcessor, LiveProcessor, LiveWorker, MonitorTask
    from core.live_state import FrameContext, LiveState
    from core.person_detector import PersonDetector
    from core.recognizer import FaceRecognizer
    from core.trainer import ViolationTrainer
    from core.uniform_matcher import UniformColorMatcher

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        parser.error("could not open local video")
    by_frame = {row["frame_index"]: row for row in labels}
    metric = None
    decoded_frames = annotated_frames = 0
    selected_count = 0
    assessment_calls = assessment_results = assessment_rows = 0
    compute_ms = deque(maxlen=10_000)
    compute_samples_dropped = 0
    skipped_for_lag = 0
    worker = None
    database = None
    detector = PersonDetector()
    trainer = ViolationTrainer()
    started = time.monotonic()
    try:
        temporary = tempfile.TemporaryDirectory(prefix="cbvms-camera-replay-")
        try:
            private = temporary.name
            db_path = Path(private) / "evaluation.sqlite"
            if args.evaluation_db:
                source = sqlite3.connect(f"file:{args.evaluation_db.resolve()}?mode=ro", uri=True)
                destination = sqlite3.connect(db_path)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                    source.close()
            database = CBVMSDatabase(db_path)
            database.initialize(process_deadlines=False)
            recognizer = FaceRecognizer(database)
            matcher = UniformColorMatcher()
            first_ok, first_frame = capture.read()
            if not first_ok:
                parser.error("video contains no decodable frames")
            warm_started = time.monotonic()
            recognizer._ensure_models()
            detector._ensure_model()
            detector.detect_persons(first_frame)
            if trainer.is_trained("uniform"):
                trainer.predict_proba("uniform", first_frame)
            warmup_wall_ms = round((time.monotonic() - warm_started) * 1000, 2)
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            tracker = FaceTrackingProcessor(recognizer, detector)
            processor = LiveProcessor(database, recognizer, detector, trainer, matcher,
                                      SimpleNamespace(), state=LiveState(), observation_only=True)
            candidate_route = args.mode == "candidate" and (
                args.clear_frame_selection or args.prioritized_assessment)
            router = ClearFrameRouter() if candidate_route else None
            aliases = {}
            if args.identity_alias_map:
                aliases = json.loads(args.identity_alias_map.read_text(encoding="utf-8"))
                if not isinstance(aliases, dict):
                    parser.error("identity alias map must be a JSON object")
            metric = ReplayMetrics({student_id: alias for alias, student_id in aliases.items()})
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            if fps <= 0:
                fps = 30.0
            worker_results = None
            compute_lock = threading.Lock()
            replay_clock_start = time.monotonic()
            original_analyze = processor.analyze
            def measured_analyze(task):
                nonlocal compute_samples_dropped
                compute_started = time.monotonic()
                try:
                    return original_analyze(task)
                finally:
                    with compute_lock:
                        if len(compute_ms) == compute_ms.maxlen:
                            compute_samples_dropped += 1
                        compute_ms.append((time.monotonic()-compute_started)*1000)
            processor.analyze = measured_analyze
            worker = LiveWorker(processor, observation_only=True)
            worker_results = worker.results  # bounded single-slot result channel
            worker.start()
            selected_guard = threading.RLock()
            def consume_result(result):
                nonlocal assessment_results, assessment_rows
                selected_index = int(result.task.context.frame_id[1])
                selected_labels = by_frame.get(selected_index, {}).get("people", [])
                selected_ms = selected_index * 1000.0 / fps
                completion_ms = max(0.0, (result.finished_at-replay_clock_start)*1000)
                assessment_results += 1
                assessment_rows += len(result.assessments)
                metric.record_assessments(selected_index, selected_ms, selected_labels,
                    result.task.observations or (), result.assessments, completion_ms=completion_ms)
            def dispatch_candidate():
                nonlocal selected_count, assessment_calls
                if router is None or worker.stop_event.is_set():
                    return
                candidate = router.next(prioritize=args.prioritized_assessment)
                if candidate is None:
                    return
                selected_index = int(candidate.task.context.frame_id[1])
                selected_labels = by_frame.get(selected_index, {}).get("people", [])
                selected_ms = selected_index * 1000.0 / fps
                metric.observe_labels(selected_index, selected_ms, selected_labels)
                metric.record_usable(selected_index, selected_ms, selected_labels,
                                     candidate.task.observations or ())
                task = candidate.task
                from copy import copy
                task = copy(task)
                object.__setattr__(task, "candidate_on_complete", router.complete)
                with selected_guard:
                    selected_count += 1
                    assessment_calls += 1
                worker.offer(task)
            worker.on_idle = dispatch_candidate
            index = 0
            next_frame_at = time.monotonic()
            last_baseline_offer_at = -1e9
            while args.max_frames == 0 or index < args.max_frames:
                delay = next_frame_at-time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                ok, frame = capture.read()
                if not ok:
                    break
                row = by_frame.get(index)
                if row is not None:
                    annotated_frames += 1
                media_ms = index * 1000.0 / fps
                people = row.get("people", []) if row else []
                metric.observe_labels(index, media_ms, people)
                captured = time.monotonic()
                if captured - next_frame_at > 1.0 / fps:
                    # Preserve labels as misses, but never burst overdue image work.
                    skipped_for_lag += 1
                    index += 1
                    decoded_frames += 1
                    next_frame_at = replay_clock_start + index / fps
                    continue
                task = MonitorTask(FrameContext(1, ("replay", index), captured), frame,
                    time.time(), threading.Event(), source_id="local-replay",
                    source_label="Local evaluation replay", observation_only=True)
                tracked = tracker.analyze(task)
                if tracked is not None:
                    if people and tracked.observations:
                        metric.record_detections(index, media_ms, people, tracked.observations)
                        metric.record_usable(index, media_ms, people, tracked.observations)
                    if router is not None:
                        router.observe(tracked, prioritize=args.prioritized_assessment,
                                       select=args.clear_frame_selection)
                        dispatch_candidate()
                    elif captured-last_baseline_offer_at >= .5:
                        candidate_task = tracked.task.with_observations(
                            tracked.observations).for_observation_only(tracked.observations)
                        if worker.offer(candidate_task):
                            with selected_guard:
                                selected_count += 1
                                assessment_calls += 1
                            last_baseline_offer_at = captured
                    else:
                        candidate_task = None
                while True:
                    try:
                        consume_result(worker_results.get_nowait())
                    except queue.Empty:
                        break
                index += 1
                decoded_frames += 1
                next_frame_at = replay_clock_start + index / fps
            drain_deadline = time.monotonic() + 10.0
            while time.monotonic() < drain_deadline:
                dispatch_candidate()
                while True:
                    try:
                        consume_result(worker_results.get_nowait())
                    except queue.Empty:
                        break
                stats = router.stats() if router is not None else {"queued": 0, "in_flight": 0}
                if worker.active_task is None and worker.requests.empty() and not stats["queued"] and not stats["in_flight"]:
                    break
                time.sleep(.01)
            worker.on_idle = None
            worker.stop()
            worker.done.wait(5)
            while True:
                try:
                    consume_result(worker_results.get_nowait())
                except queue.Empty:
                    break
        finally:
            if worker is not None and not worker.done.is_set():
                worker.on_idle = None
                worker.stop()
                worker.done.wait(5)
            if database is not None and hasattr(database, "close"):
                database.close()
            temporary.cleanup()
    finally:
        capture.release()

    elapsed = time.monotonic() - started
    metrics = metric.report() if metric is not None else {}
    compute_latency = {"n": len(compute_ms),
        "p50_ms": round(float(np.percentile(compute_ms, 50)), 2) if compute_ms else None,
        "p95_ms": round(float(np.percentile(compute_ms, 95)), 2) if compute_ms else None,
        "p99_ms": round(float(np.percentile(compute_ms, 99)), 2) if compute_ms else None}
    report = {"mode": args.mode, "frames_decoded": decoded_frames,
        "annotated_frames": annotated_frames, "processed_video_frames": decoded_frames,
        "selected_assessment_frames": selected_count, "passages": metrics.get("passage_count", 0),
        "live_processor_calls": assessment_calls, "live_processor_results": assessment_results,
        "assessment_rows": assessment_rows, "passage_metrics": metrics,
        "compute_wall_latency": compute_latency,
        "compute_latency_samples_dropped": compute_samples_dropped,
        "frames_skipped_for_lag": skipped_for_lag,
        "passage_latency_verified": skipped_for_lag == 0,
        "latency_clock": "paced video playback monotonic completion time; capture evidence uses video frame timeline; timing unverified if frames skipped",
        "elapsed_wall_ms": round(elapsed*1000, 2),
        "warmup_wall_ms": warmup_wall_ms,
        "identity_scoring_enabled": bool(args.evaluation_db and args.identity_alias_map),
        "model_backends": {"body_yolo": detector.backend_status(), "face": "ONNX CPUExecutionProvider"},
        "uniform_model_loaded": trainer.is_trained("uniform"),
        "observation_only": True, "database_writes": 0,
        "candidate_flags": {"clear_frame_selection": args.clear_frame_selection,
                             "prioritized_assessment": args.prioritized_assessment},
        "evaluation_database_copied_to_private_temp_and_discarded": bool(args.evaluation_db),
        "passage_details_written": False,
        "comparison_requires_same_video_annotations_evaluation_db_and_flags": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
