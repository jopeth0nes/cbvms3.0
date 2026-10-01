"""Exact-record evidence decoding. A hash checks bytes, not identity or truth."""
import hashlib
import io
import json
from PIL import Image, ImageOps
from core.discipline import display_local_datetime as ts, parse_db_datetime

INTEGRITY_HELP = ('Evidence integrity warning: rejection is blocked. Preserve this record and '
                  'compare the original capture with an authoritative backup. An administrator '
                  'may approve/dismiss with a documented reason; never substitute another photo.')


def digest(blob):
    return hashlib.sha256(blob).hexdigest() if blob else None


def inspect_evidence(blob, expected_hash=None, *, key=(), label='', provenance=None,
                     violation_id=None, student_id=None, detected_at=None, size=(1400, 1000)):
    actual = digest(blob)
    result = dict(key=(*key, actual), sha256=actual, image=None, label=label,
                  warning='', blocked=False)
    if expected_hash and actual != expected_hash:
        result.update(warning=INTEGRITY_HELP, blocked=True)
        return result
    if provenance:
        try:
            meta = json.loads(provenance)
            captured = parse_db_datetime(meta.get('captured_at'))
            detected = parse_db_datetime(detected_at)
            if (meta['version'] != 1 or meta['violation_id'] != violation_id
                    or meta['student_id'] != student_id or meta['detected_at'] != detected_at
                    or meta['sha256'] != actual or not expected_hash
                    or not meta['camera_session'] or not meta['frame_id']
                    or not captured or not detected
                    or captured.replace(microsecond=0) != detected.replace(microsecond=0)
                    or not isinstance(meta['frame_id'], list)
                    or len(meta['frame_id']) < 2
                    or meta['frame_id'][0] != meta['camera_session']):
                raise ValueError('Provenance association differs')
            result['label'] = f"Original detection evidence — captured {ts(meta['captured_at'])}"
        except (ValueError, TypeError, KeyError, AttributeError):
            result.update(warning=INTEGRITY_HELP, blocked=True)
            return result
    if not blob:
        result['warning'] = 'Original evidence unavailable' if key[:1] == ('violation',) else 'Supporting image unavailable'
        return result
    try:
        with Image.open(io.BytesIO(blob)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail(size, Image.Resampling.LANCZOS)
            result['image'] = image.copy()
    except Exception:
        result.update(warning='Evidence unavailable: image is unreadable.', blocked=bool(expected_hash))
    return result


def original_evidence(row, *, size=(1400, 1000)):
    row = dict(row)
    vid = row.get('violation_id', row.get('id'))
    detected = row.get('detection_time', row.get('timestamp'))
    label = ('Original detection evidence — associated detection time '
             f'{ts(detected)} (capture time not recorded)')
    return inspect_evidence(row.get('snapshot'), row.get('snapshot_sha256'),
        key=('violation', vid), label=label, provenance=row.get('snapshot_provenance'),
        violation_id=vid, student_id=row.get('student_id'), detected_at=detected, size=size)


def supporting_evidence(row, *, size=(1400, 1000)):
    row = dict(row)
    return inspect_evidence(row.get('file_data'), row.get('file_sha256'),
        key=('appeal', row.get('appeal_id'), row.get('id')),
        label=f"Student supporting image — uploaded {ts(row.get('uploaded_at'))}", size=size)
