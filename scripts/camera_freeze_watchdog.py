"""Independent diagnostic supervisor: works even if the GUI holds the GIL.

Only stack symbols are retained from macOS sample, never its process header,
arguments, environment, binary paths, Python locals, camera URLs or pixels.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def sanitized_sample(raw):
    lines = []
    inside = False
    for line in raw.splitlines():
        if line.startswith('Call graph:'):
            inside = True
        if line.startswith('Total number in stack') or line.startswith('Binary Images:'):
            break
        if inside:
            lines.append(line.replace(str(Path.home()), '<HOME>'))
    return '\n'.join(lines) + '\n' if lines else 'No call graph returned.\n'


def supervise(pid, folder, threshold=5, lifetime=900):
    started = time.monotonic()
    captures = 0
    last_capture = 0
    with (folder / 'watchdog.jsonl').open('w', buffering=1) as log:
        def record(event, **fields):
            log.write(json.dumps(dict(event=event, monotonic=time.monotonic(), **fields))+'\n')
        while time.monotonic() - started < lifetime:
            if (folder / 'finished').exists():
                return
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                record('parent_exited')
                return
            try:
                beat = float((folder / 'heartbeat').read_text())
            except (OSError, ValueError):
                time.sleep(.25)
                continue
            now = time.monotonic()
            if now - beat > threshold and now - last_capture > 15 and captures < 3:
                captures += 1
                last_capture = now
                record('heartbeat_stalled', gap_seconds=now-beat, capture=captures)
                try:
                    os.kill(pid, signal.SIGUSR1)
                except ProcessLookupError:
                    return
                if sys.platform == 'darwin':
                    try:
                        result = subprocess.run(['/usr/bin/sample', str(pid), '2', '10'],
                            capture_output=True, text=True, timeout=8)
                        (folder / f'native-sample-{captures}.txt').write_text(sanitized_sample(result.stdout))
                        record('sample_complete', returncode=result.returncode)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        record('sample_failed', kind=type(exc).__name__)
            time.sleep(.25)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pid', type=int)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    supervise(args.pid, args.folder)
