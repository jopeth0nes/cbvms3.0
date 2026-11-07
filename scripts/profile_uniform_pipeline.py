"""Isolated still-image timing through real tracking and assessment models.

No camera, authorized identity gallery, passage labels, production writes or
accuracy claims. Images must be explicitly supplied; installed public fixtures
are suitable only for computational profiling, never entrance evaluation.
"""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir())/'cbvms-profile-mpl'))
os.environ.setdefault('YOLO_CONFIG_DIR', str(Path(tempfile.gettempdir())/'cbvms-profile-yolo'))


def assessment_task(session, tracked):
    """Mirror the dashboard/router handoff, retaining same-frame landmarks."""
    return session.observe(tracked.task.with_observations(tracked.observations), tracked.observations)


def profile(candidate, image_paths, *, repeats=20, warmup=4):
    if not 1 <= repeats <= 100 or not 1 <= warmup <= 20:
        raise ValueError('Bound repeats to 1–100 and warmup to 1–20')
    import torch
    import core.live_pipeline as pipeline
    from core.live_state import FrameContext, LiveState
    from core.person_detector import PersonDetector
    from core.recognizer import FaceRecognizer, DET_FAST_SIZE, MATCH_THRESHOLD, IDENTITY_MARGIN
    from core.trainer import ViolationTrainer
    from core.uniform_matcher import UniformColorMatcher
    from core.uniform_observation import ObservationConfig, ObservationUniformSession
    from core.uniform_model_registry import candidate_descriptor, digest
    from core.uniform_latency import distribution
    from core.uniform_logo import LogoRequestGate
    from core.performance import PERF
    from database.db_manager import CBVMSDatabase
    from scripts.benchmark_uniform_backends import _load_input_images, _set_benchmark_threads, _runtime_environment

    images, metadata = _load_input_images([str(p) for p in image_paths], synthetic_fixture=False)
    descriptor = candidate_descriptor(candidate)
    config = ObservationConfig(str(Path(candidate).resolve()), descriptor['id'], .8, backend='cpu',
                               policy=None, logo_verification={'enabled':False})
    PERF.enabled = True
    reports = []
    with tempfile.TemporaryDirectory(prefix='cbvms-uniform-profile-') as tmp:
        database = CBVMSDatabase(Path(tmp)/'evaluation.sqlite')
        database.initialize(process_deadlines=False)
        recognizer = FaceRecognizer(database)
        if not recognizer._ensure_models():
            raise RuntimeError('Installed face models unavailable: '+str(recognizer.last_error))
        session = ObservationUniformSession(config)
        session.prepare()
        providers = {name:getattr(recognizer, attr).session.get_providers()
                     for name,attr in (('detection','_detector'),('recognition','_recognition'))}
        weights = {name:digest(recognizer.model_dir/name) for name in ('det_10g.onnx','w600k_r50.onnx')}
        weights.update(body=digest(PersonDetector._MODEL_PATH), candidate=descriptor['id'])
        for backend in ('cpu','mps'):
            detector = PersonDetector(backend=backend)
            detector._ensure_model()
            if detector.backend_status()['active'] != backend:
                reports.append({'body_requested':backend, 'status':'unavailable',
                                'body_backend':detector.backend_status()})
                continue
            tracker = pipeline.FaceTrackingProcessor(recognizer, detector)
            processor = pipeline.LiveProcessor(database, recognizer, detector, ViolationTrainer(),
                UniformColorMatcher(), SimpleNamespace(), observation_only=True, uniform_observation=session)
            worker = pipeline.LiveWorker(processor, observation_only=True)
            samples = []
            components = defaultdict(list)
            active = False
            logo_calls = 0
            original_association = pipeline.associate_faces_to_bodies
            original_region = detector.chest_region
            original_logo = LogoRequestGate.reserve
            def timed(name, function):
                def call(*args, **kwargs):
                    started = time.monotonic()
                    try:return function(*args, **kwargs)
                    finally:
                        if active:components[name].append((time.monotonic()-started)*1000)
                return call
            def logo(*args, **kwargs):
                nonlocal logo_calls
                logo_calls += 1
                return original_logo(*args, **kwargs)
            worker.start()
            try:
                with patch.object(pipeline, 'associate_faces_to_bodies', timed('association_ms',original_association)), \
                     patch.object(detector, 'chest_region', timed('torso_localization_ms',original_region)), \
                     patch.object(LogoRequestGate, 'reserve', logo):
                    for index in range(warmup+repeats):
                        active = index >= warmup
                        if index == warmup:
                            with PERF._lock:
                                PERF._samples.clear(); PERF._counts.clear()
                        threads = _set_benchmark_threads()
                        # Independent still images are NOT a continuous person track.
                        tracker.state = LiveState(); processor.state = LiveState(); session.invalidate()
                        cancelled = threading.Event()
                        captured = time.monotonic()
                        task = pipeline.MonitorTask(FrameContext(1,('public-fixture',index),captured),
                            images[index % len(images)],time.time(),cancelled,
                            source_id='public-still-profile',observation_only=True,earring_enabled=False)
                        tracking_started=time.monotonic()
                        tracked=tracker.analyze(task)
                        localized=time.monotonic()
                        if tracked is None:
                            raise RuntimeError('Tracking failed or expired; do not hide slow cases')
                        task=assessment_task(session,tracked)
                        gated=time.monotonic()
                        if not worker.offer(task):
                            raise RuntimeError('Unexpected competing assessment')
                        try:result=worker.results.get(timeout=5)
                        except queue.Empty:raise RuntimeError('Assessment timeout; no passage claim') from None
                        received=time.monotonic()
                        if result is None:raise RuntimeError('Assessment returned no result')
                        if tracked.observations and not result.assessments:
                            raise RuntimeError('Missing assessment rows for localized faces; invalid profile handoff')
                        row={'image_index':index % len(images), 'frame_copy_ms':1000*(tracking_started-captured),
                            'tracking_ms':1000*(localized-tracking_started),
                            'usable_evidence_gate_ms':1000*(gated-localized),
                            'publication_to_consumer_ms':1000*(received-result.finished_at),
                            'frame_to_result_ms':1000*(result.finished_at-captured),
                            'frame_to_consumer_ms':1000*(received-captured),
                            'faces':len(tracked.observations),
                            'associated_torsos':sum(o.torso_box is not None for o in tracked.observations),
                            'garment_results':len(result.assessments),
                            'garment_inferences':sum(bool(a.garment and a.garment.timings) for a in result.assessments),
                            'recognized':sum(bool(a.garment and a.garment.label) for a in result.assessments),
                            'unassessed_reasons':[a.garment.reason for a in result.assessments if a.garment and a.garment.label is None]}
                        if active:
                            samples.append(row)
                            for assessment in result.assessments:
                                garment=assessment.garment
                                if garment:
                                    for key,value in garment.timings.items():
                                        if value is not None:components['garment_'+key].append(value)
                        if detector.backend_status()['active'] != backend:
                            raise RuntimeError('Body fell back during measured workload; comparison invalid')
            finally:
                worker.stop()
                if not worker.done.wait(5):raise RuntimeError('Owned profile worker did not stop')
            reports.append({'body_requested':backend,'status':'completed','body_backend':detector.backend_status(),
                'face_providers':providers,'candidate_backend':session.predictor.backend_status(),
                'threads':threads, 'samples':samples, 'logo_schedule_calls':logo_calls,
                'logo_inference_calls':0, 'logo_backend_present':False,
                'stage_metrics':PERF.snapshot(),
                'components':{k:distribution(v) for k,v in components.items()},
                'frame_timings':{k:distribution([r[k] for r in samples]) for k in samples[0] if k.endswith('_ms')},
                'gallery_size':0, 'identity_accuracy':None, 'worker_stopped':worker.done.is_set()})
        return {'kind':'real_models_on_public_stills_not_walkthroughs', 'environment':_runtime_environment(),
            'inputs':metadata,'model_hashes':weights,'candidate_synthetic':descriptor.get('synthetic'),
            'candidate_config':config.__dict__, 'configuration':{'warmup_frames_per_backend':warmup,
                'measured_frames_per_backend':repeats,'face_input':DET_FAST_SIZE,'body_input':'existing predictor default (640)',
                'garment_input':224,'recognition_distance':MATCH_THRESHOLD,'identity_margin':IDENTITY_MARGIN,
                'recognition_confidence':.8,'camera_workload':0,'earrings':False,'pose':False,
                'independent_still_frames':True,'logo_enabled':False,'torch_threads_requested':2},
            'results':reports,'real_labeled_passages':0,'physical_camera_measurement':False,
            'limitations':['No entry/exit, human associations, independent garment/policy labels or authorized identity gallery.',
                'Still-image completion timings are not walking-speed, capture-FPS, rendering or before-exit results.',
                'Serial fresh still frames intentionally do not test evidence reuse, concurrent preview or camera queues.',
                'Recognition uses actual ArcFace with an empty gallery; no identity correctness is measured.',
                'No smaller input, threshold change, model activation or training.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--images',required=True,nargs='+',type=Path)
    parser.add_argument('--repeats',default=20,type=int)
    parser.add_argument('--warmup',default=4,type=int)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error('Output already exists; choose a new report path')
    result=profile(args.candidate,args.images,repeats=args.repeats,warmup=args.warmup)
    with args.output.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print('Isolated real-model still profile completed. Real labeled passages: 0.')


if __name__=='__main__':main()
