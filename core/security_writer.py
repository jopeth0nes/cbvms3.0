"""Bounded, in-memory retries through log_security_event, never a second log.

Facts qualify against fresh camera pixels before enqueue. Delayed persistence
retains the session cancellation gate, just like the attendance writer.
"""
from copy import copy
from dataclasses import dataclass
import queue
import threading

from core.attendance_writer import _REGISTRY, _REGISTRY_LOCK


@dataclass(frozen=True)
class UnknownEncounter:
    event_key: str
    presence_id: str
    observed_at: str
    source_id: str
    source_label: str
    session_id: str
    snapshot: bytes | None

    def write(self, database, valid_if):
        return database.log_security_event(self.presence_id, observed_at=self.observed_at,
            snapshot_jpeg=self.snapshot, event_key=self.event_key, source_id=self.source_id,
            source_label=self.source_label, session_id=self.session_id, valid_if=valid_if)


class SecurityWriter:
    def __init__(self, database, *, capacity=64, retry_seconds=.5):
        self.db_path = str(database.db_path)
        self.database = copy(database)
        self.database.timeout = .25
        self.queue = queue.Queue(maxsize=capacity)
        self.stop_event, self.done, self.wake = (threading.Event() for _ in range(3))
        self.retry_seconds = retry_seconds
        self._lock = threading.Lock()
        self.pending = set()
        self.accepted = self.cancelled = self.overflow = 0
        self.error = ''
        with _REGISTRY_LOCK:
            _REGISTRY[id(self)] = self

    def start(self):
        threading.Thread(target=self._run, daemon=True, name='unknown-sightings-writer').start()

    def offer(self, fact, cancelled):
        with self._lock:
            if cancelled.is_set() or self.stop_event.is_set() or fact.event_key in self.pending:
                return False
            try:
                self.queue.put_nowait((fact, cancelled))
            except queue.Full:
                self.overflow += 1
                return False
            self.pending.add(fact.event_key)
            return True

    def retry(self):
        self.wake.set()
        return bool(self.error and not self.done.is_set())

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def status(self):
        with self._lock:
            return (f'Unknown encounters: {self.accepted} saved · {len(self.pending)} queued'
                + (f' · Retry pending: {self.error}' if self.error else '')
                + (f' · {self.cancelled} cancelled before save' if self.cancelled else '')
                + (f' · {self.overflow} NOT QUEUED (overflow)' if self.overflow else ''))

    def _run(self):
        current = None
        try:
            while not self.stop_event.is_set():
                if current is None:
                    try:
                        current = self.queue.get(timeout=.1)
                    except queue.Empty:
                        continue
                fact, cancelled = current
                guard = lambda: not cancelled.is_set() and not self.stop_event.is_set()
                try:
                    saved = fact.write(self.database, guard) if guard() else None
                except Exception as exc:
                    with self._lock:
                        self.error = str(exc)
                    self.wake.wait(self.retry_seconds)
                    self.wake.clear()
                    continue
                with self._lock:
                    self.error = ''
                    self.accepted += int(saved is not None)
                    self.cancelled += int(saved is None)
                    self.pending.discard(fact.event_key)
                current = None
        finally:
            with self._lock:
                self.cancelled += len(self.pending)
                self.pending.clear()
            # Release queued snapshots promptly on camera worker shutdown.
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            self.done.set()
