"""Lightweight notification broker for CBVMS."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Notification:
    id: int
    student_name: str
    violation: str
    timestamp: float = field(default_factory=time.time)
    acknowledged: bool = False
    valid_if: Callable[[], bool] | None = field(default=None, repr=False, compare=False)


def play_alert() -> None:
    """Play a short alert sound (cross-platform, no extra deps). Silent on failure."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["afplay", "/System/Library/Sounds/Glass.aiff"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        try:
            import winsound  # Windows only
            winsound.Beep(880, 220)
            return
        except ImportError:
            pass
        # Linux fallback
        subprocess.Popen(
            ["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Silent fallback — never let a missing audio backend crash the app.


class Notifier:
    def __init__(self) -> None:
        self._listeners: list[Callable[[Notification], None]] = []
        self._log: list[Notification] = []
        self._lock = threading.Lock()
        self._counter = 0
        self.sound_enabled: bool = True
        self.toast_enabled: bool = True

    def subscribe(self, fn: Callable[[Notification], None]) -> None:
        self._listeners.append(fn)

    def notify(self, student_name: str, violation: str, *, valid_if=None, observed_at=None) -> Notification | None:
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
        if self.sound_enabled:
            def guarded_sound():
                if valid_if is None or valid_if():
                    play_alert()
            threading.Thread(target=guarded_sound, daemon=True).start()
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
