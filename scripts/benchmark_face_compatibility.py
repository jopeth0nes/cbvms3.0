"""Local public-image ArcFace backend probe. Never opens a student database.

Existing embeddings/model unchanged. Pairwise agreement on public fixtures is
NOT gallery compatibility or student recognition accuracy. CoreML provider events
do not distinguish CPU/GPU/ANE hardware inside Apple's runtime.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error('repeats must be 1..100')
    import cv2
    import numpy as np
    import onnxruntime as ort
    import ultralytics
    from insightface.model_zoo.model_zoo import ModelRouter
    from insightface.app.common import Face
    from insightface.utils import face_align
    from core.recognizer import FaceRecognizer
    recognizer = FaceRecognizer(SimpleNamespace(get_all_students=lambda: [],
        recognition_capture_encodings=lambda: {}))
    assets = Path(ultralytics.__file__).resolve().parent / 'assets'
    images = [cv2.imread(str(assets / name)) for name in ('bus.jpg', 'zidane.jpg')]
    crops, sources = [], []
    for image in images:
        if image is None:
            raise RuntimeError('Bundled public fixture missing')
        for row in recognizer.detect_faces(image):
            points = np.asarray(row['keypoints'], dtype=np.float32)
            crops.append(face_align.norm_crop(image, landmark=points, image_size=112))
            sources.append((image, row))
    if not crops:
        raise RuntimeError('No detected public faces; cannot evaluate compatibility')
    weights = recognizer.model_dir / 'w600k_r50.onnx'
    result = dict(public_faces=len(crops), model_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
        labeled_student_accuracy=None, gallery_compatibility_verified=False,
        detector='CPUExecutionProvider; CoreML detector not retried: prior static-output rank mismatch',
        hardware_note='CoreML kernel execution is not proof of GPU or ANE placement', trials=[])
    reference = None
    with tempfile.TemporaryDirectory(prefix='cbvms-face-probe-') as folder:
        for provider in ('CPUExecutionProvider', 'CoreMLExecutionProvider'):
            if provider not in ort.get_available_providers():
                result['trials'].append(dict(provider=provider, error='provider unavailable'))
                continue
            try:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                options.add_session_config_entry('session.intra_op.allow_spinning', '0')
                options.add_session_config_entry('session.inter_op.allow_spinning', '0')
                options.enable_profiling = True
                options.profile_file_prefix = str(Path(folder)/provider)
                start = time.monotonic()
                model = ModelRouter(str(weights)).get_model(
                    providers=[provider] if provider == 'CPUExecutionProvider' else
                    [provider, 'CPUExecutionProvider'], sess_options=options)
                model.prepare(ctx_id=0)  # negative ctx silently resets the session to CPU
                load_ms = (time.monotonic()-start)*1000
                start = time.monotonic()
                outputs = [model.get_feat(crop).flatten() for crop in crops]
                warm_ms = (time.monotonic()-start)*1000
                values = np.asarray(outputs)
                values /= np.linalg.norm(values, axis=1, keepdims=True)
                timings = []
                for _ in range(args.repeats):
                    for crop in crops:
                        start = time.monotonic()
                        model.get_feat(crop)  # synchronous ORT call, includes transfers
                        timings.append((time.monotonic()-start)*1000)
                split_delta = None
                if reference is None:
                    reference = values
                    original = []
                    for image, row in sources:
                        face = Face(bbox=np.asarray(row['box']),
                            kps=np.asarray(row['keypoints'], np.float32), det_score=row['score'])
                        model.get(image, face)
                        original.append(face.normed_embedding)
                    split_delta = float(np.max(np.abs(np.asarray(original)-values)))
                profile = json.loads(Path(model.session.end_profiling()).read_text())
                counts = Counter(event.get('args', {}).get('provider') for event in profile
                    if event.get('cat') == 'Node' and event.get('args', {}).get('provider'))
                result['trials'].append(dict(provider=provider,
                    actual_providers=model.session.get_providers(), kernel_events=dict(counts),
                    load_ms=load_ms, warmup_ms=warm_ms, samples=len(timings),
                    latency_ms={f'p{p}':float(np.percentile(timings,p)) for p in (50,95,99)},
                    min_same_face_cosine_vs_cpu=float(np.min(np.sum(values*reference,axis=1))),
                    max_pairwise_cosine_delta=float(np.max(np.abs(values@values.T-reference@reference.T))),
                    split_alignment_max_delta=split_delta))
            except Exception as exc:
                result['trials'].append(dict(provider=provider, error=str(exc)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
