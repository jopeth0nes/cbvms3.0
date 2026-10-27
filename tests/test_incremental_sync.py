import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.incremental_sync import SyncWorker, acknowledge, apply_inbound, apply_page, eligible_heads, enqueue, migrate, retry_delay


class IncrementalSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'sync.db'
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('BEGIN IMMEDIATE'); migrate(self.conn); self.conn.commit()

    def tearDown(self):
        self.conn.close(); self.tmp.cleanup()

    def add(self, entity='s1', **kwargs):
        if not self.conn.in_transaction: self.conn.execute('BEGIN')
        return enqueue(self.conn, device_id='device-A', entity_type='student', entity_id=entity,
                       expected_version=0, operation_type='student.profile.patch', payload={'name': 'Ada'}, **kwargs)

    def inbound(self, **kwargs):
        if not self.conn.in_transaction: self.conn.execute('BEGIN')
        return apply_inbound(self.conn, **kwargs)

    def page(self, **kwargs):
        if not self.conn.in_transaction: self.conn.execute('BEGIN')
        return apply_page(self.conn, **kwargs)

    def test_enqueue_obeys_business_rollback_and_payload_allowlist(self):
        self.conn.execute('BEGIN')
        oid = self.add()
        self.conn.rollback()
        self.assertEqual(self.conn.execute('select count(*) from incremental_outbox').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            enqueue(self.conn, device_id='device-A', entity_type='student', entity_id='s1', expected_version=0,
                    operation_type='student.profile.patch', payload={'role':'admin'})
        self.conn.commit()

    def test_heads_allow_unrelated_progress_and_wait_for_dependencies(self):
        a=self.add('a'); b=self.add('b'); dependent=self.add('c',dependencies=[a])
        self.conn.commit()
        heads=eligible_heads(self.conn,limit=2,now=10**12)
        self.assertEqual({r['operation_id'] for r in heads},{a,b})
        self.assertNotIn(dependent,{r['operation_id'] for r in heads})
        row=self.conn.execute('select * from incremental_outbox where operation_id=?',(a,)).fetchone()
        acknowledge(self.conn,self.receipt(row))
        self.assertIn(dependent,{r['operation_id'] for r in eligible_heads(self.conn,now=10**12)})

    def test_ack_must_bind_all_receipt_fields(self):
        oid=self.add(); self.conn.commit(); row=self.conn.execute('select * from incremental_outbox').fetchone()
        with self.assertRaises(ValueError):
            acknowledge(self.conn,dict(self.receipt(row),device_id='wrong'))
        self.assertEqual(row['state'],'pending')

    @staticmethod
    def receipt(row, **changes):
        receipt=dict(operation_id=row['operation_id'],device_id=row['device_id'],entity_type=row['entity_type'],entity_id=row['entity_id'],
                     operation_type=row['operation_type'],expected_version=row['expected_version'],payload_digest=row['payload_digest'],status='accepted',version=row['expected_version']+1)
        receipt.update(changes); return receipt

    def test_same_entity_conflict_backoff_and_same_second_order(self):
        first=self.add('s'); second=self.add('s'); other=self.add('other')
        self.conn.execute("update incremental_outbox set state='conflict' where operation_id=?",(first,)); self.conn.commit()
        got={r['operation_id'] for r in eligible_heads(self.conn,now=10**12)}
        self.assertEqual(got,{other})
        self.conn.execute("update incremental_outbox set state='pending',retry_at=9999999999999 where operation_id=?",(first,)); self.conn.commit()
        got={r['operation_id'] for r in eligible_heads(self.conn,now=10**12)}
        self.assertEqual(got,{other})
        self.conn.execute("update incremental_outbox set retry_at=0 where operation_id=?",(first,)); self.conn.commit()
        got={r['operation_id'] for r in eligible_heads(self.conn,now=10**12)}
        self.assertIn(first,got); self.assertNotIn(second,got)

    def test_duplicate_uuid_must_match_complete_identity(self):
        oid=self.add(); row=self.conn.execute('select * from incremental_outbox where operation_id=?',(oid,)).fetchone()
        with self.assertRaises(ValueError):
            enqueue(self.conn,device_id='device-A',entity_type='student',entity_id='s1',expected_version=0,
                    operation_type='student.profile.patch',payload={'name':'Grace'},operation_id=oid)
        with self.assertRaises(ValueError):
            enqueue(self.conn,device_id='device-A',entity_type='student',entity_id='s1',expected_version=1,
                    operation_type='student.profile.patch',payload={'name':'Ada'},operation_id=oid)
        self.assertEqual(row['payload'],'{"name":"Ada"}')

    def test_inbound_duplicate_and_atomic_rollback(self):
        seen=[]
        def callback(conn, oid, payload):
            conn.execute('create table if not exists applied (id text primary key)')
            conn.execute('insert into applied values(?)',(oid,)); seen.append(oid)
        self.conn.execute('BEGIN')
        self.assertTrue(self.inbound(cursor=1,operation_id='remote-1',payload={'x':1},apply_callback=callback))
        self.conn.rollback()
        self.assertEqual(self.conn.execute('select cursor from incremental_cursor').fetchone()[0],0)
        self.conn.execute('BEGIN')
        self.inbound(cursor=1,operation_id='remote-1',payload={'x':1},apply_callback=callback)
        self.conn.commit()
        self.assertFalse(self.inbound(cursor=1,operation_id='remote-1',payload={'x':1},apply_callback=callback))
        with self.assertRaises(ValueError):
            self.inbound(cursor=1,operation_id='remote-1',payload={'x':2},apply_callback=callback)
        with self.assertRaises(ValueError):
            self.inbound(cursor=3,operation_id='remote-3',payload={},apply_callback=callback)

    def test_callback_failure_caught_by_caller_still_rolls_back_savepoint(self):
        def broken(conn, oid, payload):
            conn.execute('create table if not exists callback_writes (id text)')
            conn.execute('insert into callback_writes values(?)',(oid,))
            raise RuntimeError('failure')
        self.conn.execute('BEGIN')
        with self.assertRaises(RuntimeError): self.inbound(cursor=1,operation_id='x',payload={},apply_callback=broken)
        self.conn.commit()
        self.assertEqual(self.conn.execute("select name from sqlite_master where name='callback_writes'").fetchone(),None)

    def test_apply_page_accepts_global_cursor_gaps_and_replays_exactly(self):
        seen=[]
        callback=lambda conn, oid, payload: seen.append((oid,payload))
        events=[{'cursor':3,'operation_id':'a','payload':{'x':1}},{'cursor':8,'operation_id':'b','payload':{'x':2}}]
        self.assertTrue(self.page(after_cursor=0,next_cursor=10,events=events,apply_callback=callback)); self.conn.commit()
        self.assertFalse(self.page(after_cursor=0,next_cursor=10,events=events,apply_callback=callback))
        self.assertEqual(len(seen),2)
        with self.assertRaises(ValueError): self.page(after_cursor=0,next_cursor=10,events=events+[{'cursor':9,'operation_id':'c','payload':{}}],apply_callback=callback)

    def test_empty_idle_page_does_not_block_new_events_at_same_cursor(self):
        callback=lambda conn, oid, payload: None
        self.assertFalse(self.page(after_cursor=0,next_cursor=0,events=[],apply_callback=callback)); self.conn.commit()
        self.assertTrue(self.page(after_cursor=0,next_cursor=4,events=[{'cursor':4,'operation_id':'new','payload':{}}],apply_callback=callback))

    def test_legacy_dependency_json_is_validated_and_backfilled(self):
        old_path=Path(self.tmp.name)/'old.db'; old=sqlite3.connect(old_path)
        old.execute('''CREATE TABLE incremental_outbox (operation_id TEXT PRIMARY KEY,device_id TEXT,entity_type TEXT,entity_id TEXT,
          expected_version INTEGER,operation_type TEXT,payload TEXT,payload_digest TEXT,dependencies TEXT,state TEXT,
          retry_count INTEGER,retry_at REAL,last_error TEXT,created_at TEXT)''')
        old.execute("insert into incremental_outbox values('a','d','student','s',0,'student.profile.patch','{}','x','[]','pending',0,0,null,CURRENT_TIMESTAMP)")
        old.execute("insert into incremental_outbox values('b','d','student','t',0,'student.profile.patch','{}','y','[\"a\"]','pending',0,0,null,CURRENT_TIMESTAMP)")
        from database.incremental_sync import migrate as storage_migrate
        storage_migrate(old)
        self.assertEqual(old.execute('select dependency_id from incremental_dependencies').fetchone()[0],'a')
        old.rollback(); old.close()

    def test_inbound_requires_transaction_and_rejects_noncanonical_or_oversized_page(self):
        with self.assertRaises(ValueError): apply_page(self.conn,after_cursor=0,next_cursor=0,events=[],apply_callback=lambda *a:None)
        self.conn.execute('BEGIN')
        with self.assertRaises(ValueError): self.page(after_cursor=0,next_cursor=1,events=[{'cursor':1,'operation_id':'n','payload':{'x':float('nan')}}],apply_callback=lambda *a:None)
        with self.assertRaises(ValueError): self.page(after_cursor=0,next_cursor=25,events=[{'cursor':i+1,'operation_id':str(i),'payload':{}} for i in range(26)],apply_callback=lambda *a:None)
        self.conn.rollback()

    def test_late_lost_ack_after_newer_inbound_keeps_max_version(self):
        first=self.add('s'); self.conn.commit(); row=self.conn.execute('select * from incremental_outbox where operation_id=?',(first,)).fetchone()
        receipt=self.receipt(row); acknowledge(self.conn,receipt); acknowledge(self.conn,receipt)
        second=enqueue(self.conn,device_id='device-A',entity_type='student',entity_id='s',expected_version=0,
                       operation_type='student.profile.patch',payload={'email':'a@b.co'})
        row2=self.conn.execute('select * from incremental_outbox where operation_id=?',(second,)).fetchone()
        self.conn.execute("update incremental_entity_versions set version=4 where entity_type='student' and entity_id='s'")
        acknowledge(self.conn,self.receipt(row2))
        self.assertEqual(self.conn.execute('select state from incremental_outbox where operation_id=?',(second,)).fetchone()[0],'accepted')
        self.assertEqual(self.conn.execute("select version from incremental_entity_versions where entity_type='student' and entity_id='s'").fetchone()[0],4)

    def test_conflict_receipt_is_durable_and_terminal(self):
        oid=self.add('conflict'); self.conn.commit()
        row=self.conn.execute('select * from incremental_outbox where operation_id=?',(oid,)).fetchone()
        receipt=self.receipt(row,status='conflict',version=None,detail='remote version is 7')
        acknowledge(self.conn,receipt); self.conn.commit()
        self.assertEqual(self.conn.execute('select detail from incremental_conflicts where operation_id=?',(oid,)).fetchone()[0],receipt['detail'])
        acknowledge(self.conn,receipt)
        with self.assertRaises(ValueError): acknowledge(self.conn,dict(receipt,detail='changed'))

    def test_worker_rejects_unrelated_receipt_and_retries(self):
        self.add('target'); self.conn.commit()
        unrelated=lambda batch:[{'operation_id':'not-sent'}]
        with self.assertRaises(ValueError): SyncWorker(self.path,unrelated).run_once()
        row=self.conn.execute('select state,retry_count,last_error from incremental_outbox').fetchone()
        self.assertEqual(row['state'],'pending'); self.assertEqual(row['retry_count'],1)
        self.assertEqual(row['last_error'],'IncompleteOrUnrelatedReceipts')

    def test_worker_restart_lost_ack_retry_and_auth(self):
        oid=self.add(); self.conn.commit()
        attempts=[]
        def lost(batch):
            attempts.append(batch[0]['operation_id']); raise TimeoutError('lost response')
        committed=set()
        def server(batch):
            if batch[0]['operation_id'] not in committed:
                committed.add(batch[0]['operation_id']); raise TimeoutError('response lost after server commit')
            op=batch[0]
            return [dict(operation_id=op['operation_id'],device_id=op['device_id'],entity_type=op['entity_type'],entity_id=op['entity_id'],operation_type=op['operation_type'],expected_version=op['expected_version'],payload_digest=op['payload_digest'],status='accepted',version=1)]
        worker=SyncWorker(self.path,server)
        self.assertEqual(worker.run_once(),0)
        self.assertIn(oid,committed)
        row=self.conn.execute('select retry_count from incremental_outbox').fetchone()
        self.assertEqual(row[0],1)
        self.conn.execute('update incremental_outbox set retry_at=0'); self.conn.commit()
        self.assertEqual(SyncWorker(self.path,server).run_once(),1)
        self.assertEqual(self.conn.execute('select state from incremental_outbox').fetchone()[0],'accepted')
        oid2=self.add('second'); self.conn.commit()
        def forbidden(batch): raise PermissionError()
        SyncWorker(self.path,forbidden).run_once()
        row=self.conn.execute('select state from incremental_outbox where operation_id=?',(oid2,)).fetchone()
        self.assertEqual(row[0],'auth_required')
        worker=SyncWorker(self.path,server); worker.resume_after_auth()
        self.assertEqual(self.conn.execute('select state from incremental_outbox where operation_id=?',(oid2,)).fetchone()[0],'pending')

    def test_bounded_batch_and_backoff(self):
        for i in range(4): self.add(str(i))
        self.conn.commit()
        self.assertEqual(len(eligible_heads(self.conn,limit=2,now=10**12)),2)
        oid=self.conn.execute('select operation_id from incremental_outbox limit 1').fetchone()[0]
        worker=SyncWorker(self.path,lambda batch: [],batch_size=2)
        with self.assertRaises(ValueError): worker.run_once()
        self.assertEqual(self.conn.execute('select state from incremental_outbox where operation_id=?',(oid,)).fetchone()[0],'pending')
        self.assertLessEqual(retry_delay(30,base=1,cap=20,random_value=1),20)


if __name__ == '__main__': unittest.main()
