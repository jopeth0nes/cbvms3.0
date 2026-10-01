"""Real SQLite and bounded-worker regressions for page-specific portal loading."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from core.portal_state import PAGE_SIZE, PortalRequests, page_snapshot
from database.db_manager import CBVMSDatabase
from tests.evidence_fixture import picture_evidence


class PortalRequestTests(unittest.TestCase):
    SID = '2023-00883'
    OTHER = '0000-00002'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'portal.db')
        self.db.initialize()
        for sid in (self.SID, self.OTHER):
            self.db.insert_student(sid, 'Test student', 'BSIT', '3A', b'', b'')
        self.worker = PortalRequests(self.db, self.SID)

    def tearDown(self):
        self.worker.close()
        self.assertTrue(self.worker.done.wait(3))
        self.tmp.cleanup()

    def result(self):
        return self.worker.results.get(timeout=3)

    def test_notifications_are_page_scoped_and_paginated(self):
        for i in range(PAGE_SIZE + 3):
            self.db.insert_notification(self.SID, f'Notice {i}', 'Test')
        self.db.insert_notification(self.OTHER, 'Private', 'Other owner')
        with patch.object(self.db, 'get_visible_violations_for_student', side_effect=AssertionError('unrelated query')), \
             patch.object(self.db, 'get_appeals_for_student', side_effect=AssertionError('unrelated query')):
            page = page_snapshot(self.db, self.SID, 'notifications')
            next_page = page_snapshot(self.db, self.SID, 'notifications', offset=PAGE_SIZE)
        self.assertEqual(len(page['_notifications']), PAGE_SIZE)
        self.assertEqual(len(next_page['_notifications']), 3)
        self.assertTrue(page['_has_more'])
        self.assertFalse(next_page['_has_more'])
        self.assertEqual(page['_unread_count'], PAGE_SIZE + 3)
        self.assertTrue(all(n['student_id'] == self.SID for n in page['_notifications']))

    def test_no_due_deadlines_need_no_writer_lock(self):
        lock = self.db.connect()
        lock.execute('BEGIN IMMEDIATE')
        try:
            request = self.worker.request('notifications', 1, lambda db, sid: page_snapshot(db, sid, 'notifications'))
            _, data, error = self.result()
            self.assertIsNone(error)
            self.assertEqual(data['_notifications'], [])
            self.assertEqual(request.state, 'loaded')
        finally:
            lock.rollback()
            lock.close()

    def test_notification_appeals_use_owner_scoped_metadata_and_refresh_decisions(self):
        own = self.db.log_violation(self.SID, 'Own', 'wrong_uniform', snapshot_jpeg=b'private image')
        other = self.db.log_violation(self.OTHER, 'Other', 'wrong_uniform')
        self.db.confirm_violation(own)
        self.db.confirm_violation(other)
        self.db.insert_notification(self.SID, 'Invalid link', 'Must not expose other student', violation_id=other)
        data = page_snapshot(self.db, self.SID, 'notifications')
        self.assertEqual(set(data['_notification_violations']), {own})
        self.assertNotIn('snapshot', data['_notification_violations'][own])
        self.assertTrue(data['_appeal_eligibility'][own]['eligible'])
        # An open dialog still receives current eligibility after page navigation.
        aid = self.db.insert_appeal(own, self.SID, 'Please review this picture and explanation.',
                                    evidence=picture_evidence())
        self.assertIsNotNone(aid)
        self.db.update_appeal_decision(aid, 'approved', 'Verified')
        data = page_snapshot(self.db, self.SID, 'settings', appeal_ids=(own,))
        self.assertFalse(data['_appeal_eligibility'][own]['eligible'])
        data = page_snapshot(self.db, self.SID, 'notifications')
        self.assertEqual(data['_notification_violations'][own]['appeal_status'], 'approved')

    def test_lock_failure_is_bounded_and_retry_works(self):
        lock = self.db.connect()
        lock.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        try:
            self.worker.request('notifications', 1, lambda db, sid: page_snapshot(db, sid, 'notifications'))
            request, _, error = self.result()
            self.assertEqual(request.state, 'failed')
            self.assertIn('locked', error)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            lock.rollback()
            lock.close()
        self.worker.request('notifications', 2, lambda db, sid: page_snapshot(db, sid, 'notifications'))
        self.assertIsNone(self.result()[2])

    def test_latest_read_replaces_obsolete_work_without_extra_workers(self):
        entered, release = threading.Event(), threading.Event()
        def slow(db, sid):
            entered.set()
            release.wait(2)
            return 'old'
        first = self.worker.request('violations', 1, slow)
        self.assertTrue(entered.wait(1))
        second = self.worker.request('profile', 2, lambda db, sid: 'never')
        third = self.worker.request('notifications', 3, lambda db, sid: 'latest')
        release.set()
        self.assertIs(self.result()[0], first)
        self.assertEqual(self.result()[1], 'latest')
        self.assertEqual(first.state, 'cancelled')
        self.assertEqual(second.state, 'cancelled')
        self.assertEqual(third.state, 'loaded')

    def test_queued_action_cannot_be_replaced_or_duplicated(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        first = self.worker.request('profile', 1, lambda db, sid: (entered.set(), release.wait(2)))
        self.assertTrue(entered.wait(1))
        action = self.worker.request('profile', 1, lambda db, sid: calls.append(sid), action=True)
        self.assertIsNone(self.worker.request('notifications', 2, lambda db, sid: None))
        self.assertIsNone(self.worker.request('profile', 1, lambda db, sid: None, action=True))
        release.set()
        self.result()
        self.assertIs(self.result()[0], action)
        self.assertEqual(calls, [self.SID])
        self.assertEqual(first.state, 'cancelled')

    def test_close_cancels_work_and_rejects_late_delivery(self):
        entered, release = threading.Event(), threading.Event()
        request = self.worker.request('profile', 1, lambda db, sid: (entered.set(), release.wait(2)))
        self.assertTrue(entered.wait(1))
        self.worker.close()
        release.set()
        self.assertTrue(self.worker.done.wait(1))
        self.assertEqual(request.state, 'cancelled')
        self.assertIsNone(self.worker.poll(self.SID))
        self.assertIsNone(self.worker.request('profile', 2, lambda db, sid: None))

    def test_worker_timeout_stops_sql_and_does_not_create_a_second_worker(self):
        def expensive(db, sid):
            with db.connect() as conn:
                return conn.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n').fetchone()
        request = self.worker.request('profile', 1, expensive)
        request.timeout = .05
        _, _, error = self.result()
        self.assertTrue(error)
        self.assertEqual(request.state, 'failed')
        self.assertTrue(self.worker.thread.is_alive())

    def test_evidence_excluded_and_foreign_record_focus_rejected(self):
        own = self.db.log_violation(self.SID, 'Test', 'wrong_uniform', snapshot_jpeg=b'large-image')
        other = self.db.log_violation(self.OTHER, 'Test', 'wrong_uniform')
        page = page_snapshot(self.db, self.SID, 'violations')
        self.assertEqual(page['_violations'][0]['id'], own)
        self.assertTrue(page['_violations'][0]['has_snapshot'])
        self.assertNotIn('snapshot', page['_violations'][0])
        self.assertEqual(page_snapshot(self.db, self.SID, 'violations', violation_id=other)['_violations'], [])

    def test_notification_reads_and_workflow_remain_owner_scoped_and_idempotent(self):
        now = datetime.now(timezone.utc)
        pending = self.db.log_violation(self.SID, 'Test', 'wrong_uniform', detected_at=now)
        page = page_snapshot(self.db, self.SID, 'violations')
        self.assertEqual(page['_violation_counts']['pending'], 1)
        self.assertFalse(page['_violations'][0]['can_appeal'])
        self.assertEqual(sum(r['active_count'] for r in page['_strike_summary']), 0)
        self.db.confirm_violation(pending, confirmed_at=now)
        page = page_snapshot(self.db, self.SID, 'violations')
        self.assertTrue(page['_violations'][0]['can_appeal'])
        self.assertEqual(sum(r['active_count'] for r in page['_strike_summary']), 1)
        own = self.db.get_notifications_for_student(self.SID)[0]['id']
        other = self.db.insert_notification(self.OTHER, 'Private', 'Private')
        self.assertFalse(self.db.mark_notification_read(other, student_id=self.SID))
        self.db.mark_notification_read(own, student_id=self.SID)
        self.db.mark_all_notifications_read(self.SID)
        self.assertEqual(self.db.get_unread_notification_count(self.SID), 0)
        self.assertEqual(self.db.get_unread_notification_count(self.OTHER), 1)
        before = len(self.db.get_notifications_for_student(self.SID))
        for _ in range(3):
            for route in ('dashboard', 'notifications', 'violations', 'appeals'):
                page_snapshot(self.db, self.SID, route)
        self.assertEqual(len(self.db.get_notifications_for_student(self.SID)), before)
        self.assertEqual(sum(r['active_count'] for r in self.db.get_strike_summary(self.SID)), 1)
        self.assertEqual(page_snapshot(self.db, self.OTHER, 'violations')['_violations'], [])


if __name__ == '__main__':
    unittest.main()
