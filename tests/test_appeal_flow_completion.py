"""Regression coverage for the complete picture-backed appeal and Alerts flow."""
import io
import gc
import queue
import tempfile
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import customtkinter as ctk
from PIL import Image

from core.appeal_evidence import MAX_EVIDENCE_BYTES
from core.notifier import Notifier, Notification
from database.db_manager import CBVMSDatabase
from ui.dashboard import CBVMSDashboard
from ui.notifications_panel import NotificationsPanel
from ui.records_panel import RecordsPanel
from ui.appeals_panel import AppealsPanel
from ui.student_portal import StudentPortal, SP_APPEAL, SP_DISABLED


def picture(fmt="PNG", extension="png"):
    data = io.BytesIO()
    Image.new("RGB", (40, 30), "green").save(data, format=fmt)
    return (f"proof.{extension}", "image", data.getvalue())


def widgets(parent):
    yield parent
    for child in parent.winfo_children():
        yield from widgets(child)


def cancel_callbacks(root):
    for job in root.tk.splitlist(root.tk.call("after", "info")):
        # Cancel the event only; each owning widget deletes its Tcl command on destroy.
        root.tk.call("after", "cancel", job)


class AppealFlowFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / "appeals.db")
        self.db.initialize()
        self.db.insert_student("S1", "Student One", "BSIT", "3A", b"", b"")
        with self.db.connect() as conn:
            conn.execute("INSERT INTO users (username,password_hash) VALUES ('osa.reviewer','test-only')")
        self.confirmed = datetime(2099, 8, 4, tzinfo=timezone.utc)
        self.clock = patch("database.db_manager.utc_now", return_value=self.confirmed)
        self.clock_mock = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.vid = self.new_violation()

    def new_violation(self):
        vid = self.db.log_violation("S1", "Student One", "wrong_uniform",
            detected_at=self.confirmed - timedelta(days=2))
        return vid

    def submit(self, vid=None, evidence=None):
        return self.db.insert_appeal(vid or self.vid, "S1", "Please review my explanation and picture.",
                                     evidence=evidence if evidence is not None else picture())


class AppealFlowBackendTests(AppealFlowFixture):
    def test_publication_not_detection_starts_five_days_with_exact_boundary(self):
        deadline = self.confirmed + timedelta(days=5)
        self.assertEqual(self.db.get_appeal_eligibility(self.vid, "S1")["deadline"],
                         deadline.strftime("%Y-%m-%d %H:%M:%S"))
        vid2 = self.new_violation()
        self.clock_mock.return_value = deadline
        self.assertIsNotNone(self.submit())
        self.clock_mock.return_value = deadline + timedelta(microseconds=1)
        self.assertIsNone(self.submit(vid2))

    def test_clock_is_checked_after_waiting_for_write_lock(self):
        deadline = self.confirmed + timedelta(days=5)
        self.clock_mock.return_value = deadline - timedelta(seconds=1)
        original_connect = self.db.connect
        clock = self.clock_mock
        @contextmanager
        def delayed_connect():
            with original_connect() as conn:
                class DelayedConnection:
                    def __getattr__(self, name):
                        return getattr(conn, name)
                    def execute(self, sql, *args):
                        result = conn.execute(sql, *args)
                        if sql == "BEGIN IMMEDIATE":
                            clock.return_value = deadline + timedelta(seconds=1)
                        return result
                yield DelayedConnection()
        with patch.object(self.db, "connect", delayed_connect):
            self.assertIsNone(self.submit())
        self.assertIsNone(self.db.get_appeal_for_violation(self.vid))

    def test_concurrent_duplicate_submissions_commit_one_picture_and_alert(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.submit(), range(2)))
        self.assertEqual(sum(aid is not None for aid in results), 1)
        aid = next(aid for aid in results if aid is not None)
        self.assertEqual(len(self.db.get_evidence_for_appeal(aid)), 1)
        self.assertEqual(self.db.admin_appeal_unread_count(), 1)

    def test_all_supported_picture_formats_and_invalid_payloads(self):
        for fmt, extension in (("JPEG", "jpg"), ("JPEG", "jpeg"), ("PNG", "PNG"), ("BMP", "bmp")):
            with self.subTest(extension=extension):
                self.assertIsNotNone(self.submit(self.new_violation(), picture(fmt, extension)))
        for evidence in ((), ("a.png",), (None, "image", b"x"), ("a.png", "image", None),
                         ("a.png", "image", b"x" * (MAX_EVIDENCE_BYTES + 1)),
                         picture("GIF", "png"), ("a.png", "image", picture()[2][:25])):
            with self.subTest(kind=str(evidence[:2])):
                self.assertIsNone(self.submit(evidence=evidence))
        self.assertIsNone(self.db.get_appeal_for_violation(self.vid))

    def test_reason_is_required_and_secondary_evidence_api_validates_owner_and_picture(self):
        for reason in ("", "short", "a" * 1001):
            self.assertIsNone(self.db.insert_appeal(self.vid, "S1", reason, evidence=picture()))
        aid = self.submit()
        self.assertIsNone(self.db.insert_evidence_file(aid, "other", *picture()))
        self.assertIsNone(self.db.insert_evidence_file(aid, "S1", "reason.txt", "document", b"text"))
        self.assertEqual(len(self.db.get_evidence_for_appeal(aid)), 1)

    def test_read_and_unread_appeals_survive_restart_and_decision(self):
        first = self.submit()
        second = self.submit(self.new_violation())
        self.db.mark_admin_appeal_alert_read(first)
        self.db.update_appeal_decision(second, "rejected", "Evidence reviewed", decided_by="osa.reviewer")
        reopened = CBVMSDatabase(self.db.db_path)
        reopened.initialize(process_deadlines=False)
        states = {row["id"]: row["admin_alert_read"] for row in reopened.get_admin_appeal_alerts()}
        self.assertEqual(states, {first: 1, second: 0})
        self.assertEqual(reopened.admin_appeal_unread_count(), 1)


class AppealFlowWidgetTests(AppealFlowFixture):
    """Exercise the merged portal through its real worker and native event loop."""
    def setUp(self):
        gc.collect()
        super().setUp()
        self.root = None
        self.errors = []
        self.ui_clock = patch("ui.student_portal.utc_now", side_effect=lambda: self.clock_mock.return_value)
        self.ui_clock.start()
        self.addCleanup(self.ui_clock.stop)

    def tearDown(self):
        root, self.root = self.root, None
        if isinstance(root, StudentPortal):
            root._on_close()
            self.assertTrue(root._refresh.done.wait(3))
        elif root is not None:
            cancel_callbacks(root)
            root.destroy()
        del root
        gc.collect()
        self.assertEqual(self.errors, [])

    def until(self, predicate, seconds=6):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.root.after(20, self.root.quit)
            self.root.mainloop()
        self.assertTrue(predicate(), 'Portal did not finish the requested operation')

    def loaded(self):
        self.until(lambda: self.root._request is None and self.root._action_request is None
                   and self.root._page_state in {'loaded', 'empty', 'failed'})
        self.assertNotEqual(self.root._page_state, 'failed', self.root._page_status.cget('text'))

    def navigate(self, page):
        self.root._nav_btns[page].invoke()
        self.loaded()

    def portal(self):
        self.root = StudentPortal(student_id="S1", display_name="Student One", database=self.db)
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.loaded()
        self.navigate('violations')
        return self.root

    @staticmethod
    def button(parent, text):
        return next(w for w in widgets(parent) if isinstance(w, ctk.CTkButton) and w.cget('text') == text)

    def form(self, portal):
        portal._open_appeal_form(portal._violations[0])
        modal = next(w for w in portal.winfo_children() if isinstance(w, ctk.CTkToplevel))
        reason = next(w for w in widgets(modal) if isinstance(w, ctk.CTkTextbox))
        reason.insert("1.0", "Please review the attached picture and my explanation.")
        return modal, reason, self.button(modal, 'Submit Appeal'), self.button(modal, 'Browse…')

    def test_open_form_and_notification_buttons_expire_on_the_existing_timer(self):
        portal = self.portal()
        self.navigate('notifications')
        host = ctk.CTkFrame(portal)
        portal._notification_row(host, 0, portal._notifications[0])
        notification_button = self.button(host, 'Submit Appeal')
        self.navigate('violations')
        violation_button = self.button(portal._page_body, 'Submit Appeal')
        modal, reason, submit, _ = self.form(portal)
        original_reason = reason.get("1.0", "end-1c")
        self.clock_mock.return_value = self.confirmed + timedelta(days=5, seconds=1)
        portal._last_appeal_refresh = 0
        # The timer must expire cached eligibility without SQLite on Tk's thread.
        with patch.object(portal.db, 'connect', side_effect=AssertionError('UI database access')):
            self.until(lambda: submit.cget('state') == 'disabled', seconds=2)
        for button in (submit, notification_button, violation_button):
            self.assertTrue(button.winfo_exists())
            self.assertEqual(button.cget("text"), "Appeal Period Expired")
            self.assertEqual(button.cget("fg_color"), SP_DISABLED)
            self.assertEqual(button.cget("state"), "disabled")
            button.invoke()
        self.assertEqual(violation_button._appeal_outcome_label.cget("text"), "Appeal Period Expired")
        self.assertEqual(reason.get("1.0", "end-1c"), original_reason)
        self.assertIsNone(self.db.get_appeal_for_violation(self.vid))

    def test_missing_and_invalid_picture_keep_form_open_without_submission(self):
        portal = self.portal()
        modal, _, submit, browse = self.form(portal)
        submit.invoke()
        self.assertIsNone(self.db.get_appeal_for_violation(self.vid))
        for name, data in (("fake.png", b"not an image"), ("reason.txt", b"reason"), ("file.pdf", b"%PDF")):
            path = Path(self.tmp.name) / name
            path.write_bytes(data)
            with patch("ui.student_portal.filedialog.askopenfilename", return_value=str(path)):
                browse.invoke()
            submit.invoke()
            self.until(lambda: portal._action_request is None)
            self.assertEqual(portal._page_state, 'failed')
            self.assertTrue(modal.winfo_exists())
            self.assertIsNone(self.db.get_appeal_for_violation(self.vid))

    def test_ai_completion_cannot_hide_a_simultaneous_admin_decision(self):
        aid = self.submit()
        portal = self.portal()
        self.assertEqual(portal._violations[0]['appeal_status'], 'pending')
        self.db.update_appeal_decision(aid, "approved", "Picture reviewed", decided_by="osa.reviewer")
        portal._last_workflow_refresh = time.monotonic()
        portal._ai_ui_updates.put(aid)
        self.until(lambda: portal._violations[0]['appeal_status'] == 'approved')
        self.assertEqual(portal._active, 'violations')
        self.navigate('appeals')
        self.assertEqual(portal._appeals[0]['status'], 'approved')

    def test_notification_to_picture_submission_admin_decisions_and_student_refresh(self):
        portal = self.portal()
        for decision in ('approved', 'rejected'):
            with self.subTest(decision=decision):
                if decision == 'rejected':
                    self.vid = self.new_violation()
                self.navigate('notifications')
                notice = next(n for n in portal._notifications if n['violation_id'] == self.vid)
                host = ctk.CTkFrame(portal)
                portal._notification_row(host, 0, notice)
                button = self.button(host, 'Submit Appeal')
                self.assertEqual(button.cget('fg_color'), SP_APPEAL)
                button.invoke()
                modal = next(w for w in portal.winfo_children() if isinstance(w, ctk.CTkToplevel))
                reason = next(w for w in widgets(modal) if isinstance(w, ctk.CTkTextbox))
                reason.insert('1.0', 'Please review this evidence and my written explanation.')
                path = Path(self.tmp.name) / 'proof.png'
                path.write_bytes(picture()[2])
                with patch('ui.student_portal.filedialog.askopenfilename', return_value=str(path)):
                    self.button(modal, 'Browse…').invoke()
                with patch('ui.student_portal.analyze_appeal'), patch.object(
                        portal.db, 'connect', side_effect=AssertionError('UI database access')):
                    self.button(modal, 'Submit Appeal').invoke()
                    self.until(lambda: portal._active == 'appeals' and portal._action_request is None)
                    self.loaded()
                aid = self.db.get_appeal_for_violation(self.vid)['id']
                broker = Notifier()
                records = AppealsPanel(portal, database=self.db, username='osa.reviewer')
                alerts = NotificationsPanel(portal, notifier=broker, database=self.db, on_open=records.open_alert)
                alerts._select_category('appeals')
                index = next(i for i, n in enumerate(alerts._visible_items()) if n.id == aid)
                self.button(alerts._list.winfo_children()[index], 'Open Appeal Management').invoke()
                self.until(lambda: getattr(records,'case',{}).get('id') == aid)
                self.assertEqual(records.case_id, aid)
                self.assertIsNotNone(records.case['images'][1])
                records.reason.insert('1.0', 'Reviewed the submitted picture.')
                with patch('ui.appeals_panel.messagebox.askyesno',return_value=True):
                    (records.approve if decision == 'approved' else records.reject).invoke()
                self.until(lambda: records.case.get('status') == decision)
                portal._refresh_appeal_results()
                self.loaded()
                updated = next(a for a in portal._appeals if a['id'] == aid)
                self.assertEqual(updated['status'], decision)
                self.assertEqual(updated['decided_by'], 'osa.reviewer')
                outcome_text = 'Appeal Approved — No active strike' if decision == 'approved' else 'Appeal Rejected — One finalized strike'
                self.assertTrue(any(isinstance(w, ctk.CTkLabel) and w.cget('text') == outcome_text
                                    for w in widgets(portal._page_body)))
                with self.db.connect() as conn:
                    active = conn.execute('SELECT COALESCE(SUM(is_active),0) FROM strikes WHERE violation_id=?', (self.vid,)).fetchone()[0]
                self.assertEqual(active, int(decision == 'rejected'))
                self.navigate('notifications')
                self.assertTrue(any(n['title'].startswith(f'Appeal {decision.title()}') for n in portal._notifications))
                alerts.destroy()
                records.destroy()
                host.destroy()

    def test_alerts_opening_sidebar_count_filters_and_midnight_refresh(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        aid = self.submit()
        broker = Notifier()
        broker.sound_enabled = False
        broker.notify("Student One", "Wrong uniform")
        dashboard = types.SimpleNamespace(_notifier=broker, _database=self.db, _bell_badge=Mock(),
                                         _on_nav_select=Mock(), _records_panel=Mock(), _appeals_panel=Mock(), _appeal_unread=1)
        CBVMSDashboard._open_alerts_from_bell(dashboard)
        dashboard._on_nav_select.assert_called_once_with("alerts")
        self.assertEqual(self.db.admin_appeal_unread_count(), 1)
        self.assertEqual(broker.unread_count(), 1)
        CBVMSDashboard._update_bell_badge(dashboard)
        dashboard._bell_badge.configure.assert_called_with(text="2")
        CBVMSDashboard._open_alert_record(dashboard, "appeals", aid)
        dashboard._appeals_panel.open_alert.assert_called_with("appeals", aid)
        CBVMSDashboard._open_alert_record(dashboard, "violations", None)
        dashboard._records_panel.open_alert.assert_called_with("violations", None)
        panel = NotificationsPanel(self.root, notifier=broker, database=self.db)
        panel._select_category("appeals")
        panel._mark_all_read()
        self.assertEqual(broker.unread_count(), 1)
        self.assertEqual(self.db.admin_appeal_unread_count(), 0)
        local_today = datetime.now()
        samples = [Notification(1, "A", "V", local_today.timestamp()),
                   Notification(2, "A", "V", (local_today - timedelta(days=1)).timestamp()),
                   Notification(3, "A", "A", local_today.timestamp(), category="appeals")]
        panel._category = "violations"
        panel._filter = "Today"
        self.assertEqual([n.id for n in panel._visible_items(samples)], [1])
        panel._category = "appeals"
        self.assertEqual([n.id for n in panel._visible_items(samples)], [3])
        with patch.object(panel, "_all_items", return_value=samples), \
                patch.object(panel, "_render") as render, \
                patch("ui.notifications_panel.datetime") as local_clock:
            local_clock.now.return_value = local_today
            panel._refresh()
            render.reset_mock()
            local_clock.now.return_value = local_today + timedelta(days=1)
            panel._refresh()
            render.assert_called_once()
        panel.destroy()


if __name__ == "__main__":
    unittest.main()
