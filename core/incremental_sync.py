"""Application protocol helpers for durable incremental campus sync.

The transport is injected by the host application. These functions never perform
network I/O while a SQLite transaction is open.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from database.incremental_sync import migrate

SUPPORTED = 'student.profile.patch'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _payload(operation_type, payload):
    if operation_type != SUPPORTED or not isinstance(payload, dict) or set(payload) - {'name', 'email'}:
        raise ValueError('Unsupported incremental operation or payload fields')
    if not payload or any(not isinstance(v, str) or len(v) > 254 for v in payload.values()):
        raise ValueError('Profile patch fields must be bounded strings')
    if 'name' in payload and not payload['name'].strip(): raise ValueError('Name cannot be blank')
    return _canonical(payload)


def enqueue(conn, *, device_id, entity_type, entity_id, expected_version, operation_type, payload, dependencies=(), operation_id=None):
    """Add an immutable operation using the caller's transaction (no commit)."""
    if not conn.in_transaction: raise ValueError('Enqueue requires an active caller transaction')
    if entity_type != 'student' or not all(isinstance(x, str) and x and len(x) <= 200 for x in (device_id, entity_type, entity_id)):
        raise ValueError('Device and entity identity are required')
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError('Expected version is required')
    body = _payload(operation_type, payload)
    deps = list(dependencies)
    if any(not isinstance(d, str) or not d for d in deps) or len(set(deps)) != len(deps):
        raise ValueError('Invalid dependencies')
    oid = str(uuid.UUID(operation_id)) if operation_id else str(uuid.uuid4())
    if conn.execute('SELECT applying FROM incremental_inbound_guard WHERE singleton=1').fetchone()[0]:
        raise ValueError('Inbound application cannot enqueue outbound operations')
    old = conn.execute('SELECT * FROM incremental_outbox WHERE operation_id=?', (oid,)).fetchone()
    digest = hashlib.sha256(body.encode()).hexdigest()
    if old:
        identity=(old['device_id'],old['entity_type'],old['entity_id'],old['expected_version'],old['operation_type'],old['payload_digest'],old['dependencies'])
        proposed=(device_id,entity_type,entity_id,expected_version,operation_type,digest,_canonical(deps))
        if identity != proposed: raise ValueError('Operation ID is already bound to different operation data')
        return oid
    if len(deps) > 32: raise ValueError('Too many operation dependencies')
    for dep in deps:
        dep_row=conn.execute('SELECT sequence,state FROM incremental_outbox WHERE operation_id=?',(dep,)).fetchone()
        if dep_row is None: raise ValueError('Dependencies must reference known earlier operations')
    conn.execute('INSERT OR IGNORE INTO incremental_device(singleton,device_id) VALUES(1,?)', (device_id,))
    current = conn.execute('SELECT device_id FROM incremental_device WHERE singleton=1').fetchone()[0]
    if current != device_id:
        raise ValueError('Device identity mismatch')
    conn.execute('INSERT OR IGNORE INTO incremental_entity_versions VALUES(?,?,?)', (entity_type, entity_id, expected_version))
    conn.execute('''INSERT INTO incremental_outbox(operation_id,device_id,entity_type,entity_id,expected_version,
      operation_type,payload,payload_digest,dependencies,state) VALUES(?,?,?,?,?,?,?,?,?,'pending')''',
      (oid,device_id,entity_type,entity_id,expected_version,operation_type,body,digest,_canonical(deps)))
    conn.executemany('INSERT INTO incremental_dependencies(operation_id,dependency_id) VALUES(?,?)',[(oid,d) for d in deps])
    return oid


def eligible_heads(conn, *, limit=25, now=None):
    if not 1 <= limit <= 25: raise ValueError('Invalid batch limit')
    now = time.time() if now is None else now
    return [dict(r) for r in conn.execute('''SELECT o.* FROM incremental_outbox o
      WHERE o.state='pending' AND o.retry_at<=?
      AND NOT EXISTS (SELECT 1 FROM incremental_outbox earlier WHERE earlier.entity_type=o.entity_type
        AND earlier.entity_id=o.entity_id AND earlier.sequence<o.sequence AND earlier.state<>'accepted')
      AND NOT EXISTS (SELECT 1 FROM incremental_dependencies edge JOIN incremental_outbox dep
        ON dep.operation_id=edge.dependency_id WHERE edge.operation_id=o.operation_id AND dep.state<>'accepted')
      ORDER BY o.sequence LIMIT ?''',(now,limit))]


def acknowledge(conn, receipt):
    row=conn.execute('SELECT * FROM incremental_outbox WHERE operation_id=?',(receipt.get('operation_id'),)).fetchone()
    if row is None: raise ValueError('Unknown operation receipt')
    checks=(receipt.get('device_id')==row['device_id'], receipt.get('entity_type')==row['entity_type'], receipt.get('entity_id')==row['entity_id'], receipt.get('operation_type')==row['operation_type'], receipt.get('expected_version')==row['expected_version'], receipt.get('payload_digest')==row['payload_digest'])
    if not all(checks): raise ValueError('Receipt does not match queued operation')
    status=receipt.get('status')
    if status not in ('accepted','conflict'): raise ValueError('Receipt status must be accepted or conflict')
    normalized=_canonical(receipt)
    if row['receipt'] is not None:
        if row['receipt'] != normalized: raise ValueError('Operation received a changed terminal receipt')
        return
    if row['state'] in ('accepted','conflict'): raise ValueError('Terminal operation lacks matching stored receipt')
    if status == 'conflict':
        detail = receipt.get('detail', 'Remote version conflict')
        if not isinstance(detail, str) or len(detail.encode('utf-8'))>16384: raise ValueError('Invalid conflict detail')
        conn.execute("UPDATE incremental_outbox SET state='conflict',last_error=?,receipt=? WHERE operation_id=?", (detail,normalized,row['operation_id']))
        conn.execute('INSERT INTO incremental_conflicts(operation_id,detail) VALUES(?,?) ON CONFLICT(operation_id) DO UPDATE SET detail=excluded.detail,recorded_at=CURRENT_TIMESTAMP', (row['operation_id'],detail))
        return
    version=receipt.get('version')
    if type(version) is not int or version != row['expected_version']+1: raise ValueError('Invalid accepted version')
    current=conn.execute('SELECT version FROM incremental_entity_versions WHERE entity_type=? AND entity_id=?',(row['entity_type'],row['entity_id'])).fetchone()
    conn.execute("UPDATE incremental_outbox SET state='accepted',last_error=NULL,receipt=? WHERE operation_id=?",(normalized,row['operation_id']))
    conn.execute('INSERT INTO incremental_entity_versions VALUES(?,?,?) ON CONFLICT(entity_type,entity_id) DO UPDATE SET version=excluded.version WHERE excluded.version>=incremental_entity_versions.version', (row['entity_type'],row['entity_id'],version))


def apply_inbound(conn, *, cursor, operation_id, payload, apply_callback):
    """Apply one remote event and advance cursor atomically; caller owns transaction.

    Callback must be idempotent for operation_id and must only mutate through conn.
    """
    if not conn.in_transaction: raise ValueError('Inbound application requires an active caller transaction')
    if type(cursor) is not int or cursor < 1 or not isinstance(operation_id,str) or not operation_id or len(operation_id)>200: raise ValueError('Invalid cursor or operation ID')
    body=_canonical(payload); digest=hashlib.sha256(body.encode()).hexdigest()
    if len(body.encode())>65536: raise ValueError('Inbound payload exceeds size limit')
    prev=conn.execute('SELECT cursor FROM incremental_cursor WHERE singleton=1').fetchone()[0]
    dup=conn.execute('SELECT cursor,payload_digest FROM incremental_inbox WHERE operation_id=?',(operation_id,)).fetchone()
    if dup:
        if dup['cursor'] != cursor or dup['payload_digest'] != digest: raise ValueError('Changed duplicate inbound operation')
        return False
    if cursor != prev+1: raise ValueError('Inbound cursor gap or regression')
    conn.execute('SAVEPOINT incremental_event')
    try:
        conn.execute('UPDATE incremental_inbound_guard SET applying=1 WHERE singleton=1')
        apply_callback(conn, operation_id, payload)
        conn.execute('INSERT INTO incremental_inbox(cursor,operation_id,payload_digest) VALUES(?,?,?)',(cursor,operation_id,digest))
        conn.execute('UPDATE incremental_cursor SET cursor=? WHERE singleton=1',(cursor,))
    except BaseException:
        conn.execute('ROLLBACK TO incremental_event'); conn.execute('RELEASE incremental_event'); raise
    else:
        conn.execute('UPDATE incremental_inbound_guard SET applying=0 WHERE singleton=1')
        conn.execute('RELEASE incremental_event')
    return True


def apply_page(conn, *, after_cursor, next_cursor, events, apply_callback):
    """Apply a globally sequenced, ACL-filtered page; filtered gaps are expected.

    The caller owns the surrounding transaction. Replaying the exact committed page
    is a no-op; a cursor cannot move across a different page body.
    """
    if not conn.in_transaction: raise ValueError('Inbound application requires an active caller transaction')
    if type(after_cursor) is not int or type(next_cursor) is not int or after_cursor < 0 or next_cursor < after_cursor:
        raise ValueError('Invalid page cursor bounds')
    if not isinstance(events,(list,tuple)) or len(events)>25: raise ValueError('Invalid page event batch')
    canonical=[]; previous=after_cursor
    for event in events:
        if not isinstance(event,dict) or set(event)!={'cursor','operation_id','payload'}: raise ValueError('Invalid inbound event envelope')
        c=event['cursor']; oid=event['operation_id']; payload=event['payload']
        if type(c) is not int or not after_cursor<c<=next_cursor or c<=previous: raise ValueError('Inbound event cursor order is invalid')
        if not isinstance(oid,str) or not oid or len(oid)>200 or not isinstance(payload,dict): raise ValueError('Invalid inbound operation')
        if len(_canonical(payload).encode())>65536: raise ValueError('Inbound payload exceeds size limit')
        previous=c; canonical.append({'cursor':c,'operation_id':oid,'payload':payload})
    page_body=_canonical({'after':after_cursor,'next':next_cursor,'events':canonical}).encode()
    if len(page_body)>65536: raise ValueError('Inbound page exceeds size limit')
    digest=hashlib.sha256(page_body).hexdigest()
    current=conn.execute('SELECT cursor FROM incremental_cursor WHERE singleton=1').fetchone()[0]
    prior=conn.execute('SELECT next_cursor,page_digest FROM incremental_pages WHERE after_cursor=?',(after_cursor,)).fetchone()
    if prior:
        if prior['next_cursor']!=next_cursor or prior['page_digest']!=digest: raise ValueError('Changed inbound page replay')
        if current < next_cursor: raise ValueError('Page receipt exists beyond saved cursor')
        return False
    if current!=after_cursor: raise ValueError('Page cursor does not match saved cursor')
    if not canonical and next_cursor==after_cursor:
        conn.execute('UPDATE incremental_sync_status SET last_download_at=? WHERE singleton=1',(time.time(),))
        return False
    conn.execute('SAVEPOINT incremental_page')
    try:
        conn.execute('UPDATE incremental_inbound_guard SET applying=1 WHERE singleton=1')
        for event in canonical:
            item_digest=hashlib.sha256(_canonical(event['payload']).encode()).hexdigest()
            old=conn.execute('SELECT cursor,payload_digest FROM incremental_inbox WHERE operation_id=?',(event['operation_id'],)).fetchone()
            if old:
                if old['cursor']!=event['cursor'] or old['payload_digest']!=item_digest: raise ValueError('Changed duplicate inbound operation')
            else:
                apply_callback(conn,event['operation_id'],event['payload'])
                conn.execute('INSERT INTO incremental_inbox(cursor,operation_id,payload_digest) VALUES(?,?,?)',(event['cursor'],event['operation_id'],item_digest))
        conn.execute('UPDATE incremental_cursor SET cursor=? WHERE singleton=1',(next_cursor,))
        conn.execute('INSERT INTO incremental_pages(after_cursor,next_cursor,page_digest) VALUES(?,?,?)',(after_cursor,next_cursor,digest))
        conn.execute('UPDATE incremental_sync_status SET downloaded_count=downloaded_count+?,last_download_at=? WHERE singleton=1',(len(canonical),time.time()))
    except BaseException:
        conn.execute('ROLLBACK TO incremental_page'); conn.execute('RELEASE incremental_page'); raise
    else:
        conn.execute('UPDATE incremental_inbound_guard SET applying=0 WHERE singleton=1')
        conn.execute('RELEASE incremental_page')
    return True


def retry_delay(retry_count, *, base=1.0, cap=300.0, random_value=None):
    if retry_count < 0: raise ValueError('Invalid retry count')
    raw=min(cap,base*(2**min(retry_count,30)))
    return min(cap, raw*(0.75+0.5*(random.random() if random_value is None else random_value)))


class SyncWorker:
    """Stoppable, single-flight worker using an injected transport callable."""
    def __init__(self, db_path, transport, *, batch_size=10, status_callback=None):
        if not 1<=batch_size<=10: raise ValueError('Invalid batch size')
        self.db_path=Path(db_path); self.transport=transport; self.batch_size=batch_size
        self.status_callback=status_callback or (lambda state, detail: None)
        self.stop_event=threading.Event(); self._thread=None; self._flight=threading.Lock(); self._initialized=False

    def _status(self,state,detail=''): self.status_callback(state,detail)

    def run_once(self):
        if not self._flight.acquire(blocking=False): return 0
        try: return self._run_once()
        finally: self._flight.release()

    def _run_once(self):
        self._ensure_initialized()
        conn=sqlite3.connect(self.db_path,timeout=0.25); conn.row_factory=sqlite3.Row
        try:
            batch=eligible_heads(conn,limit=self.batch_size)
        finally: conn.close()
        if not batch: return 0
        self._status('locally_saved')
        try: response=self.transport(batch)
        except PermissionError:
            self._mark_auth(batch); self._status('auth_required'); return 0
        except Exception as exc:
            self._retry(batch,type(exc).__name__); return 0
        if not isinstance(response,(list,tuple)):
            self._retry(batch,'InvalidResponse'); raise ValueError('Transport must return a receipt list')
        got=[r.get('operation_id') for r in response if isinstance(r,dict)]
        expected=[op['operation_id'] for op in batch]
        if len(got)!=len(response) or len(got)!=len(set(got)) or set(got)!=set(expected):
            self._retry(batch,'IncompleteOrUnrelatedReceipts')
            raise ValueError('Transport response must contain exactly one receipt per sent operation')
        conn=sqlite3.connect(self.db_path,timeout=0.25); conn.row_factory=sqlite3.Row
        try:
            conn.execute('BEGIN IMMEDIATE')
            for receipt in response:
                acknowledge(conn,receipt)
            accepted=sum(1 for r in response if r.get('status')=='accepted')
            conn.execute('UPDATE incremental_sync_status SET uploaded_count=uploaded_count+?,last_upload_at=?,auth_required=0 WHERE singleton=1',(accepted,time.time()))
            conn.commit()
        except Exception:
            conn.rollback(); conn.close(); self._retry(batch,'InvalidReceipt'); raise
        finally: conn.close()
        self._status('conflict' if accepted < len(response) else 'accepted'); return len(response)

    def _ensure_initialized(self):
        if self._initialized: return
        conn=sqlite3.connect(self.db_path,timeout=0.25)
        try:
            conn.execute('BEGIN IMMEDIATE')
            migrate(conn)
            conn.commit()
            self._initialized=True
        except BaseException:
            conn.rollback(); raise
        finally: conn.close()

    def _retry(self,batch,error):
        conn=sqlite3.connect(self.db_path,timeout=0.25)
        try:
            conn.execute('BEGIN IMMEDIATE')
            for op in batch:
                count=op['retry_count']+1
                conn.execute("UPDATE incremental_outbox SET retry_count=?,retry_at=?,last_error=? WHERE operation_id=? AND state='pending'",(count,time.time()+retry_delay(count),error[:100],op['operation_id']))
            conn.commit()
        finally: conn.close()

    def _mark_auth(self,batch):
        conn=sqlite3.connect(self.db_path,timeout=0.25)
        try:
            conn.executemany("UPDATE incremental_outbox SET state='auth_required' WHERE operation_id=?",[(x['operation_id'],) for x in batch]); conn.execute('UPDATE incremental_sync_status SET auth_required=1 WHERE singleton=1'); conn.commit()
        finally: conn.close()

    def resume_after_auth(self):
        """Explicitly requeue authentication-paused operations after credentials are repaired."""
        conn=sqlite3.connect(self.db_path,timeout=0.25)
        try:
            conn.execute("UPDATE incremental_outbox SET state='pending',retry_at=0 WHERE state='auth_required'")
            conn.execute('UPDATE incremental_sync_status SET auth_required=0 WHERE singleton=1'); conn.commit()
        finally: conn.close()

    def start(self, interval=5.0):
        if self._thread and self._thread.is_alive(): return
        self.stop_event.clear()
        def loop():
            while not self.stop_event.is_set():
                try: self.run_once()
                except Exception as exc: self._status('failed',type(exc).__name__)
                self.stop_event.wait(interval)
        self._thread=threading.Thread(target=loop,name='incremental-sync',daemon=True); self._thread.start()

    def stop(self, timeout=5.0):
        self.stop_event.set()
        if self._thread: self._thread.join(timeout)
