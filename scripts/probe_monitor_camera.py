"""Consented MacBook/USB camera probe with an ephemeral in-memory test identity.

No database, persistence worker, alerts, image files, or embedding files. Only
aggregate numeric measurements are written. This is not an accuracy evaluation.
"""
import argparse
from collections import Counter, deque
import json
from pathlib import Path
import queue
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ProbeRoster:
    student = dict(student_id="SYNTHETIC-CAMERA-PROBE", name="Test participant",
                   gender="Unspecified", student_status="Enrolled", registration_pending=False)
    def get_all_students(self): return []
    def recognition_capture_encodings(self): return {}
    def attendance_term_snapshot(self, **kwargs): return {}
    def get_student_by_student_id(self, sid): return dict(self.student) if sid == self.student["student_id"] else None
    def enrollment_context(self, sid, **kwargs):
        return dict(eligible=True, status="Synthetic test eligibility", enrollment={}, term={})
    def get_active_suspension(self, sid): return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consenting-participant", action="store_true", required=True)
    parser.add_argument("--seconds", type=float, default=35.)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    import tkinter as tk
    import cv2
    import numpy as np
    import psutil
    from core.camera import CameraCapture
    from core.live_pipeline import LiveProcessor, FaceTrackingProcessor, LiveWorker, MonitorTask
    from core.live_state import FrameContext
    from core.monitor_models import MonitorModels
    from core.recognizer import FaceRecognizer
    from core.person_detector import PersonDetector
    from core.performance import PERF, measure
    from ui.camera_feed import CameraFeed

    cv2.setNumThreads(2)
    PERF.enabled = True
    PERF.start_monitor()
    started = time.monotonic()
    cancelled = threading.Event()
    capture_done = threading.Event()
    capture = CameraCapture(camera_index=args.camera_index, width=1280, height=720,
                            fps_cap=30, cancel_event=cancelled)
    captured = deque(maxlen=4096)
    camera_first = [None]
    def camera_loop():
        try:
            if not capture.open(): return
            camera_first[0] = time.monotonic()-started
            while not cancelled.is_set():
                if capture.read() is not None: captured.append(time.monotonic())
        finally:
            capture.release()
            capture_done.set()
    reader = threading.Thread(target=camera_loop, daemon=True)
    reader.start()
    roster = ProbeRoster()
    recognizer, detector = FaceRecognizer(roster), PersonDetector()
    models = MonitorModels(recognizer, detector)
    models.start()
    calibrated = threading.Event()
    calibration_done = threading.Event()
    first_detection = [None]
    calibration_at = [None]
    def calibrate():
        try:
            while not cancelled.wait(.02):
                if not (models.readiness.ready("face") and models.readiness.ready("recognition")): continue
                sample = capture.get_latest_sample()
                if sample is None or time.monotonic()-sample.captured_at > 1: continue
                rows = recognizer.detect_faces(sample.frame)
                if rows and first_detection[0] is None: first_detection[0] = time.monotonic()-started
                if len(rows) != 1: continue
                embeddings = recognizer._embed_detections(sample.frame, rows)
                if not embeddings or time.monotonic()-sample.captured_at > 3: continue
                with recognizer._lock:
                    recognizer._known = [(dict(roster.student), np.asarray([embeddings[0][1]], np.float32))]
                calibration_at[0] = time.monotonic()-started
                calibrated.set()
                return
        finally:
            calibration_done.set()
    calibration = threading.Thread(target=calibrate, daemon=True)
    calibration.start()
    processor = LiveProcessor(roster, recognizer, detector, models.trainer, models.uniform_matcher, None)
    processor.readiness = models.readiness
    # No side-effect API is invoked, including unknown sightings or attendance.
    processor.persist = lambda result: None
    assessment = LiveWorker(processor)
    last_offer = [0.]
    def localized(result):
        if result.task.context.captured_at-last_offer[0] >= .5:
            assessment.offer(result.task.with_observations(result.observations))
            last_offer[0] = result.task.context.captured_at
    tracking = LiveWorker(FaceTrackingProcessor(recognizer, detector, models.readiness, localized))
    assessment.start(); tracking.start()
    root = tk.Tk()
    root.title("CBVMS temporary camera check — no records or images saved")
    root.geometry("960x650")
    feed = CameraFeed(root); feed.pack(fill="both", expand=True)
    status = tk.Label(root, text="Preparing models; stay centered with shoulders visible")
    status.pack(fill="x")
    root.protocol("WM_DELETE_WINDOW", cancelled.set)
    last_frame = None
    offered = None
    next_offer = 0.
    rendered = deque(maxlen=4096)
    counts = dict(tracking=0, assessments=0, stable_identity_frames=0, valid_torso_frames=0,
                  uniform_assessment_frames=0, identity_uncertain_frames=0)
    first = {}
    reasons = Counter()
    resolution = None
    process = psutil.Process()
    rss = []
    try:
        while not cancelled.is_set() and time.monotonic()-started < args.seconds:
            now = time.monotonic()
            root.update()
            sample = capture.get_latest_sample()
            if sample is not None and sample.frame_id != last_frame:
                resolution = [sample.frame.shape[1], sample.frame.shape[0]]
                with measure("ui_render"):
                    feed.render(cv2.flip(sample.frame, 1))
                rendered.append(time.monotonic())
                PERF.observe("preview_age", time.monotonic()-sample.captured_at)
                last_frame = sample.frame_id
            if calibrated.is_set() and sample is not None and now >= next_offer and sample.frame_id != offered:
                task = MonitorTask(FrameContext(1, sample.frame_id, sample.captured_at), sample.frame,
                                   sample.captured_wall_time or time.time(), cancelled, earring_enabled=False)
                tracking.offer(task)
                offered, next_offer = sample.frame_id, now+.1
            for kind, worker in (("tracking", tracking), ("assessments", assessment)):
                try: result = worker.results.get_nowait()
                except queue.Empty: continue
                counts[kind] += 1
                if kind == "tracking": continue
                items = result.assessments
                reasons.update(a.reason for a in items)
                flags = {"stable_identity": any(a.reliable_identity for a in items),
                         "valid_torso": any(a.torso_box for a in items),
                         "uniform_assessment": any(a.uniform_label for a in items),
                         "identity_uncertain": any(not a.reliable_identity for a in items)}
                for name, present in flags.items():
                    if present:
                        counts[name+"_frames"] += 1
                        first.setdefault(name, round(time.monotonic()-started, 3))
                status.configure(text=" · ".join(dict.fromkeys(a.state for a in items)) or result.detail)
            rss.append(process.memory_info().rss/2**20)
            time.sleep(.01)
    finally:
        cancelled.set(); tracking.stop(); assessment.stop(); models.cancel_pending()
        tracking.done.wait(5); assessment.done.wait(5); calibration_done.wait(5); capture_done.wait(5); models.wait(5)
        with recognizer._lock: recognizer._known = []
        feed.cleanup(); root.destroy()
    elapsed = time.monotonic()-started
    rate = lambda values: round((len(values)-1)/(values[-1]-values[0]), 2) if len(values)>1 else 0.
    report = dict(kind="consented_physical_camera_temporary_identity", elapsed_seconds=round(elapsed, 3),
        camera_first_frame_seconds=camera_first[0], first_face_seconds=first_detection[0],
        temporary_identity_seconds=calibration_at[0], first_results_seconds=first,
        capture_fps=rate(captured), preview_unique_fps=rate(rendered), counts=counts,
        resolution=resolution, assessment_reasons=dict(reasons),
        models={k: v.state for k,v in models.readiness.snapshot().items()},
        camera_error=capture.last_error, worker_errors=[tracking.last_error, assessment.last_error],
        workers_stopped=tracking.done.is_set() and assessment.done.is_set() and capture_done.is_set() and calibration_done.is_set(),
        rss_peak_mb=round(max(rss), 2) if rss else None, timings=PERF.snapshot(),
        business_writes=0, images_saved=0, embeddings_saved=0, accuracy_evaluated=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
