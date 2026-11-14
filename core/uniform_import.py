"""Window-independent import jobs with bounded progress and processing recovery.

Workers publish one latest snapshot; Tk polls it without callbacks into a window.
Only the most recent job per dataset is retained, with bounded completed history.
"""
from __future__ import annotations

import copy
import threading
import time
import uuid

_JOBS: dict[str, "ImportJob"] = {}
_GUARD = threading.RLock()


class ImportJob:
    def __init__(self, dataset, kind, source, category):
        self.dataset, self.kind, self.source, self.category = dataset, kind, source, category
        self.job_id = uuid.uuid4().hex
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._decision = threading.Event()
        self._rows, self._choices = [], {}
        self._snapshot = {"job_id": self.job_id, "category": category, "phase": "discovery",
            "active": True, "discovered": 0, "total": None, "processed": 0,
            "imported": 0, "duplicates": 0, "skipped": 0, "failed": 0,
            "current": None, "cancelled": False, "error": None, "awaiting": 0,
            "training_ready": 0, "quarantined": 0, "processing_failed": 0,
            "processing_excluded": 0, "processing_total": 0, "processing_done": 0, "unprocessed": None}

    @property
    def active(self):
        with self._lock:
            return self._snapshot["active"]

    def cancel(self):
        # File cancellation does not cancel the shared torso processor.
        with self._lock:
            if self._snapshot["active"] and self._snapshot["phase"] in {"discovery", "checking_duplicates", "waiting_duplicates", "importing"}:
                self._cancel.set()
                self._decision.set()

    def duplicate_rows(self):
        with self._lock:
            return copy.deepcopy(self._rows)

    def duplicate_choices(self):
        with self._lock:
            return dict(self._choices)

    def set_duplicate_choice(self, row_id, action, apply_to_type=False):
        from core.uniform_duplicates import ACTIONS
        with self._lock:
            if self._snapshot['phase'] != 'waiting_duplicates':
                return
            row = next(r for r in self._rows if r['row_id'] == row_id)
            if action not in ACTIONS[row['match_type']]:
                raise ValueError('Invalid duplicate choice')
            for target in self._rows:
                if target['row_id'] == row_id or apply_to_type and target['match_type'] == row['match_type']:
                    self._choices[target['row_id']] = action

    def submit_duplicate_choices(self):
        with self._lock:
            if self._snapshot['phase'] == 'waiting_duplicates':
                self._snapshot['phase'] = 'importing'
                self._decision.set()

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _publish(self, **values):
        with self._lock:
            self._snapshot.update(values)


def get_import_job(dataset):
    with _GUARD:
        return _JOBS.get(str(dataset.root.absolute()))


def start_import(dataset, kind, source, category):
    if kind not in {"paths", "folder", "zip"}:
        raise ValueError("kind must be paths, folder, or zip")
    key = str(dataset.root.absolute())
    with _GUARD:
        current = _JOBS.get(key)
        if current and current.active:
            return current
        job = ImportJob(dataset, kind, source, category)
        _JOBS[key] = job
        finished = [k for k, value in _JOBS.items() if k != key and not value.active]
        for old in finished[:-31]:
            _JOBS.pop(old, None)
    try:
        threading.Thread(target=_run, args=(job,), daemon=True,
                         name="uniform-import-" + job.job_id[:8]).start()
    except Exception as exc:
        job._publish(active=False, phase="done", error=str(exc))
        raise
    return job


def _processing_counts(dataset, ids):
    rows = {row['image_id']: row for row in dataset.snapshot()['records'] if row.get('image_id') in ids}
    excluded = sum(row.get('status') == 'excluded' for row in rows.values())
    states = [row.get('processing_state', 'queued') for row in rows.values() if row.get('status') != 'excluded']
    ready = states.count('training_ready')
    quarantined = states.count('quarantined')
    failed = states.count('failed')
    done = ready + quarantined + failed + excluded
    return dict(training_ready=ready, quarantined=quarantined, processing_failed=failed,
                processing_excluded=excluded, processing_total=len(ids), processing_done=done,
                awaiting=len(ids) - done)


def _run(job):
    ids = set()
    from core.uniform_duplicates import ImportPlan
    try:
        plan = ImportPlan(job.dataset)
    except Exception as exc:
        job._publish(phase='done', active=False, error=f'Cannot stage import: {exc}')
        return

    def progress(state):
        values = {key: value for key, value in state.items() if key not in {'image_ids', 'imported', 'duplicates'}}
        values['checked'] = state['processed']
        values['processed'] = 0
        if values['phase'] == 'importing':
            values['phase'] = 'checking_duplicates'
        job._publish(**values)

    try:
        try:
            getattr(plan, 'import_' + job.kind)(job.source, job.category, progress=progress, cancel=job._cancel)
        except Exception as exc:
            job._publish(error=f"{type(exc).__name__}: {str(exc).split(' Imported:')[0]}")
        if not job._cancel.is_set():
            plan.prepare()
            if plan.rows:
                with job._lock:
                    job._rows = plan.rows
                    job._choices = {r['row_id']: r['default_action'] for r in plan.rows}
                    job._snapshot['phase'] = 'waiting_duplicates'
                job._decision.wait()
            ids = plan.commit(job)
        else:
            state = job.snapshot()
            job._publish(processed=state['failed'] + state['skipped'])
        state = job.snapshot()
        remaining = None if state['total'] is None else max(0, state['total'] - state['processed'])
        job._publish(phase='processing', current=None, cancelled=job._cancel.is_set(), unprocessed=remaining)
        if not ids:
            return
        job._publish(**_processing_counts(job.dataset, ids))
        if not job.dataset.auto_process:
            if job.snapshot()['awaiting']:
                job._publish(error=job.snapshot()['error'] or 'Automatic processing is disabled for this dataset.')
            return
        handle = job.dataset.schedule_processing()
        while True:
            counts = _processing_counts(job.dataset, ids)
            job._publish(**counts)
            if counts['awaiting'] == 0:
                break
            if handle is None or not handle.is_alive():
                job._publish(error=job.snapshot()['error'] or
                             'Background processing stopped with photos still awaiting results. '
                             'Retry the import to resume automatic processing; saved photos are retained.')
                break
            time.sleep(.2)
    except Exception as exc:
        message = f"Processing status unavailable: {type(exc).__name__}: {exc}"
        previous = job.snapshot()['error']
        job._publish(error=(previous + '\n' if previous else '') + message)
    finally:
        try:
            plan.close()
        except OSError as exc:
            previous = job.snapshot()['error']
            job._publish(error=(previous + '\n' if previous else '') + f'Temporary import cleanup failed: {exc}')
        finally:
            job._publish(phase='done', active=False)
