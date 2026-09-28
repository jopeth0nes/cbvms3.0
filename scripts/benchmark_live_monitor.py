"""Synthetic Live Monitor render benchmark; never opens a camera or database.

Compare the actual committed CameraFeed.render / dashboard._annotate_frame with
current CameraFeed.render / draw_assessments on the same 720p/30 Hz synthetic
source, with one or five assessed people. Real OpenCV/PIL rendering runs; native
Tk widgets and PhotoImage upload are replaced with no-ops by default.
Use --native-tk to include actual desktop image upload and drawing. The before condition
uses the prior fixed after-work delay, the after condition subtracts render time
and skips repeated source frames, matching the dashboard scheduling changes.

The processing condition runs the current bounded LiveWorker in BOTH versions,
using repeated Gaussian blurs for --analysis-ms of CPU work per analysis task.
It tests preview isolation under controlled load, not real model accuracy or a
full reconstruction of the old dual inference pipeline. Baseline source is read
from git only. Rates cannot establish physical-camera or native-Tk performance.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import ast
import json
from pathlib import Path
import platform
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from PIL import ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.live_pipeline import LiveWorker, MonitorResult, MonitorTask
from core.live_state import FrameContext
from scripts.benchmark_enrollment_preview import SyntheticSource
from ui.camera_feed import CameraFeed
from ui.live_overlay import draw_assessments


class Canvas:
    """No Tk root; keep actual resize/color/PIL work in project render methods."""
    def __init__(self):
        self._photo = self._item = self._placeholder_item = None
        self._placeholder_key = self._geometry_key = None
        self._frame_rect = (0, 0, 0, 0)

    def winfo_width(self):
        return 960

    def winfo_height(self):
        return 600

    def create_image(self, *args, **kwargs):
        return 1

    def itemconfig(self, *args, **kwargs):
        pass

    def coords(self, *args, **kwargs):
        pass


def baseline(ref):
    root = Path(__file__).resolve().parents[1]
    camera_source = subprocess.check_output(["git", "show", f"{ref}:ui/camera_feed.py"], cwd=root, text=True)
    namespace = {"__name__": "camera_feed_baseline"}
    exec(compile(camera_source, "camera_feed_baseline", "exec"), namespace)
    old_render = namespace["CameraFeed"].render
    dashboard_source = subprocess.check_output(["git", "show", f"{ref}:ui/dashboard.py"], cwd=root, text=True)
    tree = ast.parse(dashboard_source)
    dashboard_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CBVMSDashboard")
    methods = [node for node in dashboard_class.body
               if isinstance(node, ast.FunctionDef) and node.name in ("_annotate_frame", "_draw_pill")]
    for node in methods:
        node.decorator_list = []
    namespace = {"cv2": cv2, "np": np, "ORANGE_BGR": (0, 165, 255)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), "dashboard_baseline", "exec"), namespace)
    return old_render, namespace["_annotate_frame"], namespace["_draw_pill"]


class ControlledProcessor:
    def __init__(self, analysis_ms):
        self.seconds = analysis_ms / 1000
        self.started = []
        self.completed = []
        self.active = 0
        self.peak_active = 0

    def analyze(self, task):
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        self.started.append(time.monotonic())
        deadline = time.monotonic() + self.seconds
        try:
            while time.monotonic() < deadline:
                cv2.GaussianBlur(task.frame, (9, 9), 0)
            self.completed.append(time.monotonic())
            return MonitorResult(task, (), time.monotonic())
        finally:
            self.active -= 1

    def persist(self, result):
        pass  # No database object exists in this benchmark.


def percentile(values):
    return round(float(np.percentile(values, 95)), 3) if values else None


def run(seconds, people, processing, version, old, analysis_ms, native_tk=False):
    source = SyntheticSource()
    root = None
    if native_tk:
        import tkinter as tk
        root = tk.Tk()
        root.title("CBVMS synthetic preview benchmark — no camera")
        root.geometry("960x600")
        canvas = CameraFeed(root)
        canvas.pack(fill="both", expand=True)
        root.update()
    else:
        canvas = Canvas()
    rows = []
    tracks = []
    for index in range(people):
        box = (25 + index * 230, 90, 125 + index * 230, 205)
        torso = (5 + index * 230, 215, 195 + index * 230, 560)
        rows.append(dict(track_id=index + 1, presence_id=f"bench:{index+1}", face_box=box,
                         torso_box=torso, name=f"Student {index+1}", state="Uniform compliant",
                         student_id=f"S{index+1}"))
        tracks.append(SimpleNamespace(identified=True, matched=True, stable_uniform_label="correct_uniform",
                                      stable_uniform_conf=.95, name=f"Student {index+1}",
                                      student_status="Enrolled", violation=None, suspension_tag="",
                                      torso_box=torso, box_int=lambda box=box: box))
    dashboard = SimpleNamespace(_tracker=SimpleNamespace(renderable=lambda: tracks), _draw_pill=old[2])
    processor = ControlledProcessor(analysis_ms)
    worker = LiveWorker(processor) if processing else None
    cancelled = threading.Event()
    if worker:
        worker.start()
    source.thread.start()
    rendered = []
    durations = []
    max_queued = 0
    offered = None
    last_rendered = None
    next_offer = 0
    started = time.monotonic()
    deadline = started
    try:
        image_upload = (nullcontext() if native_tk else
                        patch.object(ImageTk, "PhotoImage", side_effect=lambda *args, **kwargs: object()))
        with image_upload:
            while time.monotonic() - started < seconds:
                delay = deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                tick = time.monotonic()
                sample = source.latest()
                if sample is not None:
                    if worker and tick >= next_offer and sample.frame_id != offered:
                        worker.offer(MonitorTask(FrameContext(1, sample.frame_id, sample.captured_at),
                                                 sample.frame.copy(), time.time(), cancelled))
                        offered = sample.frame_id
                        next_offer = tick + .2
                        max_queued = max(max_queued, worker.requests.qsize())
                    if worker:
                        try:
                            worker.results.get_nowait()
                        except queue.Empty:
                            pass
                    if version == "before" or sample.frame_id != last_rendered:
                        if version == "before":
                            annotated = old[1](dashboard, sample.frame, mirror=True)
                            old[0](canvas, annotated)
                        else:
                            annotated = draw_assessments(sample.frame, rows, mirror=True)
                            CameraFeed.render(canvas, annotated)
                        rendered.append((time.monotonic(), sample.frame_id[1], sample.captured_at))
                        last_rendered = sample.frame_id
                if root is not None:
                    root.update()
                done = time.monotonic()
                durations.append((done - tick) * 1000)
                deadline = done + .033 if version == "before" else tick + .033
    finally:
        elapsed = time.monotonic() - started
        cancelled.set()
        source.stop()
        if root is not None:
            canvas.cleanup()
            root.destroy()
        if worker:
            worker.stop()
            worker.thread.join(timeout=3)
            if not worker.done.is_set():
                raise RuntimeError("Analysis worker failed to terminate")
    unique = [item for index, item in enumerate(rendered) if index == 0 or rendered[index - 1][1] != item[1]]
    gaps = [1000 * (b[0] - a[0]) for a, b in zip(unique, unique[1:])]
    source_times = list(source.times.values())
    source_fps = (len(source_times) - 1) / (source_times[-1] - source_times[0]) if len(source_times) > 1 else 0
    completed = [at for at in processor.completed if at < started + elapsed]
    return dict(version=version, people=people, mode="controlled_analysis" if processing else "preview_only",
                elapsed_seconds=round(elapsed, 2), source_fps=round(source_fps, 2),
                preview_unique_fps=round(len(unique) / elapsed, 2), preview_gap_p95_ms=percentile(gaps),
                render_tick_p95_ms=percentile(durations),
                frame_age_p95_ms=percentile([(at-captured)*1000 for at, _, captured in unique]),
                completed_analysis_hz=round(len(completed) / elapsed, 2),
                max_analysis_inflight=processor.peak_active, max_analysis_queued=max_queued,
                worker_stopped=worker.done.is_set() if worker else True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-tk", action="store_true", help="include native desktop image upload and drawing")
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--people", type=int, nargs="+", choices=range(1, 6), default=[1, 5])
    parser.add_argument("--analysis-ms", type=float, default=200)
    parser.add_argument("--baseline-ref", default="6f2e611",
                        help="committed pre-fix render path (default: 6f2e611)")
    args = parser.parse_args()
    if args.seconds <= 0 or args.analysis_ms <= 0:
        parser.error("seconds and analysis-ms must be positive")
    print(json.dumps(dict(kind="synthetic_native_tk" if args.native_tk else "synthetic_headless", machine=platform.machine(), system=platform.system(),
                          baseline_ref=args.baseline_ref, analysis_ms=args.analysis_ms,
                          native_tk=args.native_tk, real_camera=False, database=False)), flush=True)
    old = baseline(args.baseline_ref)
    for people in args.people:
        for processing in (False, True):
            for version in ("before", "after"):
                print(json.dumps(run(args.seconds, people, processing, version, old, args.analysis_ms, args.native_tk)), flush=True)
