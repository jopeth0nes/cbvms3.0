"""Opt-in camera diagnostic using the same shared-model startup path as main.py.

Use ``--manual`` for physical dropdown interaction. The diagnostic initializes
only a temporary database, does not start cloud sync, preserves camera preference
and credential lookup behavior, and suppresses all attendance/unknown writes.
It never writes frames, student data, embeddings, URLs, or exception messages.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import faulthandler
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from scripts.camera_switch_diagnostics import (
    trace_tk_callbacks, valid_result, write_failure_artifact,
)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", action="store_true", help="leave native source menu available")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--duration", type=float, help="seconds (default: manual 600, automatic 68)")
    parser.add_argument("--role", choices=("admin", "superadmin"), default="admin")
    parser.add_argument("--login-delay", type=float, default=3.0,
                        help="seconds to warm shared models before simulated login")
    return parser


def _trace(record, cls, name, folder):
    original = getattr(cls, name)
    sampled_reads = set()

    def wrapped(self, *args, **kwargs):
        started = time.monotonic()
        fields = dict(owner=cls.__name__, object_id=id(self),
                      camera_generation=getattr(self, "_camera_generation", None),
                      monitor_generation=getattr(self, "_monitor_generation", None))
        if name == "_set_camera_state" and args:
            fields["state"] = args[0]
        report_entry = name != "read" or id(self) not in sampled_reads
        if report_entry:
            record(name + "_enter", **fields)
        try:
            return original(self, *args, **kwargs)
        except BaseException as exc:
            if name == "_on_close":
                write_failure_artifact(folder, "close", exc)
                record("close_failed", error_kind=type(exc).__name__)
            raise
        finally:
            elapsed = (time.monotonic() - started) * 1000
            if name != "read" or report_entry or elapsed >= 250:
                sampled_reads.add(id(self))
                if cls.__name__ == "NetworkStream" and name == "release":
                    proc = self._proc
                    fields.update(decoder_reaped=proc is None or proc.poll() is not None,
                                  pipe_threads_stopped=not any(t.is_alive() for t in self._threads))
                if cls.__name__ == "NetworkStream":
                    from core.network_capture import MESSAGES
                    fields["error_code"] = self.error_code if self.error_code in MESSAGES else None
                fields.update(camera_generation=getattr(self, "_camera_generation", None),
                              monitor_generation=getattr(self, "_monitor_generation", None))
                record(name + "_return", ms=round(elapsed, 3), **fields)
    return wrapped


def _traced_lock(lock, record, lock_name):
    class TracedLock:
        def __init__(self, wrapped): self.wrapped = wrapped
        def acquire(self, *args, **kwargs):
            started = time.monotonic()
            record("model_lock_wait", lock=lock_name)
            acquired = self.wrapped.acquire(*args, **kwargs)
            record("model_lock_acquired", lock=lock_name, acquired=acquired,
                   wait_ms=round((time.monotonic()-started)*1000, 3))
            return acquired
        def release(self): return self.wrapped.release()
        def __enter__(self):
            self.acquire()
            return self
        def __exit__(self, *_): self.release()
    return TracedLock(lock)


def run(args) -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    folder = args.output_dir or Path("diagnostics/camera-freeze") / datetime.now(
        timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "timings.jsonl").exists():
        raise ValueError("output directory already contains a run")
    # Native FFmpeg can print camera URLs, so do not forward its stderr.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 2)
    log = (folder / "timings.jsonl").open("w", buffering=1)
    stacks = (folder / "stacks.txt").open("w", buffering=1)
    started = time.monotonic()
    print("Diagnostic output:", folder.resolve(), flush=True)
    logging_done = threading.Event()
    console_events = {"run_started", "run_finished", "run_failed", "first_frame",
                      "first_valid_analysis", "summary", "capture_owner_cleanup",
                      "analysis_cleanup", "startup_failed", "close_failed"}
    switch_clock = [None]

    def record(event, **fields):
        if logging_done.is_set():
            return
        if event == "_on_switch_camera_enter":
            switch_clock[0] = time.monotonic()
        row = dict(utc=datetime.now(timezone.utc).isoformat(),
                   t=round(time.monotonic() - started, 6), event=event,
                   thread=threading.current_thread().name, **fields)
        log.write(json.dumps(row) + "\n")
        if event in console_events:
            print(json.dumps(row), flush=True)

    phase = "imports"
    supervisor = None
    models = app = None
    gc_callback = None
    try:
        faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True)
        (folder / "heartbeat").write_text(str(time.monotonic()))
        supervisor = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("camera_freeze_watchdog.py")),
             str(os.getpid()), str(folder.resolve())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record("run_started", mode="physical_manual" if args.manual else "simulated_callback",
               role=args.role, performance_metadata={"enabled": os.environ.get("CBVMS_PERF") == "1",
               "instrumentation": "timings_jsonl"})

        from main import _warm_models
        from core.person_detector import PersonDetector
        from core.recognizer import FaceRecognizer
        from core.monitor_models import MonitorModels
        from ui.dashboard import CBVMSDashboard
        from core.camera import CameraCapture
        from database.db_manager import CBVMSDatabase
        from customtkinter.windows.widgets.ctk_optionmenu import CTkOptionMenu
        from customtkinter.windows.widgets.core_widget_classes.dropdown_menu import DropdownMenu
        from core.network_capture import NetworkStream
        import api.camera_store as camera_store
        import ui.dashboard as dashboard_module
        from ui.window_lifecycle import WorkspaceWindow
        from core.attendance_writer import AttendanceWriter
        from core.live_pipeline import LiveProcessor
        import gc

        phase = "startup"
        with tempfile.TemporaryDirectory(prefix="cbvms-switch-") as tmp, ExitStack() as ctx:
            from unittest.mock import patch
            last_completed = [None]
            ctx.enter_context(trace_tk_callbacks(record, last_completed))
            db = CBVMSDatabase(Path(tmp) / "diagnostic.db")
            db.initialize()
            db.get_user_role = lambda _username: args.role

            class TracedDeviceLock:
                def __init__(self, lock): self.lock = lock
                def acquire(self, *a, **kw):
                    then = time.monotonic(); record("device_lock_wait")
                    acquired = self.lock.acquire(*a, **kw)
                    record("device_lock_result", acquired=acquired,
                           ms=round((time.monotonic()-then)*1000, 3))
                    return acquired
                def release(self):
                    self.lock.release(); record("device_lock_released")

            ctx.enter_context(patch.object(
                dashboard_module, "CAMERA_DEVICE_LOCK",
                TracedDeviceLock(dashboard_module.CAMERA_DEVICE_LOCK)))
            ctx.enter_context(patch("api.camera_store.save_camera_preference",
                                    lambda pref: record("preference_saved",
                                                        source_type=pref.get("type"))))
            ctx.enter_context(patch.object(CBVMSDashboard, "_load_camera_preference",
                                           lambda self: None))
            ctx.enter_context(patch.object(AttendanceWriter, "offer",
                                           lambda self, *a, **k: record("attendance_write_suppressed")))
            ctx.enter_context(patch.object(LiveProcessor, "submit_unknowns",
                                           lambda self, *a, **k: record("unknown_write_suppressed")))
            ctx.enter_context(patch.object(LiveProcessor, "persist", lambda self, *a, **k: None))
            record("business_writers_suppressed", attendance=True, unknowns=True,
                   violations=True, recognition_enabled=True)
            for name in ("_on_switch_camera", "_commit_camera_switch", "_on_camera_source_connected",
                         "_deferred_start_camera", "_stop_camera", "_start_camera_worker",
                         "_on_camera_opened", "_on_nav_select", "_on_close", "_set_camera_state",
                         "_retry_camera", "_invalidate_monitor"):
                ctx.enter_context(patch.object(CBVMSDashboard, name,
                                               _trace(record, CBVMSDashboard, name, folder)))
            for cls, names in ((CTkOptionMenu, ("_open_dropdown_menu", "_dropdown_callback")),
                               (DropdownMenu, ("open", "unpost", "_button_callback", "destroy")),
                               (WorkspaceWindow, ("destroy",)),
                               (NetworkStream, ("_open_owned", "release")),
                               (CameraCapture, ("open", "read", "release"))):
                for name in names:
                    if hasattr(cls, name):
                        ctx.enter_context(patch.object(cls, name, _trace(record, cls, name, folder)))
            resolver = camera_store.resolve_camera_source
            def traced_resolve(*a, **kw):
                record("credential_resolution_enter")
                try: return resolver(*a, **kw)
                finally: record("credential_resolution_return")
            ctx.enter_context(patch.object(camera_store, "resolve_camera_source", traced_resolve))

            # Match main.py's ownership boundary without authenticating: one
            # lightweight Tk root starts preparation, then is destroyed before
            # main-thread collection and dashboard construction.
            recognizer = FaceRecognizer(db)
            person_detector = PersonDetector()
            models = MonitorModels(recognizer, person_detector)
            for lock_owner, lock_name in ((recognizer, "detector"),
                                          (recognizer, "recognition"),
                                          (person_detector, "person"),
                                          (models.trainer, "trainer")):
                field = "_model_lock" if lock_name in ("person", "trainer") else f"_{lock_name}_lock"
                setattr(lock_owner, field, _traced_lock(getattr(lock_owner, field), record, lock_name))
            login_root = WorkspaceWindow()
            login_root.title("CBVMS CAMERA DIAGNOSTIC — simulated login")
            login_root.withdraw()
            def callback_error(*args):
                error = args[1] if len(args) > 1 else None
                record("callback_error", kind=type(error).__name__ if error else "Exception")
                write_failure_artifact(folder, "tk_callback", error or RuntimeError())
            login_root.report_callback_exception = callback_error
            gc_callback = lambda phase_name, info: record(
                "gc_activity", phase=phase_name, generation=info.get("generation"),
                collected=info.get("collected"), uncollectable=info.get("uncollectable"))
            gc.callbacks.append(gc_callback)
            def login_heartbeat():
                (folder / "heartbeat").write_text(str(time.monotonic()))
                login_root.after(50, login_heartbeat)

            def start_warmup():
                threading.Thread(target=_warm_models, args=(models,), daemon=True,
                                 name="login-model-preparation").start()
                record("login_model_warmup_started")

            login_root.after(0, start_warmup)
            login_root.after(50, login_heartbeat)
            login_root.after(int(args.login_delay*1000), login_root.destroy)
            login_root.mainloop()
            # Like main.py: return from the old Tcl mainloop before collecting or
            # constructing another root, with the same shared preparation owner.
            collected = gc.collect()
            record("simulated_login_complete", authentication="not_exercised",
                   gc_collected=collected)
            app = CBVMSDashboard(username="diagnostic", database=db,
                                 recognizer=recognizer, person_detector=person_detector,
                                 monitor_models=models)
            if app._monitor_models is not models:
                raise RuntimeError("dashboard did not retain shared MonitorModels")
            app.title("CBVMS CAMERA DIAGNOSTIC — disposable database")
            app.report_callback_exception = callback_error
            gaps, heartbeat = [], [time.monotonic()]
            seqs, first_analysis, readiness_failures, readiness_states = {}, set(), set(), {}
            previous = [time.monotonic(), time.process_time()]

            def tick():
                now = time.monotonic()
                (folder / "heartbeat").write_text(str(now))
                gaps.append(now - heartbeat[0]); heartbeat[0] = now
                record("heartbeat", gap_ms=round(gaps[-1]*1000, 3),
                       last_completed_callback=last_completed[0])
                sample = app._latest_monitor_sample()
                if sample is not None:
                    token, sequence = sample.frame_id
                    camera_gen, monitor_gen = app._camera_generation, app._monitor_generation
                    key = (token, camera_gen)
                    if key not in seqs:
                        seqs[key] = [now, sequence, now, sequence]
                        record("first_frame", camera_generation=camera_gen,
                               monitor_generation=monitor_gen,
                               switch_ms=None if switch_clock[0] is None else round((now-switch_clock[0])*1000, 3),
                               source_type=(app._camera_active_source or {}).get("type"))
                    else:
                        seqs[key][2:] = [now, sequence]
                    result = app._monitor_result
                    analysis_key = (token, camera_gen, monitor_gen)
                    if analysis_key not in first_analysis and valid_result(
                            result, camera_gen, monitor_gen, token):
                        first_analysis.add(analysis_key)
                        record("first_valid_analysis", camera_generation=camera_gen,
                               monitor_generation=monitor_gen,
                               switch_ms=None if switch_clock[0] is None else round((now-switch_clock[0])*1000, 3),
                               source_type=(app._camera_active_source or {}).get("type"))
                for model_name, state in app._readiness.snapshot().items():
                    if readiness_states.get(model_name) != state.state:
                        readiness_states[model_name] = state.state
                        record("model_readiness", model=model_name, state=state.state,
                               elapsed=round(state.elapsed, 3))
                    if state.state == "failed" and model_name not in readiness_failures:
                        readiness_failures.add(model_name)
                        write_failure_artifact(folder, "model_readiness", RuntimeError(model_name))
                        record("model_readiness_failed", model=model_name)
                if now - previous[0] >= 1:
                    record("metrics", max_heartbeat_gap_ms=round(max(gaps)*1000, 2),
                           preview=app._measured_rate(app._preview_times, now),
                           tracking=app._measured_rate(app._tracking_times, now),
                           analysis=app._measured_rate(app._analysis_times, now),
                           parent_cpu_percent=100*(time.process_time()-previous[1])/(now-previous[0]),
                           camera_generation=app._camera_generation,
                           monitor_generation=app._monitor_generation,
                           camera_state=app._camera_state, nav=app._active_nav)
                    previous[:] = [now, time.process_time()]
                app.after(50, tick)

            def switch(label):
                record("selection_requested", source_type="usb" if label == "MacBook Camera" else "ip")
                app._cam_switcher._dropdown_callback(label)

            def close():
                record("summary", max_heartbeat_gap_ms=round(max(gaps, default=0)*1000, 2),
                       sources=[{"seconds": row[2]-row[0],
                                 "fps": (row[3]-row[1])/max(.001, row[2]-row[0])}
                                for row in seqs.values()])
                try:
                    app._on_close()
                except BaseException as exc:
                    write_failure_artifact(folder, "close", exc)
                    record("close_failed", error_kind=type(exc).__name__)

            app.after(50, tick)
            if args.manual:
                print("Wait for moving MacBook preview and nonzero Analysis. Physically open Source and select Front Gate. If frozen after selection/dismissal, leave it untouched for 25 seconds for stack/sample capture. If responsive, switch back to MacBook. Close the diagnostic window when finished.", flush=True)
            else:
                gate = next((key for key in app._cam_sources if key.casefold() == "front gate"), None)
                usb = next((key for key, source in app._cam_sources.items()
                            if source.get("type") == "usb" and source.get("index") == 0), None)
                if gate is None or usb is None:
                    record("automatic_sources_missing", usb_missing=usb is None, gate_missing=gate is None)
                    raise RuntimeError("required diagnostic source unavailable")
                for ms, label in ((15000, gate), (27000, usb), (39000, gate), (51000, usb),
                                  (56000, gate), (56100, usb), (59000, gate)):
                    app.after(ms, lambda label=label: switch(label))
                app.after(59100, lambda: app._on_nav_select("enrollment"))
                app.after(60000, lambda: app._on_nav_select("live"))
            app.after(int(args.duration*1000), close)
            phase = "dashboard"
            app.mainloop()
            cleanup(app, models, record)
            app = models = None
        record("run_finished")
        (folder / "finished").touch()
        return 0
    except BaseException as exc:
        write_failure_artifact(folder, phase, exc)
        record("run_failed", phase=phase, error_kind=type(exc).__name__)
        (folder / "finished").touch()
        return 1
    finally:
        if app is not None or models is not None:
            cleanup(app, models, record)
        if gc_callback is not None:
            gc.callbacks.remove(gc_callback)
        (folder / "finished").touch()
        if supervisor is not None:
            try:
                supervisor.wait(timeout=3)
            except subprocess.TimeoutExpired:
                supervisor.terminate()
                supervisor.wait(timeout=3)
        logging_done.set()
        faulthandler.unregister(signal.SIGUSR1)
        log.close()
        stacks.close()


def cleanup(app, models, record):
    """Wait only after leaving Tk's loop; retain a single shared shutdown bound."""
    if app is not None and not app._closed.is_set():
        app._on_close()
    if models is not None:
        models.cancel_pending()
    deadline = time.monotonic() + 5
    if app is not None:
        record("capture_owner_cleanup", done=app._camera_worker_done.wait(max(0, deadline-time.monotonic())))
        workers = [("analysis", app._live_worker.done), ("tracking", app._tracking_worker.done),
                   ("persistence", app._live_worker.writes_done)]
        for name, worker in (("attendance", app._live_worker.attendance_writer),
                             ("unknown", app._live_worker.processor.security_writer)):
            if worker is not None:
                workers.append((name, worker.done))
        for name, done in workers:
            record("analysis_cleanup", worker=name, done=done.wait(max(0, deadline-time.monotonic())))
    if models is not None:
        models.wait(max(0, deadline-time.monotonic()))
        record("model_cleanup", running=sum(t.is_alive() for t in models.readiness._threads.values()))


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.duration is None:
        args.duration = 600. if args.manual else 68.
    if not math.isfinite(args.duration) or not 0 < args.duration <= 600:
        parser.error("duration must be greater than zero and at most 600 seconds")
    if not math.isfinite(args.login_delay) or not 0 <= args.login_delay <= 120:
        parser.error("login delay must be between 0 and 120 seconds")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
