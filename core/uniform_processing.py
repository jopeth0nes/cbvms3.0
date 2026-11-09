"""Resumable garment processing; originals and historical reviews are immutable inputs.

One CPU detector is reused by a serialized background worker. No camera or live
classifier is acquired. Quality thresholds and measured values are persisted so
quarantine decisions can be audited; these are conservative floors, not accuracy
claims or a substitute for independent model evaluation.
"""
from __future__ import annotations

import copy
import hashlib
import io
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

PIPELINE_VERSION = 'uniform-auto-v1'
STATES = ('queued', 'processing', 'training_ready', 'quarantined', 'failed')
HARD_FLAGS = {'obstruction', 'obstructed', 'incorrect_association', 'unable_to_assess',
              'label_conflict', 'category_conflict', 'crop_unreliable', 'invalid_image'}
ADVISORY_FLAGS = {'provenance_association_uncertain', 'near_duplicate_review'}
QUALITY_THRESHOLDS = {'min_crop_side': 64, 'laplacian_variance_min': 8.0,
                      'gray_std_min': 2.0, 'clipped_fraction_max': 0.98}
_WORKERS = {}
_GUARD = threading.Lock()


def valid_crop(box, width, height):
    return (isinstance(box, (tuple, list)) and len(box) == 4
            and all(type(v) is int for v in box)
            and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height
            and min(box[2] - box[0], box[3] - box[1]) >= 16)


def measure_quality(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Measure at native resolution, capped to a fixed 512-pixel long edge.
    scale = min(1., 512 / max(gray.shape))
    measured = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    values = {'width': rgb.shape[1], 'height': rgb.shape[0],
              'laplacian_variance': float(cv2.Laplacian(measured, cv2.CV_32F).var()),
              'gray_std': float(measured.std()),
              'clipped_fraction': float(np.mean((measured <= 2) | (measured >= 253))),
              'thresholds': dict(QUALITY_THRESHOLDS),
              'obstruction_assessment': 'pose reliability and supplied obstruction flags; no semantic occlusion classifier'}
    reasons = []
    if min(rgb.shape[:2]) < QUALITY_THRESHOLDS['min_crop_side']:
        reasons.append('crop_low_resolution')
    if values['gray_std'] < QUALITY_THRESHOLDS['gray_std_min'] or values['clipped_fraction'] > QUALITY_THRESHOLDS['clipped_fraction_max']:
        reasons.append('empty_or_unusable_crop')
    if values['laplacian_variance'] < QUALITY_THRESHOLDS['laplacian_variance_min']:
        reasons.append('excessive_crop_blur')
    return values, reasons


def duplicate_groups(records):
    """Connected near-duplicate components; retain all useful views with weights."""
    parent = {r['image_id']: r['image_id'] for r in records}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for r in records:
        for other in r.get('duplicate_of', []):
            if other in parent:
                a, b = find(r['image_id']), find(other)
                parent[max(a, b)] = min(a, b)
    groups = {}
    for r in records:
        groups.setdefault(find(r['image_id']), []).append(r)
    for gid, rows in groups.items():
        # dHash similarity is advisory: different garments can share shape. Exact
        # duplicate label conflicts are resolved separately from this grouping.
        for row in rows:
            row['duplicate_group'] = gid
            row['duplicate_group_size'] = len(rows)
            row['sample_weight'] = 1. / len(rows)
    return groups


def process_record(dataset, record, detector):
    from core.uniform_dataset import GARMENT_CATEGORIES, MAX_FILE_BYTES, _image_info
    result = {'processing_state': 'quarantined', 'training_eligible': False,
              'eligibility_reasons': [], 'quality_checks': {}, 'pipeline_version': PIPELINE_VERSION,
              'crop_confidence': record.get('crop_confidence')}
    reasons = result['eligibility_reasons']
    if record.get('status') == 'excluded':
        reasons.append('intentionally_excluded')
        return result
    if record.get('category') not in GARMENT_CATEGORIES:
        reasons.append('unassigned_category')
        return result
    if (record.get('logo_annotation') or {}).get('source_kind') == 'reference_artwork' or record.get('source_kind') == 'reference_artwork':
        reasons.append('reference_artwork')
        return result
    flags = set(record.get('flags', []))
    reasons.extend(sorted(flags & HARD_FLAGS))
    result['advisory_warnings'] = sorted(flags - HARD_FLAGS)
    result['flag_assessment'] = {'hard_blocking': sorted(flags & HARD_FLAGS),
        'advisory': sorted(flags & ADVISORY_FLAGS),
        'automatically_reassessed': sorted(flags & {'possible_blur', 'low_resolution', 'image_quality_check_failed',
                                                   'multiple_people', 'person_association_uncertain'})}
    if reasons:
        return result
    try:
        path = dataset.root / record['original_path']
        if path.is_symlink() or dataset.root.resolve() not in path.resolve().parents:
            raise ValueError('unsafe_original_path')
        cursor = dataset.root
        for component in Path(record['original_path']).parts:
            cursor = cursor / component
            if cursor.is_symlink(): raise ValueError('unsafe_original_path')
        with path.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('original_checksum_mismatch')
        _, width, height, _, _ = _image_info(data)
        if (width, height) != (record['width'], record['height']):
            raise ValueError('original_dimensions_changed')
        with Image.open(io.BytesIO(data)) as im:
            rgb = np.asarray(im.convert('RGB'))
        box = record.get('crop')
        manual = record.get('crop_source') == 'manual' and valid_crop(box, width, height)
        if not manual:
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            persons = detector.detect_persons(bgr)
            if len(persons) != 1:
                reasons.append('multiple_people_association_uncertain' if len(persons) > 1 else 'no_reliable_person')
                result['quality_checks']['person_count'] = len(persons)
                return result
            # Require a pose-anchored torso for automatic inclusion. Geometric
            # person-box fractions remain a manual inspection aid only.
            box = detector.pose_torso_box(bgr, persons[0], require_full_torso=True) if detector.use_pose else None
            if not valid_crop(box, width, height):
                reasons.append('no_reliable_torso')
                return result
            result.update(crop=list(box), crop_source='detector',
                          crop_method='single_person_pose', crop_confidence=None)
        result['quality_checks'], quality_reasons = measure_quality(rgb[box[1]:box[3], box[0]:box[2]])
        reasons.extend(quality_reasons)
        if not reasons:
            # A saved correction establishes subject association; retain the old
            # detector warnings in history/flags but mark their resolution.
            result['resolved_warnings'] = sorted(flags & {'multiple_people', 'person_association_uncertain', 'low_resolution', 'possible_blur', 'image_quality_check_failed'})
        if not reasons:
            crop_dir = dataset.root / 'crops'
            if crop_dir.is_symlink(): raise ValueError('unsafe_crop_directory')
            crop_dir.mkdir(exist_ok=True)
            crop_key = hashlib.sha256((record['sha256'] + repr(list(box)) + PIPELINE_VERSION).encode()).hexdigest()
            rel = f'crops/{crop_key}.png'
            target = dataset.root / rel
            if target.is_symlink():
                raise ValueError('unsafe_crop_path')
            if not target.exists():
                import tempfile
                fd, name = tempfile.mkstemp(prefix='.crop-', dir=crop_dir)
                try:
                    with os.fdopen(fd, 'wb') as out:
                        Image.fromarray(rgb[box[1]:box[3], box[0]:box[2]]).save(out, format='PNG')
                        out.flush(); os.fsync(out.fileno())
                    os.replace(name, target)
                finally:
                    if os.path.exists(name): os.unlink(name)
            result.update(processing_state='training_ready', training_eligible=True, crop_path=rel,
                          crop_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    except Exception as exc:
        result.update(processing_state='failed', training_eligible=False)
        reasons.append(f'{type(exc).__name__}: {exc}')
    return result


def migrate(dataset, *, detector=None, retry=False, stop=None, limit=None):
    """Atomic per-record progress. Kernel lock makes interrupted processing resumable.

    No catalog lock is held during inference. Revision comparison discards stale
    results after concurrent crop/label edits. Terminal rows of this version are
    not repeated unless retry=True. Excluded rows never become eligible.
    """
    from core.uniform_dataset import _now
    import fcntl
    report = {'pipeline_version': PIPELINE_VERSION, 'processed': 0, 'training_ready': 0,
              'quarantined': 0, 'failed': 0, 'skipped': 0, 'interrupted': False}
    with (dataset.root / '.processing.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with dataset._locked():
            catalog = dataset._load()
            backup = dataset.root / f'catalog-before-{PIPELINE_VERSION}.json'
            if not backup.exists():
                from core.uniform_candidate import _atomic_json
                _atomic_json(backup, catalog)
            duplicate_groups(catalog['records'])
            ids = [r['image_id'] for r in catalog['records']]
            dataset._save(catalog)
        for iid in ids:
            if (stop and stop.is_set()) or (limit is not None and report['processed'] >= limit):
                report['interrupted'] = True
                break
            with dataset._locked():
                catalog = dataset._load()
                row = next(r for r in catalog['records'] if r['image_id'] == iid)
                if row.get('category') not in {'campus_uniform', 'organizational_shirt', 'other_clothing'}:
                    report['skipped'] += 1
                    continue
                if not retry and row.get('pipeline_version') == PIPELINE_VERSION and row.get('processing_state') in {'training_ready', 'quarantined', 'failed'}:
                    report['skipped'] += 1
                    continue
                row.update(processing_state='processing', training_eligible=False)
                original = copy.deepcopy(row)
                dataset._save(catalog)
            if detector is None and original.get('category') in {'campus_uniform','organizational_shirt','other_clothing'} and original.get('status') != 'excluded':
                from core.person_detector import PersonDetector
                detector = PersonDetector(backend='cpu')
                detector.use_pose = True
            result = process_record(dataset, original, detector)
            with dataset._locked():
                catalog = dataset._load()
                row = next(r for r in catalog['records'] if r['image_id'] == iid)
                if row != original:
                    # Imports/reviews invalidate readiness themselves. Never apply
                    # an inference result computed from stale metadata.
                    if row.get('processing_state') == 'processing': row['processing_state'] = 'queued'
                    dataset._save(catalog)
                    continue
                row.update(result, processed_at=_now())
                row.setdefault('processing_history', []).append({'at': row['processed_at'],
                    'pipeline_version': PIPELINE_VERSION, 'state': result['processing_state'],
                    'reasons': list(result['eligibility_reasons'])})
                dataset._save(catalog)
            report['processed'] += 1
            report[result['processing_state']] += 1
            # Yield between images, preserving the live monitor's opportunity to run.
            time.sleep(.01)
        with dataset._locked():
            records = dataset._load()['records']
        report['totals'] = {s: sum(r.get('processing_state') == s for r in records) for s in STATES}
        from collections import Counter
        report['reason_counts'] = dict(Counter(reason for r in records for reason in r.get('eligibility_reasons', [])
                                              if r.get('processing_state') in {'quarantined','failed'}))
        report['unassigned'] = sum(r.get('category') == 'unassigned' for r in records)
        report['ready_by_category'] = {c: sum(r.get('category') == c and r.get('training_eligible') is True for r in records)
                                       for c in ('campus_uniform','organizational_shirt','other_clothing')}
        from core.uniform_candidate import _atomic_json
        _atomic_json(dataset.root / 'processing_report.json', report)
    return report


def schedule(dataset):
    """Coalesce uploads in one worker per root; safe across multiple UI instances."""
    key = str(dataset.root.resolve())
    with _GUARD:
        current = _WORKERS.get(key)
        if current and current[0].is_alive():
            current[1].set()
            return current[0]
        wake = threading.Event()
        def work():
            try:
                while True:
                    wake.clear()
                    import subprocess, sys
                    env = {**os.environ, 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2',
                           'OPENBLAS_NUM_THREADS': '2', 'VECLIB_MAXIMUM_THREADS': '2',
                           'MPLCONFIGDIR': str(dataset.root.resolve() / '.mplconfig'),
                           'YOLO_CONFIG_DIR': str(dataset.root.resolve() / '.ultralytics')}
                    with (dataset.root / 'processing.log').open('w') as log:
                        subprocess.run([sys.executable, '-m', 'core.uniform_processing', str(dataset.root.resolve())],
                                       cwd=Path(__file__).resolve().parents[1], env=env,
                                       stdout=log, stderr=subprocess.STDOUT, check=True)
                    with _GUARD:
                        if not wake.is_set():
                            _WORKERS.pop(key, None)
                            return
            except Exception:
                # Leave queued/processing rows retryable, never claim success.
                import logging
                logging.getLogger(__name__).exception('Uniform dataset processing interrupted')
                with _GUARD: _WORKERS.pop(key, None)
        thread = threading.Thread(target=work, daemon=True, name='uniform-dataset-processing')
        _WORKERS[key] = (thread, wake)
        thread.start()
        return thread


def main(argv=None):
    import sys
    import torch
    from core.uniform_dataset import UniformDataset
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit('usage: python -m core.uniform_processing DATASET_ROOT')
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    cv2.setNumThreads(1)
    migrate(UniformDataset(args[0], auto_process=False))


if __name__ == '__main__':
    main()
