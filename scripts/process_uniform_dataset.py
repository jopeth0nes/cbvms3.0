"""Preview or apply resumable automatic processing without changing model assets.

Default: process a disposable byte-for-byte dataset copy. --apply changes only the
specified catalog and derived crops, preserving originals and a versioned backup.
"""
import argparse
import json
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('MKL_NUM_THREADS', '2')
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir())/'cbvms-processing-mpl'))
os.environ.setdefault('YOLO_CONFIG_DIR', str(Path(tempfile.gettempdir())/'cbvms-processing-yolo'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT/'data'/'uniform_stage1')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--retry', action='store_true', help='Reassess terminal failures/quarantines as well')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    import torch
    import cv2
    torch.set_num_threads(2); torch.set_num_interop_threads(1); cv2.setNumThreads(1)
    from core.uniform_dataset import UniformDataset
    def process(root):
        ds = UniformDataset(root, auto_process=False)
        before = ds._load()['records']
        def preserved(row):
            keys = ('image_id', 'sha256', 'original_path', 'category', 'sources', 'status', 'review_history')
            return {**{k: row.get(k) for k in keys},
                    'manual_crop': row.get('crop') if row.get('crop_source') == 'manual' else None}
        prior = {r['image_id']: preserved(r) for r in before}
        def original_hashes(rows):
            result = {}
            for row in rows:
                path = root / row['original_path']
                if path.is_file() and not path.is_symlink() and root.resolve() in path.resolve().parents:
                    result[row['image_id']] = hashlib.sha256(path.read_bytes()).hexdigest()
            return result
        originals = original_hashes(before)
        assets = list((ROOT/'models').glob('*')) + [ROOT/'yolov8n-cls.pt']
        model_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets if p.is_file()}
        report = ds.process_pending(retry=args.retry, limit=args.limit)
        after = ds._load()['records']
        preservation = {'records_labels_history_manual_crops': prior == {r['image_id']: preserved(r) for r in after},
                        'original_bytes': originals == original_hashes(after),
                        'model_assets': all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p,digest in model_hashes.items())}
        report['preservation'] = preservation
        if not all(preservation.values()):
            raise RuntimeError('Migration preservation verification failed: ' + str(preservation))
        return report
    if args.apply:
        report = process(args.dataset)
    else:
        with tempfile.TemporaryDirectory(prefix='uniform-processing-preview-') as folder:
            target = Path(folder)/'dataset'
            shutil.copytree(args.dataset, target, symlinks=True)
            report = process(target)
    report['applied'] = args.apply
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
