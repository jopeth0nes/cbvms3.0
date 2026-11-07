"""Preserve full-resolution DYCI artwork separately from camera training samples."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid

from PIL import Image, ImageOps
from core.appeal_evidence import MAX_EVIDENCE_BYTES, MAX_EVIDENCE_PIXELS, MAX_EVIDENCE_SIDE

REFERENCE_DIR = Path(__file__).resolve().parents[1] / 'data' / 'uniform_references'
KINDS = {'logo': 'DYCI logo', 'shirt': 'Digital shirt'}


def save_reference(source, kind, *, root=None):
    if kind not in KINDS:
        raise ValueError('Choose a DYCI logo or digital shirt reference.')
    path = Path(source)
    with path.open('rb') as stream:
        data = stream.read(MAX_EVIDENCE_BYTES + 1)
    if not data or len(data) > MAX_EVIDENCE_BYTES:
        raise ValueError('Reference images must be non-empty and no larger than 10 MB.')
    with Image.open(io.BytesIO(data)) as picture:
        if picture.format not in {'JPEG', 'PNG', 'BMP', 'WEBP'}:
            raise ValueError('Choose a JPG, PNG, BMP or WebP image.')
        if (max(picture.size) > MAX_EVIDENCE_SIDE or picture.width * picture.height > MAX_EVIDENCE_PIXELS):
            raise ValueError('Reference image dimensions are too large.')
        picture.load()
        picture = ImageOps.exif_transpose(picture).convert('RGBA')
    folder = Path(root) if root is not None else REFERENCE_DIR
    folder = folder / kind
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (uuid.uuid4().hex + '.png')
    picture.save(target, format='PNG')
    metadata = {'kind': kind, 'source_name': path.name, 'width': picture.width, 'height': picture.height,
                'saved_at': datetime.now(timezone.utc).isoformat(),
                'source_sha256': hashlib.sha256(data).hexdigest(),
                'saved_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    try:
        target.with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def list_references(*, root=None):
    folder = Path(root) if root is not None else REFERENCE_DIR
    return [(kind, path) for kind in KINDS for path in sorted((folder / kind).glob('*.png'))]
