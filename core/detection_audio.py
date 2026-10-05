"""Bounded, serial, cancellable local audio. No attendance/discipline writes or Tk.

The backend runs only on the dispatch thread. Producers only update bounded memory.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import wave
from typing import Callable

from core.live_state import UniformAssessment

LOG = logging.getLogger('cbvms.audio')
ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets' / 'sounds'


class SoundKind(IntEnum):
    POSITIVE = 1
    VIOLATION = 2
    SUSPENSION = 3
    GENERIC = 0  # Separate compatibility/unknown alert; never inferred from text.


ASSETS = {kind: ASSET_DIR / f'{kind.name.lower()}.wav' for kind in SoundKind}
LABELS = {SoundKind.POSITIVE: 'Correct uniform chime',
          SoundKind.VIOLATION: 'Accepted violation tone',
          SoundKind.SUSPENSION: 'Active suspension alert'}


def select_sound(assessment, active_suspension: bool) -> SoundKind | None:
    if not assessment.reliable_identity or not assessment.student_id:
        return None
    if active_suspension:
        return SoundKind.SUSPENSION
    if any(code in ('wrong_uniform', 'earring') for code in assessment.accepted_categories):
        return SoundKind.VIOLATION
    if (not assessment.accepted_categories
            and assessment.uniform_assessment is UniformAssessment.CORRECT):
        return SoundKind.POSITIVE
    return None


class LocalAudioBackend:
    """One file at a time. Cancellation is polled at 20ms; no shell or network."""
    def play(self, path: Path, valid: Callable[[], bool]):
        with wave.open(str(path), 'rb') as wav:
            duration = wav.getnframes() / wav.getframerate()
        if not 0 < duration <= 2:
            raise ValueError(f'Expected a brief WAV asset: {path}')
        if not valid():
            return
        if sys.platform == 'win32':
            import winsound
            try:
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                deadline = time.monotonic() + duration
                while time.monotonic() < deadline and valid():
                    time.sleep(.02)
            finally:
                winsound.PlaySound(None, 0)
            return
        candidates = ('afplay',) if sys.platform == 'darwin' else ('paplay', 'aplay')
        player = next((shutil.which(name) for name in candidates if shutil.which(name)), None)
        if player is None:
            raise RuntimeError(f'No audio player available; install/configure {" or ".join(candidates)}')
        if not valid():
            return
        process = subprocess.Popen([player, str(path)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + duration + 2
            while process.poll() is None:
                if not valid():
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'{player} did not finish playback; check audio device')
                time.sleep(.02)
            if process.returncode:
                raise RuntimeError(f'{player} exited {process.returncode}; check output device and audio service')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=.2)


@dataclass
class AudioRequest:
    key: tuple
    kind: SoundKind
    created: float
    valid_if: Callable[[], bool]
    resolve: Callable[[], SoundKind | None] | None = None
    person: tuple | None = None
    epoch: int = 0
    relevance: Callable[[], bool] = lambda: True
    cancelled: threading.Event = field(default_factory=threading.Event)


class AudioDispatcher:
    MAX_PENDING = 24
    MAX_ENCOUNTERS = 4096
    COOLDOWN = 15.0

    def __init__(self, backend=None, *, clock=time.monotonic):
        self.backend = backend or LocalAudioBackend()
        self.clock = clock
        self._cv = threading.Condition()
        self._pending = OrderedDict()
        self._states = OrderedDict()
        self._cooldowns = OrderedDict()
        self._enabled = True
        self._kinds = {kind: True for kind in SoundKind}
        self._epoch = 0
        self._closed = False
        self._thread = None
        self._active = None
        self.last_error = ''
        self._last_error_at = -float('inf')

    @property
    def closed(self):
        with self._cv:
            return self._closed

    @property
    def enabled(self):
        with self._cv:
            return self._enabled

    @enabled.setter
    def enabled(self, value):
        with self._cv:
            self._enabled = bool(value)
            if not value:
                self._discard_locked()

    def kind_enabled(self, kind):
        with self._cv:
            return self._kinds[kind]

    def set_kind_enabled(self, kind, value):
        with self._cv:
            self._kinds[kind] = bool(value)
            if not value:
                for key, req in list(self._pending.items()):
                    if req.kind == kind:
                        req.cancelled.set()
                        del self._pending[key]
                if self._active is not None and self._active.kind == kind:
                    self._active.cancelled.set()

    def _discard_locked(self):
        self._epoch += 1
        self._pending.clear()
        if self._active is not None:
            self._active.cancelled.set()
        self._cv.notify_all()

    def cancel_detection(self):
        # Also discard previews/generic work on camera/navigation boundaries.
        with self._cv:
            self._discard_locked()
            self._states.clear()
            self._cooldowns.clear()

    def close(self):
        with self._cv:
            self._closed = True
            self._discard_locked()

    def _enqueue(self, req):
        if self._closed or not self._enabled or not self._kinds[req.kind]:
            return False
        if req.key in self._pending:
            req.created = self._pending[req.key].created
        elif len(self._pending) >= self.MAX_PENDING:
            victim = min(self._pending.values(), key=lambda r: (r.kind, r.created))
            if victim.kind > req.kind:
                return False
            del self._pending[victim.key]
        self._pending[req.key] = req
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name='cbvms-audio')
            self._thread.start()
        self._cv.notify()
        return True

    def observe_batch(self, observations, *, scope):
        """Items: (assessment, cheap freshness guard, suspension reader, motion guard).

        A full confirmed analysis batch invalidates absent/changed people. No DB
        calls occur here. Readers are run on the audio worker immediately pre-play.
        """
        with self._cv:
            now = self.clock()
            seen = set()
            for observation in observations:
                assessment, guard, suspension_reader = observation[:3]
                relevance = observation[3] if len(observation) > 3 else (lambda: True)
                key = ('detection', scope, assessment.presence_id, assessment.student_id)
                seen.add(key)
                kind = select_sound(assessment, assessment.active_suspension)
                previous = self._states.get(key)
                self._states[key] = (kind, now)
                self._states.move_to_end(key)
                if previous is not None and previous[0] != kind:
                    self._pending.pop(key, None)
                    if self._active is not None and self._active.key == key:
                        self._active.cancelled.set()
                if kind is None:
                    continue
                if previous is not None and previous[0] == kind and key not in self._pending:
                    continue  # No periodic repetition for a held state, even after cooldown.
                person = (scope, assessment.student_id)
                prior = self._cooldowns.get(person)
                if prior and now - prior[0] < self.COOLDOWN and kind <= prior[1]:
                    continue
                self._enqueue(AudioRequest(key, kind, now, guard,
                    lambda a=assessment, reader=suspension_reader: select_sound(a, bool(reader())),
                    person, self._epoch, relevance))
            for key in list(self._states):
                if key not in seen:
                    del self._states[key]
                    self._pending.pop(key, None)
                    if self._active is not None and self._active.key == key:
                        self._active.cancelled.set()
            while len(self._states) > self.MAX_ENCOUNTERS:
                self._states.popitem(last=False)

    def preview(self, kind):
        with self._cv:
            key = ('preview', kind)
            if self._active is not None and self._active.key == key:
                return False
            return self._enqueue(AudioRequest(key, kind, self.clock(), lambda: True, epoch=self._epoch))

    def generic(self, *, valid_if=None):
        with self._cv:
            return self._enqueue(AudioRequest(('generic',), SoundKind.GENERIC, self.clock(),
                                               valid_if or (lambda: True), epoch=self._epoch))

    def _valid(self, req):
        with self._cv:
            ttl = 1.5 if req.kind == SoundKind.POSITIVE else 3.0
            allowed = (not self._closed and self._enabled and self._kinds[req.kind]
                       and req.epoch == self._epoch and not req.cancelled.is_set()
                       and 0 <= self.clock() - req.created <= ttl)
            if req.person is not None:
                state = self._states.get(req.key)
                allowed = allowed and state is not None and state[0] == req.kind
        return allowed and req.valid_if()

    def _run(self):
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                req = max(self._pending.values(), key=lambda r: (r.kind, -r.created))
                del self._pending[req.key]
                self._active = req
            try:
                if not self._valid(req):
                    continue
                # Fail closed if suspension lookup fails or current priority differs.
                if req.resolve is not None and req.resolve() != req.kind:
                    continue
                if not req.relevance() or not self._valid(req):
                    continue
                with self._cv:
                    # An urgent arrival during a slow read must take the next slot.
                    if any(p.kind > req.kind for p in self._pending.values()):
                        if req.key not in self._pending and not req.cancelled.is_set():
                            self._enqueue(req)
                        continue
                    if req.person is not None:
                        prior = self._cooldowns.get(req.person)
                        now = self.clock()
                        if prior and now-prior[0] < self.COOLDOWN and req.kind <= prior[1]:
                            continue
                        self._cooldowns[req.person] = (now, req.kind)
                        self._cooldowns.move_to_end(req.person)
                        while len(self._cooldowns) > self.MAX_ENCOUNTERS:
                            self._cooldowns.popitem(last=False)
                self.backend.play(ASSETS[req.kind], lambda: self._valid(req))
            except Exception as exc:
                self.last_error = f'{req.kind.name.lower()} audio unavailable: {exc}'
                if self.clock() - self._last_error_at >= 10:
                    LOG.warning('%s', self.last_error)
                    self._last_error_at = self.clock()
            finally:
                with self._cv:
                    self._active = None
                    self._cv.notify_all()
