"""Student-scoped background snapshots, with session and owner rejection."""
import queue
import threading
from core.diagnostics import event


def violation_group(row):
    if row.get("status") in ("dismissed", "resolved") or row.get("appeal_status") == "approved":
        return "Resolved"
    if row.get("status") in ("pending_review", "unreviewed"):
        return "Pending"
    return "Confirmed"


def snapshot(database, student_id):
    database.process_expired_deadlines()
    return {
        "_student": database.get_student_by_student_id(student_id) or {},
        "_current_term": database.get_current_academic_term() or {},
        "_violations": database.get_visible_violations_for_student(student_id),
        "_strike_summary": database.get_strike_summary(student_id),
        "_notifications": database.get_notifications_for_student(student_id),
        "_appeals": database.get_appeals_for_student(student_id),
        "_active_suspension": database.get_active_suspension(student_id),
    }


def comparable(data):
    return {k: ([{a: b for a, b in row.items() if a != "appeal_remaining_seconds"}
                 for row in v] if k == "_violations" else v) for k, v in data.items()}


class PortalRefresh:
    def __init__(self, database, student_id, loader=snapshot):
        if not isinstance(student_id, str) or not student_id.strip():
            raise ValueError("An authenticated string student ID is required")
        self.database, self.student_id, self.loader = database, student_id.strip(), loader
        self.results = queue.Queue(maxsize=1)
        self.busy = False
        self.generation = 0
        self.closed = False

    def request(self):
        if self.closed or self.busy:
            return False
        self.busy = True
        generation, owner = self.generation, self.student_id
        def run():
            try:
                data, error = self.loader(self.database, owner), None
            except Exception as exc:
                data, error = None, str(exc)
                event("portal_refresh_failed", student_id=owner, error=error)
            self.results.put((generation, owner, data, error))
        threading.Thread(target=run, daemon=True, name="student-portal-read").start()
        return True

    def poll(self, owner):
        try:
            generation, sid, data, error = self.results.get_nowait()
        except queue.Empty:
            return None
        self.busy = False
        if self.closed or generation != self.generation or sid != owner:
            return None
        return data, error

    def close(self):
        self.closed = True
        self.generation += 1

# The desktop portal uses a bounded, page-scoped worker. PortalRefresh above is
# retained for callers requesting a complete non-UI snapshot.
from dataclasses import dataclass, field
import io
import time
from PIL import Image, ImageOps
from database.db_manager import CBVMSDatabase

# Detailed violation cards create many native text widgets. Ten records keep
# first layout responsive on macOS; evidence remains lazy and pages stay bounded.
PAGE_SIZE = 10


def prepare_photo(blob, size):
    if not blob:
        return None
    with Image.open(io.BytesIO(blob)) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail(size, Image.Resampling.LANCZOS)
        return image.copy()


def page_snapshot(database, student_id, page, *, offset=0, group='All', violation_id=None, appeal_ids=(), appeal_id=None):
    """Load only a page's metadata; evidence bytes are fetched on explicit request."""
    started = time.monotonic()
    database.process_expired_deadlines(student_id=student_id)
    data = {'_unread_count': database.get_unread_notification_count(student_id)}
    if page in {'dashboard', 'violations', 'profile'}:
        data['_current_term'] = database.get_current_academic_term() or {}
        data['_strike_summary'] = database.get_strike_summary(student_id)
    if page in {'dashboard', 'profile'}:
        with database.connect() as conn:
            fields = ','.join(row[1] for row in conn.execute('PRAGMA table_info(students)')
                              if row[1] not in ('encoding', 'photo', 'profile_photo'))
            if page == 'profile':
                fields += ',COALESCE(NULLIF(profile_photo, X\'\'), photo) AS portal_photo'
            row = conn.execute(f'SELECT {fields} FROM students WHERE student_id = ?', (student_id,)).fetchone()
            data['_student'] = dict(row) if row else {}
        data['_active_suspension'] = database.get_active_suspension(student_id)
    if page in {'dashboard', 'violations'}:
        with database.connect() as conn:
            counts = conn.execute('''SELECT
                SUM(v.status IN ('pending_review','unreviewed')) AS pending,
                SUM(v.status NOT IN ('pending_review','unreviewed','dismissed','resolved')
                    AND COALESCE(a.status,'') != 'approved') AS confirmed,
                SUM(v.status IN ('dismissed','resolved') OR a.status = 'approved') AS resolved
                FROM violations v LEFT JOIN appeals a ON a.violation_id = v.id
                WHERE v.student_id = ?''', (student_id,)).fetchone()
            data['_violation_counts'] = {k: int(v or 0) for k, v in dict(counts).items()}
            data['_pending_appeals'] = conn.execute(
                "SELECT COUNT(*) FROM appeals WHERE student_id = ? AND status = 'pending'", (student_id,)).fetchone()[0]
        rows = database.get_visible_violations_for_student(
            student_id, process_deadlines=False, include_snapshot=False,
            limit=6 if page == 'dashboard' else PAGE_SIZE+1, offset=offset,
            group=group, violation_id=violation_id)
        data['_has_more'] = len(rows) > (5 if page == 'dashboard' else PAGE_SIZE)
        data['_violations'] = rows[:5 if page == 'dashboard' else PAGE_SIZE]
    if page == 'notifications':
        rows = database.get_notifications_for_student(student_id, limit=PAGE_SIZE+1, offset=offset)
        data['_has_more'], data['_notifications'] = len(rows) > PAGE_SIZE, rows[:PAGE_SIZE]
        related = {}
        for vid in {n['violation_id'] for n in rows[:PAGE_SIZE] if n.get('violation_id')}:
            records = database.get_visible_violations_for_student(student_id,
                process_deadlines=False, include_snapshot=False, violation_id=vid, limit=1)
            if records:
                record = dict(records[0])
                record.pop('appeal_remaining_seconds', None)
                related[vid] = record
        data['_notification_violations'] = related
    if page == 'appeals':
        rows = database.get_appeals_for_student(student_id, limit=PAGE_SIZE+1,
            offset=0 if appeal_id else offset, appeal_id=appeal_id)
        data['_has_more'], data['_appeals'] = len(rows) > PAGE_SIZE, rows[:PAGE_SIZE]
    records = data.get('_violations', []) + list(data.get('_notification_violations', {}).values())
    eligibility = {r['id']: {'eligible': bool(r.get('can_appeal')),
        'reason': r.get('appeal_eligibility_reason'), 'deadline': r.get('appeal_deadline')}
        for r in records}
    for vid in set(appeal_ids) - eligibility.keys():
        eligibility[vid] = database.get_appeal_eligibility(vid, student_id)
    data['_appeal_eligibility'] = eligibility
    fetched = time.monotonic()
    if page == 'profile':
        blob = data['_student'].pop('portal_photo', None)
        data['_profile_photo_key'] = blob  # equality compares bytes, never Tk image objects
        data['_profile_image'] = prepare_photo(blob, (196, 196))
    data['_timings'] = {'database_ms': round((fetched-started)*1000, 2),
                        'prepare_ms': round((time.monotonic()-fetched)*1000, 2)}
    return data


@dataclass
class PortalRequest:
    id: int
    page: str
    generation: int
    operation: object
    cancelled: threading.Event = field(default_factory=threading.Event)
    created: float = field(default_factory=time.monotonic)
    state: str = 'loading'
    action: bool = False
    timeout: float = 4.


class PortalWorkerDatabase(CBVMSDatabase):
    """Only used by the portal worker: short lock waits and cancellable SQL."""
    request = None

    def connect(self):
        request = self.request
        if request and (request.cancelled.is_set() or time.monotonic()-request.created > request.timeout):
            raise TimeoutError('Request cancelled or timed out')
        conn = super().connect()
        if request:
            conn.set_progress_handler(lambda: int(request.cancelled.is_set() or
                time.monotonic()-request.created > request.timeout), 1000)
        return conn


class PortalRequests:
    """One worker and one replaceable pending read; no Tk objects in results."""
    def __init__(self, database, student_id):
        if not isinstance(student_id, str) or not student_id.strip():
            raise ValueError('An authenticated string student ID is required')
        self.student_id = student_id.strip()
        self.database = PortalWorkerDatabase(database.db_path, timeout=.75)
        self.results = queue.Queue()
        self.closed = False
        self.sequence = 0
        self.current = None
        self.pending = None
        self.condition = threading.Condition()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name='student-portal-read', daemon=True)
        self.thread.start()

    @property
    def busy(self):
        with self.condition:
            return self.current is not None or self.pending is not None

    @staticmethod
    def _cancel(request):
        if request:
            request.cancelled.set()
            request.state = 'cancelled'

    def request(self, page, generation, operation, *, action=False):
        with self.condition:
            if self.closed:
                return None
            # Never replace an authorized mutation, even before it starts.
            if self.pending and self.pending.action:
                return None
            if action and self.current and self.current.action:
                return None
            self.sequence += 1
            request = PortalRequest(self.sequence, page, generation, operation, action=action)
            self._cancel(self.pending)
            if self.current and not self.current.action:
                self._cancel(self.current)
            self.pending = request
            self.condition.notify()
            return request

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.pending is not None)
                    if self.closed:
                        return
                    request = self.current = self.pending
                    self.pending = None
                self.database.request = request
                try:
                    data = request.operation(self.database, self.student_id)
                    error = None
                except Exception as exc:
                    data, error = None, str(exc)
                    event('portal_request_failed', page=request.page, request_id=request.id,
                          student_id=self.student_id, error=error)
                finally:
                    self.database.request = None
                with self.condition:
                    self.current = None
                    request.state = ('cancelled' if request.cancelled.is_set() else
                                     'failed' if error else 'loaded')
                    if not self.closed:
                        self.results.put((request, data, error))
        finally:
            self.done.set()

    def poll(self, owner):
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return None
        return result if not self.closed and owner == self.student_id else None

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self._cancel(self.current)
            self._cancel(self.pending)
            self.pending = None
            self.condition.notify()
