"""One bounded read worker with latest-only requests/results, independent of Tk."""
import queue
import threading


def latest(queue_, value):
    try:
        queue_.get_nowait()
    except queue.Empty:
        pass
    try:
        queue_.put_nowait(value)
    except queue.Full:
        pass


class ReportWorker:
    def __init__(self):
        self.requests = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run,daemon=True,name='suspension-report-read')
        self.thread.start()

    def offer(self, generation, operation):
        if not self.closed.is_set():
            latest(self.requests,(generation,operation))

    def _run(self):
        while not self.closed.is_set():
            job = self.requests.get()
            if job is None:
                break
            generation, operation = job
            try:
                value, error = operation(), None
            except Exception as exc:
                value, error = None, str(exc)
            if not self.closed.is_set():
                latest(self.results,(generation,value,error))

    def close(self):
        self.closed.set()
        latest(self.requests,None)
