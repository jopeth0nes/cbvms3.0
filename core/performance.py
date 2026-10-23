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
            result[name] = dict(count=count, retained=len(samples), p50_ms=percentile(.5), p95_ms=percentile(.95))
        return result


PERF = PerformanceMetrics(os.environ.get("CBVMS_PERF") == "1")


@contextmanager
def measure(name):
    if not PERF.enabled:
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        PERF.observe(name, time.monotonic()-started)


@contextmanager
def timed_lock(lock, name):
    if not PERF.enabled:
        with lock:
            yield
        return
    started = time.monotonic()
    with lock:
        PERF.observe(name, time.monotonic()-started)
        yield
