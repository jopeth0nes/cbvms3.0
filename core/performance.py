"""Opt-in, bounded numeric pipeline timings; never retain frames or identities.

Set CBVMS_PERF=1 before launch. Each fixed-name metric retains only its latest
256 samples, plus a lifetime count. Times are monotonic seconds internally.
"""
from collections import deque
from contextlib import contextmanager
import math
import os
import threading
import time
import hashlib


NAMES = frozenset({
    "face_load", "face_warm", "recognition_load", "recognition_warm",
    "face_inference", "recognition_inference", "database_read", "body",
    "uniform", "tracking", "recognition", "queue_tracking", "queue_assessment",
    "capture_to_tracking", "capture_to_assessment", "preview_age", "ui_render",
    "lock_detector", "lock_recognition", "lock_person", "lock_trainer",
    "body_load", "body_inference", "uniform_load", "uniform_inference",
    "earring_load", "earring_inference", "persistence", "empty_scene",
    "rejected", "replaced_tracking", "replaced_assessment",
    "first_camera_frame", "first_face", "first_stable_identity", "first_torso",
    "first_uniform_assessment",
    "candidate_selection_wait", "passage_first_observation",
    "candidate_to_assessment", "passage_entry_to_assessment",
    "passage_entry_to_first_assessment", "usable_to_assessment",
    "usable_to_first_assessment",
    "face_alignment", "face_embedding", "gallery_matching", "frame_copy",
    "uniform_preprocessing", "earring_preprocessing", "result_to_ui",
    "confirmation",
    "candidate_quality",
    "candidate_invalid_face_box", "candidate_face_visibility_rejected",
    "candidate_empty_face_crop",
})


class PerformanceMetrics:
    def __init__(self, enabled=False, capacity=256):
        self.enabled = enabled
        self.capacity = capacity
        self._lock = threading.Lock()
        self._samples = {}
        self._counts = {}
        self._first = set()
        self._origin = time.monotonic()

    def observe(self, name, seconds):
        if not self.enabled or name not in NAMES:
            return
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            return
        with self._lock:
            self._samples.setdefault(name, deque(maxlen=self.capacity)).append(float(seconds))
            self._counts[name] = self._counts.get(name, 0) + 1

    def start_monitor(self):
        """A new monitor/session gets new first-result milestones, no identity cache."""
        if self.enabled:
            with self._lock:
                self._origin = time.monotonic()
                self._first.clear()

    def first(self, name):
        if not self.enabled or name not in NAMES:
            return
        with self._lock:
            if name in self._first:
                return
            self._first.add(name)
            elapsed = time.monotonic()-self._origin
        self.observe(name, elapsed)

    def snapshot(self):
        with self._lock:
            values = {name: (tuple(samples), self._counts[name]) for name, samples in self._samples.items()}
        result = {}
        for name, (samples, count) in values.items():
            ordered = sorted(samples)
            def percentile(p):
                at = (len(ordered)-1)*p
                lower = int(at)
                return round(1000*(ordered[lower]+(ordered[min(lower+1, len(ordered)-1)]-ordered[lower])*(at-lower)), 3)
            result[name] = dict(count=count, retained=len(samples), p50_ms=percentile(.5),
                                p95_ms=percentile(.95), p99_ms=percentile(.99))
        return result


PERF = PerformanceMetrics(os.environ.get("CBVMS_PERF") == "1")


class ObservationTraces:
    """Bounded local numerical traces; no pixels, labels or gallery identities."""
    def __init__(self, enabled=False, capacity=4096):
        self.enabled = enabled
        self.rows = deque(maxlen=capacity)
        self.dropped = 0
        self._lock = threading.Lock()
        self._local = threading.local()
        self._salt = os.urandom(16)

    def key(self, value):
        return hashlib.sha256(self._salt + repr(value).encode()).hexdigest()[:20]

    def span(self, name, start, end):
        row = getattr(self._local, "row", None)
        if row is not None and len(row['spans']) < 256:
            row['spans'].append(dict(stage=name, start=start, end=end))

    def result(self, result):
        row = getattr(self._local, 'row', None)
        if row is not None:
            row['completed'] = result is not None
            if result is not None:
                assessments = getattr(result, 'assessments', ())
                row['passages'] = [self.key(a.presence_id) for a in assessments]
                row['assessment_count'] = len(assessments)
                row['confirmed_identity_count'] = sum(bool(a.reliable_identity)
                    for a in assessments)

    @contextmanager
    def observation(self, task, kind, offered_at):
        if not self.enabled:
            yield
            return
        row = dict(frame=self.key((task.source_id, task.camera_generation,
                   task.context.generation, task.context.frame_id)), kind=kind,
                   passages=[self.key(o.presence_id) for o in task.observations or ()],
                   captured_at=task.context.captured_at, queue_entered=offered_at,
                   passage_entered_at=getattr(task, 'passage_entered_at', None),
                   first_usable_at=getattr(task, 'first_usable_at', None),
                   worker_started=time.monotonic(), selected_at=task.candidate_selected_at,
                   spans=[])
        previous = getattr(self._local, 'row', None)
        self._local.row = row
        try:
            yield
        finally:
            row['worker_finished'] = time.monotonic()
            self._local.row = previous
            with self._lock:
                self.dropped += len(self.rows) == self.rows.maxlen
                self.rows.append(row)

    def snapshot(self):
        with self._lock:
            return dict(rows=list(self.rows), dropped=self.dropped,
                        clock='monotonic seconds; spans may overlap, do not sum')


TRACES = ObservationTraces(os.environ.get('CBVMS_TRACE') == '1')


@contextmanager
def measure(name):
    if not PERF.enabled and not TRACES.enabled:
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        ended = time.monotonic()
        PERF.observe(name, ended-started)
        TRACES.span(name, started, ended)


@contextmanager
def timed_lock(lock, name):
    if not PERF.enabled and not TRACES.enabled:
        with lock:
            yield
        return
    started = time.monotonic()
    with lock:
        ended = time.monotonic()
        PERF.observe(name, ended-started)
        TRACES.span(name, started, ended)
        yield
