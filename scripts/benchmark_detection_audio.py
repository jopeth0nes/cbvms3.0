"""Synthetic 720p preview with a silent, deliberately slow audio backend.

No physical camera, model, real audio device, or application database is used.
Uses the existing live rendering benchmark and its controlled 200ms analysis.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.detection_audio import AudioDispatcher, SoundKind
from scripts.benchmark_live_monitor import run, percentile


class SlowAudio:
    def __init__(self):
        self.calls = 0
        self.active = 0
        self.maximum = 0

    def play(self, path, valid):
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            # Simulate one-second device latency without accessing a device.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and valid():
                time.sleep(.01)
        finally:
            self.active -= 1


def measure(seconds, native, enabled):
    backend = SlowAudio()
    audio = AudioDispatcher(backend)
    audio.enabled = enabled
    stop = threading.Event()
    submission_ms = []
    max_pending = 0
    def submit():
        nonlocal max_pending
        kinds = tuple(SoundKind)
        i = 0
        while not stop.wait(.05):
            start = time.perf_counter()
            audio.preview(kinds[i % len(kinds)])
            submission_ms.append((time.perf_counter()-start)*1000)
            with audio._cv:
                max_pending = max(max_pending,len(audio._pending))
            i += 1
    producer = threading.Thread(target=submit)
    producer.start()
    try:
        result = run(seconds,5,True,'after',(None,None,None),200,native)
    finally:
        stop.set(); producer.join(1)
        audio.close()
        if audio._thread:
            audio._thread.join(1)
    result.update(audio='slow_mock' if enabled else 'muted', simulated_playback_ms=1000,
        preview_submission_p95_ms=percentile(submission_ms),
        audio_calls=backend.calls, max_audio_inflight=backend.maximum,
        max_audio_pending=max_pending, native_tk=native, physical_camera=False,
        real_audio=False, audio_worker_stopped=not audio._thread or not audio._thread.is_alive())
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=5)
    parser.add_argument('--native-tk',action='store_true')
    args=parser.parse_args()
    for enabled in (False,True):
        print(json.dumps(measure(args.seconds,args.native_tk,enabled)),flush=True)
