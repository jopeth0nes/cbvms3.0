"""Bounded asynchronous sighting writer with metadata-only restart journals.

Journals are not an attendance database: reports and deduplication use SQLite only.
A process lock prevents another live worker from recovering our queued facts.
"""
from collections import OrderedDict
from copy import copy

import os

if os.name == "nt":
    import msvcrt
else:
    import fcntl
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import uuid
import weakref

from core.attendance import Sighting
from core.discipline import parse_db_datetime
from core.diagnostics import event

def _lock_owner(handle):
    if os.name == 'nt':
        handle.seek(0)
        if not handle.read(1):
            handle.write('0')
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


_REGISTRY = weakref.WeakValueDictionary()
_REGISTRY_LOCK = threading.Lock()


def writer_status(database):
    with _REGISTRY_LOCK:
        workers = [w for w in list(_REGISTRY.values()) if w.db_path == str(database.db_path)]
    return ' · '.join(w.status() for w in workers) or 'No live sighting writer in this process.'


def retry_writers(database):
    with _REGISTRY_LOCK:
        workers=[w for w in list(_REGISTRY.values()) if w.db_path==str(database.db_path)]
    return sum(w.retry() for w in workers)


class AttendanceWriter:
    def __init__(self, database, *, capacity=256, retry_seconds=.5):
        self.db_path = str(database.db_path)
        self.database = copy(database)
        self.database.timeout = .25
        self.queue = queue.Queue(maxsize=capacity)
        self.retry_seconds = retry_seconds
        self.stop_event = threading.Event()
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._reserved = OrderedDict()
        self.cooldown = 300
        self.overflow = self.accepted = self.cancelled = 0
        self.active = False
        self.error = ''
        self.warning = ''
        self.saved_pending = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name='attendance-writer')
        with _REGISTRY_LOCK:
            _REGISTRY[id(self)] = self

    def start(self):
        self._thread.start()

    def retry(self):
        # Explicit recovery after startup/filesystem failure. DB lock retries are automatic.
        with self._lock:
            if not self.done.is_set() or self.stop_event.is_set():
                return False
            self.done.clear()
            self.error=''
            self._thread=threading.Thread(target=self._run,daemon=True,name='attendance-writer')
            self._thread.start()
            return True

    def offer(self, sighting, cancelled):
        if not isinstance(sighting,Sighting) or cancelled.is_set() or self.stop_event.is_set() or self.done.is_set():
            return False
        observed = parse_db_datetime(sighting.observed_at).timestamp()
        with self._lock:
            last = self._reserved.get(sighting.student_id)
            # Load reduction only; SQLite independently enforces cross-camera/restart cooldown.
            if last is not None and 0 <= observed-last < self.cooldown:
                return False
            try:
                self.queue.put_nowait((sighting,cancelled))
            except queue.Full:
                self.overflow += 1
                event('attendance_overflow', unsaved=self.overflow, capacity=self.queue.maxsize)
                return False
            self._reserved[sighting.student_id] = observed
            self._reserved.move_to_end(sighting.student_id)
            if len(self._reserved)>4096:
                self._reserved.popitem(last=False)
            return True

    def status(self):
        with self._lock:
            text = f'Sightings: {self.accepted} saved · {self.queue.qsize()+int(self.active)} queued'
            if self.warning:
                text += f' · {self.warning}'
            if self.error:
                text += f' · {self.error}' if self.done.is_set() else f' · Retry pending: {self.error}'
            if self.overflow:
                text += f' · {self.overflow} NOT QUEUED (overflow)'
            if self.saved_pending:
                text += f' · {self.saved_pending} journaled for restart'
            return text

    def stop(self):
        self.stop_event.set()

    @staticmethod
    def _journal(directory, sighting):
        destination = directory / (sighting.key+'.json')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=directory, delete=False) as output:
                temporary = Path(output.name)
                json.dump(sighting.payload(),output,ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary,destination)
            except FileExistsError:
                pass
            # Persist the directory entry as well as the file content.
            if os.name != 'nt':
                fd = os.open(directory,os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return destination
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _run(self):
        base = Path(self.db_path).with_name(Path(self.db_path).name+'.attendance-pending')
        locks = []
        current = None
        recovering = None
        journaled = {}
        try:
            base.mkdir(mode=0o700,exist_ok=True)
            own = base / uuid.uuid4().hex
            own.mkdir(mode=0o700)
            lock = (own/'owner.lock').open('a+')
            _lock_owner(lock)
            locks.append(lock)
            def recovery():
                for directory in base.iterdir():
                    if directory==own or not directory.is_dir():
                        continue
                    candidate=(directory/'owner.lock').open('a+')
                    try:
                        _lock_owner(candidate)
                    except OSError:
                        candidate.close()
                        continue
                    locks.append(candidate)
                    try:
                        for path in directory.glob('*.json'):
                            yield path
                    finally:
                        locks.remove(candidate)
                        candidate.close()
            recovering=iter(recovery())
            recovering_done=False
            next_settings=0.
            while not self.stop_event.is_set():
                if time.monotonic()>=next_settings:
                    try:
                        self.cooldown=self.database.get_attendance_cooldown()
                    except Exception as exc:
                        self.error=str(exc)
                    next_settings=time.monotonic()+2
                if current is None:
                    try:
                        fact,cancelled=self.queue.get(timeout=.1)
                        current=(fact,cancelled,journaled.pop(fact.key,None))
                    except queue.Empty:
                        if recovering_done:
                            continue
                        path=next(recovering,None)
                        if path is None:
                            recovering_done=True
                            continue
                        try:
                            fact=Sighting(**json.loads(path.read_text(encoding='utf8')))
                            if path.stem != fact.key:
                                raise ValueError('Journal identity checksum mismatch')
                            current=(fact,threading.Event(),path)
                        except Exception as exc:
                            self.saved_pending+=1
                            self.warning=f'Unreadable pending sighting retained: {exc}'
                            event('attendance_journal_error',error=self.warning)
                            continue
                self.active=True
                fact,cancelled,path=current
                if cancelled.is_set():
                    if path:
                        path.unlink(missing_ok=True)
                    self.cancelled+=1
                    self._release(fact)
                    current=None
                    self.active=False
                    continue
                try:
                    if path is None:
                        path=self._journal(own,fact)
                        current=(fact,cancelled,path)
                    saved=self.database.record_attendance(fact.student_id,sighting=fact,
                        observed_at=fact.observed_at,valid_if=lambda: not cancelled.is_set())
                    path.unlink(missing_ok=True)
                    if cancelled.is_set():
                        self.cancelled+=1
                        self._release(fact)
                    elif saved:
                        self.accepted+=1
                    else:
                        self._align_cooldown(fact)
                    self.error=''
                    current=None
                    self.active=False
                except Exception as exc:
                    self.error=str(exc)
                    event('attendance_retry_pending',error=self.error,event_key=fact.key)
                    # Journal the bounded backlog even while SQLite is locked. Only this
                    # thread consumes the queue; producers never perform file I/O.
                    with self.queue.mutex:
                        backlog=list(self.queue.queue)
                    for pending, flag in backlog:
                        if flag.is_set():
                            old=journaled.pop(pending.key,None)
                            if old:
                                old.unlink(missing_ok=True)
                        elif pending.key not in journaled:
                            try:
                                journaled[pending.key]=self._journal(own,pending)
                            except Exception as journal_error:
                                self.warning=f'Queued records not yet durable: {journal_error}'
                                event('attendance_journal_error',error=self.warning)
                    self.stop_event.wait(self.retry_seconds)
            # Shutdown is metadata-only: keep retryable facts, remove explicitly cancelled work.
            remaining=[] if current is None else [current]
            while True:
                try:
                    fact,cancelled=self.queue.get_nowait()
                    remaining.append((fact,cancelled,journaled.pop(fact.key,None)))
                except queue.Empty:
                    break
            for fact,cancelled,path in remaining:
                if cancelled.is_set():
                    if path:
                        path.unlink(missing_ok=True)
                    continue
                try:
                    self._journal(own,fact) if path is None else None
                    self.saved_pending+=1
                except Exception as exc:
                    self.overflow+=1
                    self.error=f'UNSAVED on shutdown: {exc}'
                    event('attendance_unsaved',event_key=fact.key,error=self.error)
        except Exception as exc:
            self.error=f'Writer unavailable: {exc}'
            event('attendance_writer_failed',error=self.error,unsaved=self.queue.qsize())
        finally:
            self.active=False
            if recovering is not None:
                recovering.close()
            for lock in locks:
                lock.close()
            self.done.set()

    def _align_cooldown(self,fact):
        # A different camera may have committed first. Suppressed observations
        # must not start a fresh advisory cooldown and extend the DB boundary.
        with self.database.connect() as conn:
            row=conn.execute('SELECT MAX(observed_at) FROM attendance_events WHERE student_id=? AND observed_at<=?',
                (fact.student_id,fact.observed_at)).fetchone()
        observed=parse_db_datetime(fact.observed_at).timestamp()
        with self._lock:
            if self._reserved.get(fact.student_id)==observed:
                if row[0]:
                    self._reserved[fact.student_id]=parse_db_datetime(row[0]).timestamp()
                else:
                    self._reserved.pop(fact.student_id,None)

    def _release(self,fact):
        with self._lock:
            if self._reserved.get(fact.student_id)==parse_db_datetime(fact.observed_at).timestamp():
                self._reserved.pop(fact.student_id,None)
