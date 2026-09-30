"""Real local-model smoke probe; never persists a detection to the live DB.

Default: probe student 2023-00883's stored photo (NOT live-camera evidence).
--camera: use actual fresh camera samples for up to --seconds after model load.
The parent bounds the entire probe with a subprocess timeout, so a wedged native
call is terminated with its owning process, never by releasing an active lock.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def probe(args):
    import json
    import logging
    import sqlite3
    import tempfile
    import threading
    import time
    import cv2
    import numpy as np
    from core.camera import CameraCapture
    from core.live_pipeline import LiveProcessor, MonitorTask, FaceTrackingProcessor
    from core.live_state import FrameContext
    from core.model_readiness import face_readiness
    from core.person_detector import PersonDetector
    from core.recognizer import FaceRecognizer
    from core.trainer import ViolationTrainer
    from core.uniform_matcher import UniformColorMatcher
    from database.db_manager import CBVMSDatabase

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    source = sqlite3.connect(f'{Path(args.db).resolve().as_uri()}?mode=ro', uri=True)
    with tempfile.TemporaryDirectory(prefix='cbvms_smoke_') as folder:
        target = Path(folder)/'probe.db'
        copy = sqlite3.connect(target)
        source.backup(copy)
        copy.close(); source.close()
        db = CBVMSDatabase(target)
        db.initialize(process_deadlines=False)
        recognizer, detector, trainer = FaceRecognizer(db), PersonDetector(), ViolationTrainer()
        matcher = UniformColorMatcher()
        states = face_readiness(recognizer)
        states.add('body', detector._ensure_model)
        if trainer.is_trained('uniform'):
            states.add('uniform', lambda: trainer._get_model('uniform'))
        started = time.monotonic()
        states.start()
        while any(s.state in ('waiting', 'loading') for s in states.snapshot().values()):
            time.sleep(.05)
        print(json.dumps({'source': 'camera' if args.camera else 'stored_photo',
                          'components': {k: {'state': s.state, 'seconds': round(s.elapsed, 3),
                                              'error': s.error} for k, s in states.snapshot().items()}}), flush=True)
        if not states.ready('face') or not states.ready('recognition'):
            return 2
        processor = LiveProcessor(db, recognizer, detector, trainer, matcher, None)
        tracker = FaceTrackingProcessor(recognizer)
        camera = CameraCapture(camera_index=0) if args.camera else None
        if camera is not None and not camera.open():
            print(json.dumps({'camera_open': False, 'error': camera.last_error}), flush=True)
            return 2
        if camera is None:
            student = db.get_student_by_student_id(args.student_id) or {}
            blob = student.get('photo')
            frame = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR) if blob else None
            if frame is None:
                print('No decodable stored enrollment photo', flush=True)
                return 2
        first = {'track': None, 'reliable_identity': None, 'uniform_assessment': None}
        sampling_started = time.monotonic()
        sequence, analyzed, rejected = 0, 0, 0
        recognized = set()
        try:
            while time.monotonic()-sampling_started < args.seconds:
                if camera is not None:
                    if camera.read() is None:
                        continue
                    sample = camera.get_latest_sample()
                    frame, captured, frame_id = sample.frame, sample.captured_at, sample.frame_id
                else:
                    captured, frame_id = time.monotonic(), ('stored_photo', sequence)
                sequence += 1
                task = MonitorTask(FrameContext(1, frame_id, captured), frame, time.time(), threading.Event(),
                                   uniform_enabled=states.ready('body'), earring_enabled=False)
                track = tracker.analyze(task)
                if track and track.assessments and first['track'] is None:
                    first['track'] = round(time.monotonic()-started, 3)
                result = processor.analyze(task)
                if result is None:
                    rejected += 1
                    continue
                analyzed += 1
                for assessment in result.assessments:
                    if assessment.reliable_identity:
                        recognized.add(assessment.student_id)
                    if assessment.reliable_identity and first['reliable_identity'] is None:
                        first['reliable_identity'] = round(time.monotonic()-started, 3)
                    if assessment.uniform_label and first['uniform_assessment'] is None:
                        first['uniform_assessment'] = round(time.monotonic()-started, 3)
                if camera is None and sequence >= 4:
                    break
        finally:
            if camera is not None:
                camera.release()
        print(json.dumps({'source': 'camera' if args.camera else 'repeated_stored_photo_not_live_evidence',
                          'first_seconds_including_initialization': first, 'analyzed': analyzed,
                          'recognized_student_ids': sorted(recognized),
                          'rejected': rejected, 'live_database_writes': 0}), flush=True)
        if camera is None and states.ready('uniform'):
            path = next(iter(sorted(trainer._source_label_dir('uniform', 'correct_uniform').glob('*.jpg'))), None)
            crop = cv2.imread(str(path)) if path else None
            if crop is not None:
                start = time.monotonic()
                probabilities = trainer.predict_proba('uniform', crop)
                print(json.dumps({'source': 'stored_training_crop_not_accuracy_test',
                                  'classifier_seconds': round(time.monotonic()-start, 3),
                                  'classifier_returned_probabilities': probabilities is not None,
                                  'colour_reference_available': matcher.is_loaded()}), flush=True)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(ROOT/'data/cbvms.db'))
    parser.add_argument('--student-id', default='2023-00883')
    parser.add_argument('--camera', action='store_true')
    parser.add_argument('--seconds', type=float, default=15)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return probe(args)
    try:
        return subprocess.run([sys.executable, __file__, *sys.argv[1:], '--child'],
                              timeout=65+args.seconds).returncode
    except subprocess.TimeoutExpired:
        print('Probe process terminated at its deadline; native inference did not finish.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
