"""Isolated on-device benchmark. No images, identities or production writes.

Uses actual camera acquisition (or explicitly synthetic frames) and native Tk. Full mode uses the actual dashboard
with an empty disposable roster and suppressed business writers. Busy contention
uses the incremental SyncWorker with disposable SQLite and simulated receipts,
NOT a production/network synchronization benchmark.
Times begin after --warmup; acquisition timestamps are post-read, not sensor PTS.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def distribution(values):
    import numpy as np
    return {f'p{p}': round(float(np.percentile(values, p)), 3)
            for p in (1, 50, 95, 99)} if values else {}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('capture', 'preview', 'yolo', 'full'), required=True)
    p.add_argument('--source', choices=('usb', 'front-gate', 'synthetic'), default='usb')
    p.add_argument('--render-ref', help='Load render and (in full mode) feed callback from a local Git revision')
    p.add_argument('--fps', type=int, choices=(20, 30, 60), default=30)
    p.add_argument('--width', type=int, default=1280)
    p.add_argument('--height', type=int, default=720)
    p.add_argument('--seconds', type=float, default=600)
    p.add_argument('--warmup', type=float, default=15)
    p.add_argument('--busy', action='store_true')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    def power_state():
        return {name: subprocess.run(['pmset', '-g', name], capture_output=True,
                    text=True, timeout=5).stdout for name in ('custom', 'batt')}
    power_before = power_state()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    progress = args.output.with_suffix('.progress.json')
    progress.write_text(json.dumps({'phase': 'imports'}))
    source_hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
        for name in ('core/camera.py', 'core/live_pipeline.py', 'core/assessment_candidates.py',
                     'core/person_detector.py', 'core/recognizer.py', 'ui/dashboard.py',
                     'core/performance.py', 'core/trainer.py',
                     'ui/camera_feed.py', 'scripts/benchmark_camera_runtime.py')}
    if not 0 < args.seconds <= 3600 or args.warmup < 0:
        p.error('seconds must be in (0, 3600], warmup nonnegative')
    if args.busy and args.mode != 'full':
        p.error('--busy requires --mode full')
    # Native decoders may print URLs. Suppress stdout/stderr from dependencies;
    # output is the allowlisted numeric report only.
    with open(os.devnull, 'w') as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    import cv2
    import psutil
    from core.camera import CameraCapture
    from core.performance import PERF, TRACES, NAMES
    from ui.camera_feed import CameraFeed, next_preview_tick
    cv2.setNumThreads(2)
    PERF.enabled = True
    stop = threading.Event()
    events = defaultdict(lambda: deque(maxlen=250000))
    costs = defaultdict(lambda: deque(maxlen=250000))
    rows, settings, errors, opened_sources = [], {}, [], []
    process = psutil.Process()
    origin = [float('inf')]
    def timed(cls, name, metric, stack):
        original = getattr(cls, name)
        def call(self, *a, **kw):
            before = time.monotonic()
            try:
                return original(self, *a, **kw)
            finally:
                if before >= origin[0]:
                    costs[metric].append((time.monotonic()-before)*1000)
        stack.enter_context(patch.object(cls, name, call))

    with tempfile.TemporaryDirectory(prefix='cbvms-fps-') as folder, ExitStack() as stack:
        if args.render_ref:
            namespace = {'__name__': 'benchmark_camera_feed_baseline'}
            code = subprocess.check_output(['git', 'show', args.render_ref+':ui/camera_feed.py'], text=True)
            exec(compile(code, 'camera_feed_baseline', 'exec'), namespace)
            stack.enter_context(patch.object(CameraFeed, 'render', namespace['CameraFeed'].render))
        if args.source == 'synthetic':
            import numpy as np
            class SyntheticBackend:
                def __init__(self):
                    self.deadline = time.monotonic()
                    self.sequence = 0
                    self.frame = np.random.default_rng(17).integers(
                        0, 256, (args.height, args.width, 3), dtype=np.uint8)
                def read(self):
                    self.deadline = max(self.deadline+1/args.fps, time.monotonic())
                    stop.wait(max(0, self.deadline-time.monotonic()))
                    self.sequence += 1
                    frame = self.frame.copy()
                    frame[0, 0] = (self.sequence % 256, self.sequence//256 % 256, 0)
                    return True, frame
                def getBackendName(self): return 'SYNTHETIC_NO_SENSOR'
                def get(self, prop):
                    return {cv2.CAP_PROP_FRAME_WIDTH: args.width,
                            cv2.CAP_PROP_FRAME_HEIGHT: args.height,
                            cv2.CAP_PROP_FPS: args.fps}.get(prop, 0)
                def release(self): pass
            def synthetic_open(self, index, backend):
                backend = SyntheticBackend()
                self._remember(backend.read()[1])
                return backend
            stack.enter_context(patch.object(CameraCapture, '_open_index_backend', synthetic_open))
        source = dict(id='usb_0', type='usb', index=0, label='MacBook Camera')
        if args.source == 'front-gate':
            from api.camera_store import get_saved_ip_cameras
            saved = next((c for c in get_saved_ip_cameras()
                          if c.get('label', '').casefold() == 'front gate'), None)
            if saved is None:
                args.output.write_text(json.dumps({'error': 'front_gate_not_configured'}))
                return 2
            source = dict(id='ip_'+str(saved['id']), type='rj45', label='Front Gate')
        original_remember = CameraCapture._remember
        def remember(self, *a, **kw):
            original_remember(self, *a, **kw)
            if self._frame_time >= origin[0]:
                events['capture'].append(self._frame_time)
        stack.enter_context(patch.object(CameraCapture, '_remember', remember))
        original_open = CameraCapture.open
        def opened(self):
            ok = original_open(self)
            if ok:
                settings.clear()
                settings.update(self.get_settings())
                opened_sources.append({'kind': 'network' if self._source_url else args.source,
                                       'settings': dict(settings)})
            else:
                errors.append('camera_open_failed')
            return ok
        stack.enter_context(patch.object(CameraCapture, 'open', opened))
        timed(CameraCapture, 'get_latest_sample', 'sample_copy_ms', stack)
        timed(CameraFeed, 'render', 'render_ms', stack)
        observe = PERF.observe
        def observed(name, seconds):
            observe(name, seconds)
            if time.monotonic() >= origin[0] and name in NAMES:
                costs[name+'_ms'].append(seconds*1000)
        stack.enter_context(patch.object(PERF, 'observe', observed))
        app = cap = reader = worker = models = None
        stopped = {}
        sync_counts = {'accepted': 0, 'batches': 0}
        if args.mode == 'full':
            from database.db_manager import CBVMSDatabase
            from core.recognizer import FaceRecognizer
            from core.person_detector import PersonDetector
            from core.monitor_models import MonitorModels
            from core.live_pipeline import LiveProcessor
            from core.attendance_writer import AttendanceWriter
            from ui.dashboard import CBVMSDashboard
            if args.render_ref:
                import ast
                import ui.dashboard as dashboard_module
                old = subprocess.check_output(['git', 'show', args.render_ref+':ui/dashboard.py'], text=True)
                tree = ast.parse(old)
                cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CBVMSDashboard')
                method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_update_feed')
                namespace = vars(dashboard_module).copy()
                exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                             'dashboard_feed_baseline', 'exec'), namespace)
                stack.enter_context(patch.object(CBVMSDashboard, '_update_feed', namespace['_update_feed']))
            db = CBVMSDatabase(Path(folder)/'isolated.db')
            db.initialize(process_deadlines=False)
            db.get_user_role = lambda _: 'admin'
            stack.enter_context(patch('api.camera_store.save_camera_preference', lambda _: None))
            stack.enter_context(patch.object(CBVMSDashboard, '_load_camera_preference', lambda self: None))
            stack.enter_context(patch.object(LiveProcessor, 'persist', lambda *a: None))
            stack.enter_context(patch.object(LiveProcessor, 'submit_unknowns', lambda *a: None))
            stack.enter_context(patch.object(AttendanceWriter, 'offer', lambda *a: False))
            timed(CBVMSDashboard, '_update_feed', 'feed_callback_ms', stack)
            timed(CBVMSDashboard, '_monitor_rows', 'projection_ms', stack)
            recognizer, detector = FaceRecognizer(db), PersonDetector()
            models = MonitorModels(recognizer, detector)
            progress.write_text(json.dumps({'phase': 'model_warmup'}))
            models.start(); models.wait(60)
            app = CBVMSDashboard(username='benchmark', database=db, recognizer=recognizer,
                                 person_detector=detector, monitor_models=models)
            def callback_error(*_):
                errors.append('tk_callback_error')
                app.quit()
            app.report_callback_exception = callback_error
            import builtins
            original_print = builtins.print
            def diagnostic_print(*values, **kwargs):
                if values and isinstance(values[0], str) and values[0].startswith('[CBVMS] feed error:'):
                    errors.append('feed_callback_error')
                original_print(*values, **kwargs)
            stack.enter_context(patch.object(builtins, 'print', diagnostic_print))
            app._camera_requested_source = source
            app._camera_resolution_setting = (args.width, args.height)
            app._fps_cap_setting = args.fps
            app._feed_interval_ms = max(16, 1000//args.fps)
            app.title('CBVMS isolated performance test — no business writes')
            original_render = app.camera_feed.render
            def render(frame):
                ok = original_render(frame)
                now = time.monotonic()
                if ok and now >= origin[0]:
                    # This invocation is counted below from the dashboard's
                    # unique-frame deque, not from arbitrary redraw calls.
                    events['redraw'].append(now)
                return ok
            app.camera_feed.render = render
            if args.busy:
                from core.incremental_sync import SyncWorker, enqueue
                from database.incremental_sync import migrate
                from scripts.benchmark_incremental_sync import synthetic_receipt
                with db.connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    migrate(conn)
                def transport(batch):
                    stop.wait(.05)  # bounded simulated network delay; no network
                    sync_counts['batches'] += 1
                    return [synthetic_receipt(op) for op in batch]
                sync = SyncWorker(db.db_path, transport, batch_size=10)
                def contention():
                    sequence = 0
                    while not stop.wait(.5):
                        start = time.monotonic()
                        try:
                            with db.connect() as conn:
                                conn.execute('BEGIN IMMEDIATE')
                                if start >= origin[0]:
                                    costs['isolated_writer_wait_ms'].append((time.monotonic()-start)*1000)
                                for _ in range(5):
                                    sequence += 1
                                    enqueue(conn, device_id='benchmark', entity_type='student',
                                            entity_id=f'synthetic-{sequence}', expected_version=0,
                                            operation_type='student.profile.patch',
                                            payload={'name': 'Synthetic benchmark'})
                            sync_counts['accepted'] += sync.run_once()
                        except Exception:
                            errors.append('isolated_writer_failed')
                worker = threading.Thread(target=contention, daemon=True)
                worker.start()
        else:
            cap = CameraCapture(camera_index=0, width=args.width, height=args.height,
                                fps_cap=args.fps, cancel_event=stop)
            def capture():
                try:
                    if args.source == 'front-gate':
                        from api.camera_store import resolve_camera_source
                        resolved = resolve_camera_source(source, cancel_event=stop)
                        cap._source_url = cap.source_url = resolved['url']
                        cap.transport = resolved.get('transport', 'tcp')
                    if cap.open():
                        while not stop.is_set():
                            if cap.read() is None:
                                stop.wait(.005)
                except Exception:
                    errors.append('capture_failed')
                finally:
                    cap.release()
            reader = threading.Thread(target=capture, daemon=True)
            reader.start()
            if args.mode == 'preview':
                import tkinter as tk
                app = tk.Tk(); app.geometry('960x600')
                feed = CameraFeed(app); feed.pack(fill='both', expand=True)
            if args.mode == 'yolo':
                from core.person_detector import PersonDetector
                detector = PersonDetector()
                import numpy as np
                detector.detect_persons(np.zeros((args.height, args.width, 3), np.uint8))
        start = time.monotonic()
        progress.write_text(json.dumps({'phase': 'measurement'}))
        origin[0] = start + args.warmup
        deadline = origin[0]+args.seconds
        last = None
        last_times = dict(preview=0., tracking=0., assessment=0.)
        next_render, next_stats = start, origin[0]
        process.cpu_percent()
        def tick():
            nonlocal last, next_render, next_stats
            now = time.monotonic()
            if args.mode == 'full':
                active = app._camera_active_source
                if active is not None and active.get('id') != source['id']:
                    errors.append('source_changed_during_benchmark')
                    app.quit()
                    return
                for name, values in (('preview', app._preview_times), ('tracking', app._tracking_times),
                                     ('assessment', app._analysis_times)):
                    fresh = [t for t in values if t > last_times[name] and t >= origin[0]]
                    events[name].extend(fresh)
                    if fresh: last_times[name] = fresh[-1]
            elif args.mode != 'capture' and now >= next_render:
                sample = cap.get_latest_sample()
                if sample is not None and sample.frame_id != last:
                    if args.mode == 'preview':
                        ok = feed.render(sample.frame)
                    else:
                        detector.detect_persons(sample.frame)
                        ok = detector.last_error is None
                        if not ok and 'yolo_inference_failed' not in errors:
                            errors.append('yolo_inference_failed')
                    finished = time.monotonic()
                    if ok and now >= origin[0]:
                        events[args.mode].append(finished)
                        costs['capture_to_output_ms'].append((finished-sample.captured_at)*1000)
                    last = sample.frame_id
                if args.mode == 'preview':
                    next_render, _ = next_preview_tick(next_render, now, time.monotonic(), max(16, 1000//args.fps))
                else:
                    next_render = now
            if now >= next_stats:
                row = dict(t=round(now-origin[0], 3), rss_mb=round(process.memory_info().rss/2**20, 2),
                           cpu_percent=process.cpu_percent())
                if args.mode == 'full':
                    row['queued'] = [app._tracking_worker.requests.qsize(), app._live_worker.requests.qsize()]
                    if app._candidate_router is not None:
                        row['candidates'] = app._candidate_router.stats()
                    row['canvas_size'] = [app.camera_feed.winfo_width(), app.camera_feed.winfo_height()]
                rows.append(row); next_stats = now+1
                if len(rows) % 10 == 0:
                    progress.write_text(json.dumps({'phase': 'measurement', 'last': row,
                        'counts': {k: len(v) for k,v in events.items()}}))
            if app:
                if now >= deadline:
                    app.quit()
                else:
                    app.after(5, tick)
        try:
            if app:
                app.after(0, tick)
                app.mainloop()
            else:
                while time.monotonic() < deadline:
                    tick()
                    time.sleep(.001)
        finally:
            stop.set()
            if app:
                if args.mode == 'full':
                    app._on_close()
                    for name, event in (('camera', app._camera_worker_done),
                                        ('tracking', app._tracking_worker.done),
                                        ('assessment', app._live_worker.done)):
                        stopped[name] = event.wait(10)
                else: feed.cleanup(); app.destroy()
            for thread in (reader, worker):
                if thread: thread.join(10)
            if models: models.cancel_pending(); models.wait(10)
        report = dict(configuration=vars(args) | {'output': str(args.output)}, settings=settings,
                      measured_seconds=args.seconds, accuracy_evaluated=False, empty_gallery=True,
                      sensor_latency_measured=False, business_writes=0, frames_saved=0,
                      contention='real incremental SyncWorker, disposable SQLite, simulated transport' if args.busy else 'idle',
                      sync_counts=sync_counts, errors=errors,
                      rates={}, costs_ms={k: distribution(list(v)) for k,v in costs.items()},
                      resources=rows, recent_timings=PERF.snapshot(),
                      observation_traces=TRACES.snapshot(),
                      measurement_start_monotonic=origin[0],
                      power_before=power_before, power_after=power_state(),
                      workers_stopped=stopped,
                      opened_sources=opened_sources,
                      reader_stopped=reader is None or not reader.is_alive())
        report['models'] = ({name: state.state for name, state in models.readiness.snapshot().items()}
                            if models else {})
        if args.mode == 'full':
            report['active_model_backends'] = {
                'face_detection': (recognizer._detector.session.get_providers()
                                   if recognizer._detector is not None else []),
                'face_embedding': (recognizer._recognition.session.get_providers()
                                   if recognizer._recognition is not None else []),
                'uniform': (str(next(models.trainer._models['uniform'].model.parameters()).device)
                            if 'uniform' in models.trainer._models else None),
            }
            report['assessment_configuration'] = {
                'observation_only': app._observation_only,
                'clear_frame_selection': os.environ.get('CBVMS_CLEAR_FRAME_SELECTION', '0'),
                'prioritized_assessment': os.environ.get('CBVMS_PRIORITIZED_ASSESSMENT', '0'),
                'fresh_selection': os.environ.get('CBVMS_ASSESSMENT_FRESH_SELECTION', '0'),
                'body_yolo': app._person_detector.backend_status(),
                'candidate_limits': app._candidate_router.stats() if app._candidate_router else None,
            }
        if models and any(report['models'].get(name) != 'ready'
                          for name in ('face', 'recognition', 'body', 'uniform')):
            errors.append('required_model_unavailable')
        if not report['reader_stopped'] or not all(stopped.values()):
            errors.append('worker_shutdown_timeout')
        report['cost_sample_counts'] = {name: len(values) for name, values in costs.items()}
        report['source_sha256_at_start'] = source_hashes
        report['timestamp_semantics'] = ('Capture timestamps follow backend read/decode. Preview measures '
            'unique Tk image submissions, not compositor presentation or sensor-to-screen latency. '
            'OpenCV exposes no sensor sequence/PTS here; no hashes are used to discard static scenes.')
        for name, values in events.items():
            values = [t for t in values if origin[0] <= t < deadline]
            bins = [sum(origin[0]+i <= t < origin[0]+i+10 for t in values)/10
                    for i in range(0, int(args.seconds)-9, 10)]
            report['rates'][name] = dict(count=len(values), hz=round(len(values)/args.seconds, 3),
                ten_second_hz=distribution(bins), first_30s_hz=sum(t<origin[0]+30 for t in values)/min(30,args.seconds),
                last_30s_hz=sum(t>=deadline-30 for t in values)/min(30,args.seconds),
                gap_ms=distribution([(b-a)*1000 for a,b in zip(values, values[1:])]))
        if 'capture' in report['rates'] and 'preview' in report['rates']:
            report['acquired_not_presented'] = max(0, report['rates']['capture']['count'] - report['rates']['preview']['count'])
        report['source_dropped_before_decode'] = None
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+'\n')
        return 0 if not errors else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        import traceback
        failure = {'error_kind': type(exc).__name__,
                   'frames': [{'file': Path(f.filename).name, 'line': f.lineno,
                               'function': f.name} for f in traceback.extract_tb(exc.__traceback__)]}
        if '--output' in sys.argv:
            Path(sys.argv[sys.argv.index('--output')+1]).write_text(json.dumps(failure, indent=2))
        raise SystemExit(2)
