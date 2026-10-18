"""Read-only saved-camera startup audit; no database/models/preferences are opened.

Close other camera readers for comparable sequential attempts. URLs and native
decoder messages are never printed. --snapshots explicitly saves preview contact
sheets (potentially sensitive camera pixels) in the chosen output directory.
First clean preview requires visual review; successful decoding is not proof.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.camera_store import get_saved_ip_cameras, resolve_camera_source
from core.network_capture import NetworkStream


class AuditStream(NetworkStream):
    def _diagnose(self):
        # Classify complete lines, never retain raw stderr or credential URLs.
        self.decoder_warnings = {'missing_reference': 0, 'decode_error': 0}
        pending = b''
        try:
            while True:
                part = os.read(self._proc.stderr.fileno(), 1024)
                if not part:
                    break
                pending += part
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    low = line.lower()
                    if any(s in low for s in (b'could not find ref', b'missing reference', b'reference picture missing')):
                        self.decoder_warnings['missing_reference'] += 1
                    if any(s in low for s in (b'error while decoding', b'corrupt', b'concealing', b'invalid nal')):
                        self.decoder_warnings['decode_error'] += 1
                pending = pending[-4096:]
        except (OSError, ValueError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='front gate')
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--seconds', type=float, default=8)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--snapshots', action='store_true')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        saved = next(c for c in get_saved_ip_cameras() if c['label'].casefold() == args.label.casefold())
        source = resolve_camera_source({'id': 'ip_' + saved['id'], 'type': 'rj45'})
    except Exception:
        print('Saved camera or credential unavailable; no settings changed.')
        return 1
    for attempt in range(1, args.attempts + 1):
        cap = AuditStream(source['url'], transport=source.get('transport', 'tcp'))
        started = time.monotonic()
        tiles, ages = [], []
        count = 0
        next_snapshot = 0
        try:
            opened = cap.open()
            end = time.monotonic() + args.seconds
            while opened and time.monotonic() < end:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(.005)
                    continue
                now = time.monotonic()
                count += 1
                ages.append((now - cap.captured_at) * 1000)
                if args.snapshots and now >= next_snapshot:
                    import cv2
                    tile = cv2.resize(frame, (384, 216))
                    cv2.putText(tile, f'{now-started:.3f}s from open', (8, 22),
                                cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
                    tiles.append(tile)
                    next_snapshot = now + .5
            result = {'attempt': attempt, 'opened': opened, 'error': cap.error_code,
                      'transport_requested': cap.transport, 'settings': cap.get_settings(),
                      'frames_read': count, 'max_post_decode_age_ms': max(ages, default=None),
                      'first_clean_preview_ms': None,
                      'quality_note': 'Requires visual review; OpenCV has no reliable corruption flag.'}
        finally:
            cap.release()
        result['decoder_warnings'] = getattr(cap, 'decoder_warnings', {})
        result['decoder_reaped'] = cap._proc is None or cap._proc.poll() is not None
        result['pipe_threads_stopped'] = not any(t.is_alive() for t in cap._threads)
        if tiles:
            import cv2
            import numpy as np
            while len(tiles) % 4:
                tiles.append(np.zeros_like(tiles[0]))
            sheet = np.vstack([np.hstack(tiles[i:i+4]) for i in range(0, len(tiles), 4)])
            cv2.imwrite(str(args.output_dir / f'attempt-{attempt}.jpg'), sheet)
        with (args.output_dir / 'results.jsonl').open('a') as output:
            output.write(json.dumps(result) + '\n')
        print(json.dumps(result), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
