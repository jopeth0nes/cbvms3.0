"""Provider-owner academic projection updates preserve lifecycle and private data."""
import json
import sqlite3
import unittest
import uuid
from tests.test_student_lifecycle_cloud import LifecycleCloudTests
from core.student_lifecycle_cloud import deliver


class AcademicOwnerProjectionTests(LifecycleCloudTests):
    def update(self, **override):
        before=self.c.execute("SELECT snapshot FROM portal_students WHERE id='S1'").fetchone()[0]
        row=dict(operation_id=str(uuid.uuid4()),student_id='S1',authority_id='authority',expected_snapshot=before,
                 academic_terms=json.dumps([{'id':1,'start_date':'2026-06-01','end_date':'2026-12-31'}]),
                 current_term=json.dumps({'id':1}),enrollment_status='Enrolled in current term',
                 term_history=json.dumps({'enrollments':[{'confirmation_method':'administrative_exception'}]}),reason='Explicit owner approval')
        row.update(override)
        self.c.execute('INSERT INTO portal_academic_owner_updates('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        return row

    def test_restored_projection_works_preserving_unrelated_payloads(self):
        deliver(self.t,self.cmd(1,'recycle'));deliver(self.t,self.cmd(2,'restore'))
        row=self.update()
        after=json.loads(self.c.execute("SELECT snapshot FROM portal_students WHERE id='S1'").fetchone()[0])
        self.assertEqual(after['violations'],[{'id':-1}])
        self.assertEqual(after['enrollment_status'],'Enrolled in current term')
        self.assertEqual(self.c.execute("SELECT password_hash FROM portal_accounts WHERE student_id='S1'").fetchone()[0],'unchanged')
        self.assertEqual(self.c.execute("SELECT applying FROM portal_student_lifecycle WHERE student_id='S1'").fetchone()[0],0)
        self.assertEqual(self.c.execute("SELECT snapshot FROM portal_students WHERE id='WINDOWS'").fetchone()[0],'{"violations":[{"id":-1}]}')
        self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(),[])
        with self.assertRaises(sqlite3.IntegrityError): self.update(operation_id=row['operation_id'])

    def test_recycled_and_blocked_or_unauthorized_update_denied(self):
        for fields in ({'authority_id':'wrong'},{'student_id':'WINDOWS'},{'expected_snapshot':'stale'}):
            with self.assertRaises(sqlite3.IntegrityError): self.update(**fields)
        deliver(self.t,self.cmd(1,'recycle'))
        with self.assertRaises(sqlite3.IntegrityError): self.update()
