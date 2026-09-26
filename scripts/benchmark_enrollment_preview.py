"""Time the real dashboard enrollment preview with a synthetic 720p/30 FPS source.

Runs EnrollmentPanel._wizard_tick and _render_capture, including OpenCV resizing,
guide drawing, optical flow, PIL conversion, and background inference scheduling.
Replaces camera acquisition, 200 ms model inference, Tk widgets, and Tk image
upload. This is a headless scheduling benchmark, NOT a real camera/Tk benchmark.

Preview-only deliberately pauses validation to measure preview independence.
Use --before-source /tmp/old_enrollment.py to measure the same harness against
an earlier implementation; no database is opened or modified.
"""

import argparse
import heapq
import importlib.util
import json
from pathlib import Path
import queue
import sys
import threading
import time
from types import FunctionType, SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.camera import CameraSample
from core.face_capture import CaptureSession
from ui import enrollment


class SyntheticSource:
    """Publish independently acquired, uniquely numbered samples at 30 FPS."""

    def __init__(self):
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.frame[180:540, 460:820] = np.random.default_rng(4).integers(
            0, 255, (360, 360, 3), dtype=np.uint8
        )
        self.sample = None
        self.times = {}
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._pump, daemon=True)

    def _pump(self):
        deadline = time.monotonic()
        sequence = 0
        while not self.stop_event.is_set():
            deadline += 1 / 30
            if self.stop_event.wait(max(0, deadline - time.monotonic())):
                break
            sequence += 1
            frame = self.frame.copy()
            # A corner marker identifies raw frames without retaining every image.
            frame[0, 0] = (sequence % 256, (sequence // 256) % 256, 0)
            acquired = time.monotonic()
            self.times[sequence] = acquired
            self.sample = CameraSample(frame, ("synthetic", sequence), acquired)

    def latest(self):
        return self.sample

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)


class Widget:
    """Keep widget calls cheap while retaining configuration/update counts."""

    def __init__(self):
        self.configurations = 0

    def winfo_exists(self):
        return True

    def configure(self, **kwargs):
        self.configurations += 1

    def create_image(self, *args, **kwargs):
        return 1

    def itemconfig(self, *args, **kwargs):
        pass

    def delete(self, *args):
        pass


def percentile(values, quantile=95):
    return round(float(np.percentile(values, quantile)), 2) if len(values) else None


def run(seconds=5, processing=True, before=None):
    module = enrollment
    if before is not None:
        spec = importlib.util.spec_from_file_location("enrollment_baseline", before)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    source = SyntheticSource()
    jobs = []
    queued_peak = 0
    job_sequence = 0

    def after(delay, callback):
        nonlocal queued_peak, job_sequence
        job_sequence += 1
        heapq.heappush(jobs, (time.monotonic() + delay / 1000, job_sequence, callback))
        queued_peak = max(queued_peak, len(jobs))
        return job_sequence

    modal = Widget()
    modal.after = after
    modal.after_cancel = lambda job: None
    student_key = ("update", 7, "S7")
    panel = SimpleNamespace(
        _selected_pk=7,
        get_frame_sample=source.latest,
        get_frame=lambda: source.latest().frame if source.latest() else None,
        _capture_inference_lock=threading.Lock(),
    )
    # Bind project helpers too, so the benchmark follows the production flow.
    for name, method in module.EnrollmentPanel.__dict__.items():
        if isinstance(method, staticmethod):
            setattr(panel, name, getattr(module.EnrollmentPanel, name))
        elif isinstance(method, FunctionType):
            setattr(panel, name, method.__get__(panel))

    state = dict(
        alive=True, modal=modal, target_pk=7, target_student_id="S7",
        student_key=student_key, step=0, session=CaptureSession(student_key),
        angle_frames={}, results=queue.Queue(), worker_busy=False,
        reviewing=False, capturing=False, preview_w=520, preview_h=390,
        mirror=SimpleNamespace(display_mirror=lambda: True), canvas=Widget(),
        canvas_item=None, img=None, cap_btn=Widget(), skip_btn=Widget(),
        dot=Widget(), det_status=Widget(), requested_at=0,
    )
    if hasattr(panel, "_init_wizard_preview"):
        panel._init_wizard_preview(state)
    # Deliberately paused inference is the preview-only condition in both versions.
    state["worker_busy"] = not processing
    inference_calls = []
    active = 0
    peak_active = 0
    inference_lock = threading.Lock()
    inference_done = threading.Event()
    inference_done.set()

    def detect(frame):
        nonlocal active, peak_active
        with inference_lock:
            active += 1
            peak_active = max(peak_active, active)
            inference_calls.append(time.monotonic())
            inference_done.clear()
        try:
            time.sleep(.2)
            embedding = np.zeros(512, np.float32)
            embedding[0] = 1
            return [([460, 180, 820, 540], embedding, .9, None)]
        finally:
            with inference_lock:
                active -= 1
                if not active:
                    inference_done.set()

    panel.recognizer = SimpleNamespace(enrollment_faces=detect)
    render = panel._render_capture
    rendered = []

    def record_render(state, frame, *args, **kwargs):
        render(state, frame, *args, **kwargs)
        if not kwargs.get("frozen"):
            sequence = int(frame[0, 0, 0]) + int(frame[0, 0, 1]) * 256
            latest = source.latest()
            rendered.append((time.monotonic(), sequence, latest.frame_id[1]))

    panel._render_capture = record_render
    tick_times = []
    started = time.monotonic()
    source.thread.start()
    try:
        with patch.object(module.ImageTk, "PhotoImage", side_effect=lambda **kw: object()):
            panel._wizard_tick(state)
            while time.monotonic() - started < seconds:
                if jobs and jobs[0][0] <= time.monotonic():
                    _, _, callback = heapq.heappop(jobs)
                    tick_started = time.monotonic()
                    callback()
                    tick_times.append((time.monotonic() - tick_started) * 1000)
                else:
                    time.sleep(.001)
    finally:
        elapsed = time.monotonic() - started
        state["alive"] = False
        if "stop_event" in state:
            state["stop_event"].set()
        source.stop()
        inference_done.wait(2)

    unique = []
    for record in rendered:
        if not unique or unique[-1][1] != record[1]:
            unique.append(record)
    gaps = np.diff([at for at, _, _ in unique]) * 1000
    source_times = list(source.times.values())
    source_fps = ((len(source_times) - 1) / (source_times[-1] - source_times[0])
                  if len(source_times) > 1 else 0)
    ages = [(at - source.times[sequence]) * 1000 for at, sequence, _ in unique]
    frames_behind = [latest - sequence for _, sequence, latest in unique]
    return dict(
        version="before" if before else "after",
        mode="validation" if processing else "preview_only",
        elapsed_seconds=round(elapsed, 2), source_fps=round(source_fps, 2),
        rendered_unique_frames=len(unique), preview_fps=round(len(unique) / elapsed, 2),
        preview_gap_p95_ms=percentile(gaps), rendered_frame_age_p95_ms=percentile(ages),
        frames_behind_source_p95=percentile(frames_behind),
        frame_sequence_rewinds=sum(b[1] < a[1] for a, b in zip(unique, unique[1:])),
        ui_tick_p95_ms=percentile(tick_times), inference_calls=len(inference_calls),
        max_inference_inflight=peak_active, max_queued_ui_ticks=queued_peak,
        status_widget_updates=state["det_status"].configurations,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--before-source", type=Path)
    args = parser.parse_args()
    for enabled in (False, True):
        print(json.dumps(run(args.seconds, enabled, args.before_source)), flush=True)
