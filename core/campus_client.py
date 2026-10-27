"""Scoped HTTPS incremental client. No provider OAuth or legacy writer identity.

Construction is explicit; ordinary desktop startup does not enroll or adopt data.
Credentials come from a caller-controlled secure store, never from command args.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from core.incremental_sync import SyncWorker, apply_page


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CampusClient:
    def __init__(self, origin, credentials, *, opener=None):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/') or parsed.port not in (None,443)):
            raise ValueError('A direct HTTPS origin is required')
        self.origin = origin.rstrip('/')
        self.credentials = credentials
        self.opener = opener or build_opener(_NoRedirect())

    def _call(self, path, data=None):
        credentials = self.credentials()
        device_token, session = credentials['device_token'], credentials['actor_session']
        if not all(isinstance(v,str) and 32 <= len(v) <= 256 and '\n' not in v and '\r' not in v for v in (device_token,session)):
            raise PermissionError('Application credentials required')
        encoded = None if data is None else json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        if encoded and len(encoded)>65536: raise ValueError('Operation batch exceeds limit')
        request = Request(self.origin+path,data=encoded,headers={
            'Authorization':'Bearer '+device_token,'X-CBVMS-Actor-Session':session,
            'Content-Type':'application/json','Accept':'application/json'},method='GET' if data is None else 'POST')
        try:
            with self.opener.open(request,timeout=15) as response:
                if response.status!=200: raise OSError('Unacknowledged application response')
                raw=response.read(65537)
                if len(raw)>65536: raise ValueError('Response exceeds limit')
        except HTTPError as exc:
            if exc.code in (401,403): raise PermissionError('Application authentication or permission required') from None
            raise OSError('Application request was not acknowledged') from None
        result=json.loads(raw)
        if not isinstance(result,dict): raise ValueError('Invalid application response')
        return result

    def __call__(self, batch):
        if not 1<=len(batch)<=10: raise ValueError('Batch must contain 1–10 operations')
        keys=('operation_id','device_id','entity_type','entity_id','expected_version','operation_type','payload_digest')
        operations=[{**{k:r[k] for k in keys},'payload':json.loads(r['payload'])} for r in batch]
        result=self._call('/api/campus/operations',{'operations':operations})
        if set(result)!={'receipts'}: raise ValueError('Invalid receipt envelope')
        return result['receipts']

    def changes(self, cursor):
        if type(cursor) is not int or cursor<0: raise ValueError('Invalid cursor')
        result=self._call('/api/campus/changes?after='+str(cursor))
        if set(result)!={'after_cursor','next_cursor','events'} or result['after_cursor']!=cursor:
            raise ValueError('Invalid change page')
        return result


class CampusCycle:
    """Single bounded push/pull cycle; call/start only after explicit enrollment.

    Callback must apply the supported business projection in the supplied SQLite
    transaction, retaining pending local edits. Exceptions never reach Tk directly.
    """
    def __init__(self, path, client, apply_callback, *, status_callback=None):
        self.path=path; self.client=client; self.apply_callback=apply_callback
        self.status_callback=status_callback or (lambda state,detail:None)
        self.upload=SyncWorker(path,client,batch_size=10,status_callback=self.status_callback)
        self._lock=threading.Lock(); self._stop=threading.Event(); self._thread=None

    def run_once(self):
        if not self._lock.acquire(False): return False
        try:
            self.upload.run_once()
            with sqlite3.connect(self.path,timeout=.25) as conn:
                cursor=conn.execute('SELECT cursor FROM incremental_cursor WHERE singleton=1').fetchone()[0]
            page=self.client.changes(cursor)  # no database transaction during I/O
            with sqlite3.connect(self.path,timeout=.25) as conn:
                conn.row_factory=sqlite3.Row; conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
                apply_page(conn,**page,apply_callback=self.apply_callback)
            self.status_callback('cycle_completed','')
            return True
        except PermissionError:
            self.status_callback('auth_required','');return False
        except Exception as exc:
            self.status_callback('offline',type(exc).__name__);return False
        finally:self._lock.release()

    def start(self,interval=15):
        if not 1<=interval<=3600:raise ValueError('Invalid poll interval')
        if self._thread and self._thread.is_alive():return
        self._stop.clear()
        def run():
            while not self._stop.is_set():
                self.run_once();self._stop.wait(interval)
        self._thread=threading.Thread(target=run,name='campus-incremental-sync',daemon=True);self._thread.start()

    def stop(self,timeout=1):
        self._stop.set()
        if self._thread:self._thread.join(timeout)
