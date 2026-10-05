"""Lightweight notification broker for CBVMS."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from core.detection_audio import AudioDispatcher


@dataclass
class Notification:
    id: int
    student_name: str
    violation: str
    timestamp: float = field(default_factory=time.time)
    acknowledged: bool = False
    valid_if: Callable[[], bool] | None = field(default=None, repr=False, compare=False)
    category: str = "violations"


_compat_audio = AudioDispatcher()
_audio_lock = threading.Lock()


def _default_audio() -> AudioDispatcher:
    """Share the application master/worker with legacy audio-only callers."""
    global _compat_audio
    with _audio_lock:
        if _compat_audio.closed:
            _compat_audio = AudioDispatcher()
        return _compat_audio


def play_alert() -> None:
    """Compatibility audio-only entry point, using a bounded dispatcher."""
    _default_audio().generic()


class Notifier:
    def __init__(self, *, audio=None) -> None:
        self._listeners: list[Callable[[Notification], None]] = []
        self._log: list[Notification] = []
        self._lock = threading.Lock()
        self._counter = 0
        self.audio = audio if audio is not None else _default_audio()
        self.toast_enabled: bool = True

    @property
    def sound_enabled(self) -> bool:
        return self.audio.enabled

    @sound_enabled.setter
    def sound_enabled(self, value) -> None:
        self.audio.enabled = value

    def close(self) -> None:
        self.audio.close()

    def subscribe(self, fn: Callable[[Notification], None]) -> None:
        self._listeners.append(fn)

    def notify(self, student_name: str, violation: str, *, valid_if=None, observed_at=None, play_sound=True) -> Notification | None:
        if valid_if is not None and not valid_if():
            return None
        with self._lock:
            if valid_if is not None and not valid_if():
                return None
            self._counter += 1
            notif = Notification(id=self._counter, student_name=student_name, violation=violation,
                                 timestamp=time.time() if observed_at is None else observed_at,
                                 valid_if=valid_if)
            self._log.append(notif)
        for fn in self._listeners:
            try:
                fn(notif)
            except Exception:
                pass
        if play_sound:
            self.audio.generic(valid_if=valid_if)
        return notif

    def acknowledge(self, notif_id: int) -> None:
        with self._lock:
            for n in self._log:
                if n.id == notif_id:
                    n.acknowledged = True
                    break

    def mark_all_read(self) -> None:
        with self._lock:
            for n in self._log:
                n.acknowledged = True

    def get_log(self, *, unacknowledged_only: bool = False) -> list[Notification]:
        with self._lock:
            items = list(self._log)
        if unacknowledged_only:
            items = [n for n in items if not n.acknowledged]
        return list(reversed(items))

    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for n in self._log if not n.acknowledged)
