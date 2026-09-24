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
        self.confirmed = datetime(2099, 8, 4, tzinfo=timezone.utc)
        self.clock = patch("database.db_manager.utc_now", return_value=self.confirmed)
        self.clock_mock = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.vid = self.new_violation()

    def new_violation(self):
        vid = self.db.log_violation("S1", "Student One", "wrong_uniform",
            detected_at=self.confirmed - timedelta(days=2))
        self.db.confirm_violation(vid, confirmed_at=self.confirmed)
        return vid

    def submit(self, vid=None, evidence=None):
        return self.db.insert_appeal(vid or self.vid, "S1", "Please review my explanation and picture.",
                                     evidence=evidence if evidence is not None else picture())


class AppealFlowBackendTests(AppealFlowFixture):
    def test_confirmation_not_detection_starts_five_days_with_exact_boundary(self):
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
    # Use one interpreter for this class to avoid Tk's global state crossing roots.
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cancel_callbacks(cls.root)
        cls.root.destroy()
        cls.root = None
        # Dispose Tk image/widget cycles on their owning thread, before camera
        # stress tests create workers that might otherwise trigger collection.
        gc.collect()

    def tearDown(self):
        cancel_callbacks(self.root)
        for child in self.root.winfo_children():
            child.destroy()
        self.root._appeal_buttons = []
        self.root._image_refs = []
        gc.collect()

    def portal(self):
        root = self.root
        root.db = self.db
        root.student_id = "S1"
        root.display_name = "Student One"
        root._appeal_buttons = []
        root._standing_labels = []
        root._appeals = []
        root._appealed_ids = set()
        root._ai_ui_updates = queue.Queue()
        root._active = "violations"
        root._image_refs = []
        for name in ("_make_appeal_button", "_refresh_appeal_buttons", "_open_appeal_form",
                     "_poll_ai_updates", "_refresh_appeal_results", "_reload_workflow_data",
                     "_appeal_status_pill", "_status_pill"):
            setattr(root, name, types.MethodType(getattr(StudentPortal, name), root))
        root._appeal_ineligible_message = StudentPortal._appeal_ineligible_message
        root._card = StudentPortal._card
        root._toast = Mock()
        root._log_activity = Mock()
        root._show = Mock()
        root._last_appeal_refresh = root._last_result_refresh = root._last_standing_refresh = 0
        root._reload_workflow_data()
        return root

    def form(self, portal):
        portal._open_appeal_form(portal._violations[0])
        modal = next(w for w in portal.winfo_children() if isinstance(w, ctk.CTkToplevel))
        modal.withdraw()
        reason = next(w for w in widgets(modal) if isinstance(w, ctk.CTkTextbox))
        reason.insert("1.0", "Please review the attached picture and my explanation.")
        submit = next(w for w in widgets(modal) if isinstance(w, ctk.CTkButton) and w.cget("text") == "Submit Appeal")
        browse = next(w for w in widgets(modal) if isinstance(w, ctk.CTkButton) and w.cget("text") == "Browse…")
        return modal, reason, submit, browse

    def test_open_form_and_notification_buttons_expire_on_the_existing_timer(self):
        portal = self.portal()
        host = ctk.CTkFrame(portal)
        StudentPortal._notification_row(portal, host, 0, portal._notifications[0])
        portal._viol_list = ctk.CTkFrame(portal)
        StudentPortal._violation_card(portal, 0, portal._violations[0])
        violation_button = next(w for w in widgets(portal._viol_list) if isinstance(w, ctk.CTkButton)
                                and w.cget("text") == "Submit Appeal")
        modal, reason, submit, _ = self.form(portal)
        notification_button = next(w for w in widgets(host) if isinstance(w, ctk.CTkButton)
                                   and w.cget("text") == "Submit Appeal")
        original_reason = reason.get("1.0", "end-1c")
        portal._poll_ai_updates()
        self.clock_mock.return_value = self.confirmed + timedelta(days=5, seconds=1)
        portal._last_appeal_refresh = 0
        until = time.monotonic() + 1.5
        while time.monotonic() < until and submit.cget("state") != "disabled":
            portal.update()
            time.sleep(.01)
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
            self.assertTrue(modal.winfo_exists())
            self.assertIsNone(self.db.get_appeal_for_violation(self.vid))

    def test_ai_completion_cannot_hide_a_simultaneous_admin_decision(self):
        aid = self.submit()
        portal = self.portal()
        self.assertEqual(portal._appeals[0]["status"], "pending")
        self.db.update_appeal_decision(aid, "approved", "Picture reviewed", decided_by="osa.reviewer")
        portal._last_result_refresh = time.monotonic()  # Next scheduled result poll is not due.
        portal._ai_ui_updates.put(aid)
        portal._poll_ai_updates()
        portal._show.assert_called_with("violations")
        self.assertEqual(portal._violations[0]["appeal_status"], "approved")
        self.assertEqual(portal._appeals[0]["status"], "approved")

    def test_notification_to_picture_submission_admin_decisions_and_student_refresh(self):
        for decision in ("approved", "rejected"):
            with self.subTest(decision=decision):
                if decision == "rejected":
                    self.vid = self.new_violation()
                portal = self.portal()
                host = ctk.CTkFrame(portal)
                notice = next(n for n in portal._notifications if n["violation_id"] == self.vid)
                StudentPortal._notification_row(portal, host, 0, notice)
                button = next(w for w in widgets(host) if isinstance(w, ctk.CTkButton)
                              and w.cget("text") == "Submit Appeal")
                self.assertEqual(button.cget("fg_color"), "#32CD32")
                button.invoke()
                modal = next(w for w in portal.winfo_children() if isinstance(w, ctk.CTkToplevel))
                modal.withdraw()
                reason = next(w for w in widgets(modal) if isinstance(w, ctk.CTkTextbox))
                reason.insert("1.0", "Please review this evidence and my written explanation.")
                path = Path(self.tmp.name) / "proof.png"
                path.write_bytes(picture()[2])
                browse = next(w for w in widgets(modal) if isinstance(w, ctk.CTkButton) and w.cget("text") == "Browse…")
                with patch("ui.student_portal.filedialog.askopenfilename", return_value=str(path)):
                    browse.invoke()
                submit = next(w for w in widgets(modal) if isinstance(w, ctk.CTkButton) and w.cget("text") == "Submit Appeal")
                with patch("ui.student_portal.analyze_appeal"):
                    submit.invoke()
                aid = self.db.get_appeal_for_violation(self.vid)["id"]
                broker = Notifier()
                records = RecordsPanel(portal, database=self.db, username="osa.reviewer")
                alerts = NotificationsPanel(portal, notifier=broker, database=self.db, on_open=records.open_alert)
                alerts._select_category("appeals")
                index = next(i for i, n in enumerate(alerts._visible_items()) if n.id == aid)
                alert_card = alerts._list.winfo_children()[index]
                next(w for w in alert_card.winfo_children() if isinstance(w, ctk.CTkButton)
                     and w.cget("text") == "Open Appeal Management").invoke()
                self.assertEqual(records._current_appeal["id"], aid)
                self.assertTrue(records._ap_ev_img.cget("image"))
                records._open_appeal_picture()
                self.assertTrue(any(isinstance(w, ctk.CTkToplevel) for w in records.winfo_children()))
                records._ap_notes.insert("1.0", "Reviewed the submitted picture.")
                (records._ap_approve_btn if decision == "approved" else records._ap_reject_btn).invoke()
                portal._active = "appeals"
                portal._show.reset_mock()
                portal._refresh_appeal_results()
                portal._show.assert_called_with("appeals")
                updated = next(a for a in portal._appeals if a["id"] == aid)
                self.assertEqual(updated["status"], decision)
                self.assertEqual(updated["decided_by"], "osa.reviewer")
                result_host = ctk.CTkFrame(portal)
                StudentPortal._appeal_card(portal, result_host, 0, updated)
                outcome_text = "Appeal Approved — Strike Removed" if decision == "approved" else "Appeal Rejected — Strike Remains"
                self.assertTrue(any(isinstance(w, ctk.CTkLabel) and w.cget("text") == outcome_text
                                    for w in widgets(result_host)))
                self.assertTrue(any(n["title"].startswith(f"Appeal {decision.title()}") for n in portal._notifications))
                with self.db.connect() as conn:
                    active = conn.execute("SELECT is_active FROM strikes WHERE violation_id=?", (self.vid,)).fetchone()[0]
                self.assertEqual(active, int(decision == "rejected"))
                alerts.destroy()
                records.destroy()
                host.destroy()
                result_host.destroy()

    def test_alerts_opening_sidebar_count_filters_and_midnight_refresh(self):
        aid = self.submit()
        broker = Notifier()
        broker.sound_enabled = False
        broker.notify("Student One", "Wrong uniform")
        dashboard = types.SimpleNamespace(_notifier=broker, _database=self.db, _bell_badge=Mock(),
                                         _on_nav_select=Mock(), _records_panel=Mock())
        CBVMSDashboard._open_alerts_from_bell(dashboard)
        dashboard._on_nav_select.assert_called_once_with("alerts")
        self.assertEqual(self.db.admin_appeal_unread_count(), 1)
        self.assertEqual(broker.unread_count(), 1)
        CBVMSDashboard._update_bell_badge(dashboard)
        dashboard._bell_badge.configure.assert_called_with(text="2")
        CBVMSDashboard._open_alert_record(dashboard, "appeals", aid)
        dashboard._records_panel.open_alert.assert_called_with("appeals", aid)
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
