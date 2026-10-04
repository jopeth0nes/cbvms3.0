from tests.auth_fixture import portal_session
"""Native sidebar navigation and workflow tests using isolated SQLite records."""
from datetime import datetime, timezone
from pathlib import Path
import gc
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import customtkinter as ctk
from PIL import Image
from database.db_manager import CBVMSDatabase
from core.portal_state import PAGE_SIZE
from ui.student_portal import StudentPortal, _NAV_ITEMS
from tests.evidence_fixture import picture_evidence


class PortalNativeTests(unittest.TestCase):
    SID, OTHER = '2023-00883', '0000-00002'
    metrics = []

    def test_dark_mode_updates_canvas_and_survives_navigation(self):
        from ui.student_portal import SP_BG
        from ui.portal_scroll import PortalScrollFrame
        self.app._toggle_dark(True)
        self.app._show('settings')
        self.wait_page()
        self.assertEqual(ctk.get_appearance_mode(), 'Dark')
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():
                yield from descendants(child)
        hosts = [w for w in descendants(self.app) if isinstance(w, PortalScrollFrame)]
        self.assertTrue(hosts)
        self.assertEqual(hosts[0]._parent_canvas.cget('bg'), SP_BG[1])
        self.assertTrue(self.db.get_portal_preferences(self.SID)['dark_mode'])
        self.app._toggle_dark(False)
        self.assertEqual(hosts[0]._parent_canvas.cget('bg'), SP_BG[0])

    def setUp(self):
        gc.collect()  # Destroy old-root fonts on Tk's thread before starting workers.
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'portal.db')
        self.db.initialize()
        for sid in (self.SID, self.OTHER):
            self.db.insert_student(sid, 'Same display name', 'BSIT', '3A', b'', b'')
        self.app = StudentPortal(student_id=self.SID, display_name='Portal test', database=self.db, session_token=portal_session(self.db, self.SID))
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.wait_page()

    def tearDown(self):
        app = self.app
        self.metrics.extend(app._navigation_metrics)
        if not app._closed:
            app._on_close()
        self.assertTrue(app._refresh.done.wait(3), 'Portal worker did not exit')
        self.assertEqual(self.errors, [])
        self.app = None
        del app
        gc.collect()
        self.tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        print('PORTAL_METRICS=' + json.dumps(cls.metrics), flush=True)

    def pump(self, seconds=.03):
        self.app.after(int(seconds * 1000), self.app.quit)
        self.app.mainloop()

    def until(self, predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.pump()
        self.assertTrue(predicate(), f'Portal did not update: {self.app._page_state} / {self.app._page_status.cget("text")}')

    def wait_page(self):
        self.until(lambda: self.app._request is None and self.app._page_state in {'loaded', 'empty', 'failed'})
        self.assertNotEqual(self.app._page_state, 'failed', self.app._page_status.cget('text'))
        self.assertTrue(self.app._page_scroll.winfo_exists())
        self.until(lambda: self.app._page_scroll._parent_canvas.winfo_height() > 10)
        self.pump(.1)

    def click(self, route):
        self.app._nav_btns[route].invoke()
        self.assertEqual(self.app._active, route)
        self.assertTrue(self.app._page_status.winfo_exists())
        self.wait_page()

    def widgets(self, widget=None):
        widget = widget or self.app._page_body
        yield widget
        for child in widget.winfo_children():
            yield from self.widgets(child)

    def button(self, text, widget=None):
        return next(w for w in self.widgets(widget) if isinstance(w, ctk.CTkButton) and text in str(w.cget('text')))

    def texts(self):
        return '\n'.join(str(w.cget('text')) for w in self.widgets() if isinstance(w, (ctk.CTkLabel, ctk.CTkButton)))

    def refresh(self):
        self.app._reload_workflow_data()
        self.wait_page()

    def detect(self, sid=None, snapshot=None):
        return self.db.log_violation(sid or self.SID, 'Test', 'wrong_uniform', snapshot_jpeg=snapshot)

    def test_pending_confirmation_appeal_dismissal_and_isolation(self):
        app = self.app
        self.click('violations')
        app._on_violation_filter('Pending')
        self.wait_page()
        own, other = self.detect(), self.detect(self.OTHER)
        self.until(lambda: [v['id'] for v in app._violations] == [own], seconds=6)
        self.assertIn('Appeal available — no strike', self.texts())
        self.assertNotIn('Appeal Period Expired', self.texts())
        self.assertIn('Submit Appeal', self.texts())
        self.assertEqual(sum(r['active_count'] for r in app._strike_summary), 0)
        self.db.confirm_violation(own)
        self.refresh()
        self.assertEqual(app._violations, [])  # SQL applies the selected Pending filter.
        self.assertEqual(app._violation_filter, 'Pending')
        app._on_violation_filter('Confirmed')
        self.wait_page()
        self.assertEqual([v['id'] for v in app._violations], [own])
        self.assertTrue(app._violations[0]['can_appeal'])
        self.assertIn('Submit Appeal', self.texts())
        self.assertEqual(sum(r['active_count'] for r in app._strike_summary), 0)
        self.button('Submit Appeal').invoke()
        modal = next(w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel))
        box = next(w for w in self.widgets(modal) if isinstance(w, ctk.CTkTextbox))
        box.insert('1.0', 'Please review the evidence because this detection was incorrect.')
        picture = Path(self.tmp.name) / 'proof.png'
        picture.write_bytes(picture_evidence()[2])
        with patch('ui.student_portal.filedialog.askopenfilename', return_value=str(picture)):
            self.button('Browse', modal).invoke()
        with patch('ui.student_portal.analyze_appeal'):
            self.button('Submit Appeal', modal).invoke()
            self.until(lambda: app._active == 'appeals' and app._action_request is None)
            self.wait_page()
        appeal = self.db.get_appeals_for_student(self.SID)[0]
        self.assertEqual(appeal['violation_id'], own)
        self.db.update_appeal_decision(appeal['id'], 'approved', 'Verified test evidence', decided_by='admin', decision_category_code='approval.detection_error')
        self.click('appeals');self.wait_page()
        self.assertIn('Decision category: Uniform compliant / detection error',self.texts())
        self.assertIn('Verified test evidence',self.texts())
        pending = self.detect()
        self.db.dismiss_violation(pending, decided_by='test', reason='False positive')
        self.click('violations')
        app._on_violation_filter('Resolved')
        self.wait_page()
        self.assertEqual({v['id'] for v in app._violations}, {own, pending})
        self.assertNotIn(other, [v['id'] for v in app._violations])
        self.assertIn('Appeal Approved — No active strike', self.texts())
        self.assertIn('Resolved — detection dismissed', self.texts())
        self.assertEqual(sum(r['active_count'] for r in app._strike_summary), 0)
        self.assertEqual(self.db.get_appeals_for_student(self.OTHER), [])

    def test_sidebar_cycles_large_data_pagination_scroll_and_cleanup(self):
        for i in range(45):
            self.db.insert_notification(self.SID, f'Notice {i}', 'Notification content')
            self.detect()
        self.click('notifications')
        self.assertEqual(len(self.app._notifications), PAGE_SIZE)
        first = {n['id'] for n in self.app._notifications}
        self.button('Next').invoke()
        self.wait_page()
        self.assertFalse(first & {n['id'] for n in self.app._notifications})
        canvas = self.app._page_scroll._parent_canvas
        canvas.yview_moveto(.4)
        self.pump()
        old, position = self.app._page_scroll, canvas.yview()
        self.refresh()
        self.assertIs(self.app._page_scroll, old)
        self.assertEqual(canvas.yview(), position)
        self.click('dashboard')
        self.pump(.2)
        callbacks_before = len(self.app.tk.call('after', 'info'))
        fds_before = len(os.listdir('/dev/fd'))
        bindings_before = self.app.bind('<MouseWheel>').count('if {')
        for _ in range(3):
            for route, _ in _NAV_ITEMS:
                self.click(route)
            self.click('dashboard')
        self.pump(.2)
        self.assertLessEqual(len(self.app.tk.call('after', 'info')), callbacks_before + 2)
        self.assertLessEqual(len(os.listdir('/dev/fd')), fds_before + 2)
        self.assertEqual(self.app.bind('<MouseWheel>').count('if {'), bindings_before)
        self.assertEqual(sum(t.name == 'student-portal-read' for t in threading.enumerate()), 1)
        self.app._logout_button.invoke()
        self.assertTrue(self.app.logged_out)
        self.assertEqual(self.app._page_state, 'cancelled')

    def test_notifications_badge_mark_read_related_record_and_relogin(self):
        own = self.detect()
        self.db.confirm_violation(own)
        foreign = self.db.insert_notification(self.OTHER, 'Private', 'Other student')
        self.db.insert_notification(self.SID, 'General', 'General notice')
        self.click('notifications')
        self.assertEqual(self.app._unread_count, 2)
        self.assertIn('(2)', self.app._nav_btns['notifications'].cget('text'))
        self.button('Mark read').invoke()
        self.until(lambda: self.app._action_request is None)
        self.wait_page()
        self.assertEqual(self.app._unread_count, 1)
        self.button('Mark all as read').invoke()
        self.until(lambda: self.app._action_request is None)
        self.wait_page()
        self.assertEqual(self.app._unread_count, 0)
        self.button('View related record').invoke()
        self.wait_page()
        self.assertEqual([v['id'] for v in self.app._violations], [own])
        counts = (len(self.db.get_notifications_for_student(self.SID)), self.db.get_strike_count(self.SID, 'wrong_uniform'))
        self.app._logout_button.invoke()
        self.assertTrue(self.app._refresh.done.wait(2))
        gc.collect()
        self.app = StudentPortal(student_id=self.SID, display_name='Relogin test', database=self.db, session_token=portal_session(self.db, self.SID))
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.wait_page()
        self.click('notifications')
        self.assertEqual(self.app._unread_count, 0)
        self.assertEqual((len(self.db.get_notifications_for_student(self.SID)), self.db.get_strike_count(self.SID, 'wrong_uniform')), counts)
        self.assertEqual(self.db.get_unread_notification_count(self.OTHER), 1)

    def test_loading_navigation_action_navigation_and_logout(self):
        app = self.app
        entered, release = threading.Event(), threading.Event()
        from core.portal_state import page_snapshot
        def slow(db, sid, page, **kwargs):
            if page == 'notifications':
                entered.set()
                release.wait(2)
            return page_snapshot(db, sid, page, **kwargs)
        with patch('ui.student_portal.page_snapshot', side_effect=slow):
            app._nav_btns['notifications'].invoke()
            self.until(entered.is_set)
            for key in ('violations', 'appeals', 'profile'):
                app._nav_btns[key].invoke()
            release.set()
            self.wait_page()
        self.assertEqual(app._active, 'profile')
        entered.clear(); release.clear()
        def save(db, sid):
            entered.set()
            release.wait(2)
            return db.insert_notification(sid, 'Saved', 'Result')
        callback = []
        app._run_action(save, callback.append)
        self.until(entered.is_set)
        app._nav_btns['notifications'].invoke()
        release.set()
        self.wait_page()
        self.assertEqual(callback, [])
        self.assertEqual(app._notifications[0]['title'], 'Saved')
        entered.clear(); release.clear()
        app._run_action(lambda db, sid: (entered.set(), release.wait(2)), callback.append)
        self.until(entered.is_set)
        request = app._action_request
        app._logout_button.invoke()
        release.set()
        self.assertTrue(app._refresh.done.wait(2))
        self.assertEqual(request.state, 'cancelled')
        self.assertEqual(callback, [])

    def test_query_lock_and_render_failure_retry_keep_content(self):
        app = self.app
        self.db.insert_notification(self.SID, 'Visible', 'Keep this content')
        self.click('notifications')
        old = app._page_scroll
        with patch('core.portal_state.PortalWorkerDatabase.get_notifications_for_student', side_effect=sqlite3.OperationalError('test query failure')):
            app._reload_workflow_data()
            self.until(lambda: app._page_state == 'failed')
        self.assertIs(app._page_scroll, old)
        self.assertIn('test query failure', app._page_status.cget('text'))
        app._retry_button.invoke()
        self.wait_page()
        lock = self.db.connect()
        lock.execute('BEGIN EXCLUSIVE')
        try:
            app._nav_btns['violations'].invoke()
            self.until(lambda: app._page_state == 'failed')
            self.assertIn('locked', app._page_status.cget('text'))
            app._nav_btns['settings'].invoke()
            self.assertEqual(app._page_state, 'loaded')
        finally:
            lock.rollback(); lock.close()
        self.click('notifications')
        with patch.object(app, '_panel_notifications', side_effect=ValueError('test render failure')):
            self.db.insert_notification(self.SID, 'Changed', 'New content')
            app._reload_workflow_data()
            self.until(lambda: app._page_state == 'failed')
        app._retry_button.invoke()
        self.wait_page()
        self.assertEqual(len(app._notifications), 2)
        self.assertIn('Changed', self.texts())

    def test_report_draft_survives_refresh_and_submit_is_async(self):
        self.click('report')
        title = next(w for w in self.widgets() if isinstance(w, ctk.CTkEntry))
        body = next(w for w in self.widgets() if isinstance(w, ctk.CTkTextbox))
        title.insert(0, 'Native portal test report')
        body.insert('1.0', 'This report belongs only to an isolated test database.')
        self.db.insert_notification(self.SID, 'New notice', 'Badge should update')
        self.refresh()
        self.assertEqual(title.get(), 'Native portal test report')
        self.assertTrue(body.get('1.0', 'end').strip())
        self.button('Send Report to Admin').invoke()
        self.until(lambda: self.app._action_request is None)
        self.assertEqual(title.get(), '')
        with self.db.connect() as conn:
            row = conn.execute('SELECT reporter_id, title FROM system_reports').fetchone()
        self.assertEqual(tuple(row), (self.SID, 'Native portal test report'))

    def test_timeout_keeps_worker_owned_and_late_save_recovers(self):
        app = self.app
        entered, release = threading.Event(), threading.Event()
        from core.portal_state import page_snapshot
        def slow(db, sid, page, **kwargs):
            entered.set()
            release.wait(3)
            return page_snapshot(db, sid, page, **kwargs)
        with patch('ui.student_portal.page_snapshot', side_effect=slow):
            app._nav_btns['notifications'].invoke()
            app._request.timeout = .1
            self.until(entered.is_set)
            request = app._request
            self.until(lambda: app._page_state == 'failed')
        self.assertTrue(app._refresh.busy)
        self.assertTrue(request.cancelled.is_set())
        app._retry_button.invoke()
        self.assertIsNot(app._request, request)
        release.set()
        self.wait_page()
        self.assertEqual(app._page_state, 'empty')
        entered.clear(); release.clear()
        results = []
        app._run_action(lambda db, sid: (entered.set(), release.wait(3), 'finished')[-1], results.append)
        app._action_request.timeout = .1
        self.until(entered.is_set)
        self.until(lambda: app._page_state == 'failed')
        self.assertFalse(app._run_action(lambda db, sid: None, results.append))
        self.assertIn('Waiting for the outcome', app._page_status.cget('text'))
        release.set()
        self.until(lambda: app._action_request is None)
        self.assertEqual(results, ['finished'])
        self.assertEqual(app._page_state, 'empty')

    def test_evidence_on_demand_and_profile_update(self):
        output = io.BytesIO()
        Image.new('RGB', (60, 40), 'blue').save(output, format='JPEG')
        vid = self.detect(snapshot=output.getvalue())
        self.click('violations')
        self.button('View Detection Evidence').invoke()
        self.until(lambda: self.app._action_request is None)
        modal = next(w for w in self.app.winfo_children() if isinstance(w, ctk.CTkToplevel))
        from core.evidence_integrity import digest
        self.assertEqual(modal._evidence_key, ('violation', vid, digest(output.getvalue())))
        self.assertIsNotNone(modal._evidence_image)
        modal.destroy()
        self.click('profile')
        self.button('Update Profile').invoke()
        modal = next(w for w in self.app.winfo_children() if isinstance(w, ctk.CTkToplevel))
        entry = next(w for w in self.widgets(modal) if isinstance(w, ctk.CTkEntry))
        entry.delete(0, 'end'); entry.insert(0, 'Updated Test Name')
        self.button('Save', modal).invoke()
        self.until(lambda: self.app._action_request is None)
        self.wait_page()
        self.assertEqual(self.db.get_student_by_student_id(self.SID)['name'], 'Updated Test Name')
        self.assertEqual(self.db.get_student_by_student_id(self.OTHER)['name'], 'Same display name')
        entries = [w for w in self.widgets() if isinstance(w, ctk.CTkEntry)]
        self.assertEqual(len(entries), 3)
        for entry, value in zip(entries, ('fixture personal passphrase', 'new-password', 'new-password')):
            entry.insert(0, value)
        self.button('Save Password').invoke()
        self.until(lambda: self.app._action_request is None)
        self.assertEqual(self.db.verify_student_account(self.SID, 'new-password')['student_id'], self.SID)
        photo = Path(self.tmp.name) / 'profile.png'
        Image.new('RGB', (90, 60), 'green').save(photo)
        with patch('ui.student_portal.filedialog.askopenfilename', return_value=str(photo)):
            self.button('Change Photo').invoke()
        self.until(lambda: self.app._action_request is None)
        self.wait_page()
        self.assertTrue(self.db.get_student_by_student_id(self.SID)['profile_photo'])
        self.assertEqual(self.db.get_student_by_student_id(self.SID)['photo'], b'')


if __name__ == '__main__':
    unittest.main()
