"""One bounded in-flight operation per owner, delivered only to its live view.

No Tk calls from workers. Slow writes cannot be retried while still in flight.
SQLite connections retain the database's bounded busy timeout.
"""
import queue
import threading
import time


class BackgroundTask:
    def __init__(self, owner):
        self.owner = owner
        self.busy = False
        self.generation = 0
        owner.bind('<Destroy>', self._destroy, add='+')

    def _destroy(self, event):
        if event.widget is self.owner:
            self.generation += 1

    def run(self, operation, done, error):
        if self.busy:
            error('An operation is still running. Please wait before retrying.')
            return False
        self.busy = True
        generation = self.generation
        result = queue.Queue(maxsize=1)
        started = time.monotonic()
        warned = False
        def work():
            try:
                result.put((operation(), None))
            except Exception as exc:
                result.put((None, str(exc)))
        def poll():
            nonlocal warned
            if generation != self.generation or not self.owner.winfo_exists():
                return
            try:
                value, failure = result.get_nowait()
            except queue.Empty:
                if not warned and time.monotonic() - started > 10:
                    warned = True
                    error('Still working. Please wait; retry is available when this operation finishes.')
                self.owner.after(40, poll)
                return
            self.busy = False
            if failure:
                error(f'{failure} You can retry.')
            else:
                done(value)
        try:
            threading.Thread(target=work, daemon=True, name='academic-data').start()
        except Exception as exc:
            self.busy = False
            error(str(exc))
            return False
        self.owner.after(40, poll)
        return True
