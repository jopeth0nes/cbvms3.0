"""Appeal deadline, evidence transaction, and notification widget integration."""
import io
import gc
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import customtkinter as ctk
from PIL import Image

from core.notifier import Notifier
from database.db_manager import CBVMSDatabase
from ui.notifications_panel import NotificationsPanel
from ui.records_panel import RecordsPanel
from ui.student_portal import StudentPortal, SP_APPEAL, SP_DISABLED


class AppealAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / "test.db")
        self.db.initialize()
        self.db.insert_student("S1", "Student One", "BSIT", "3A", b"", b"")
        self.now = datetime(2099, 8, 1, tzinfo=timezone.utc)
        self.vid = self.db.log_violation(student_id="S1", student_name="Student One",
            violation_type="Wrong Uniform", violation_code="wrong_uniform", detected_at=self.now)
        self.db.confirm_violation(self.vid, confirmed_at=self.now)

    def submit(self, evidence=None):
        with patch("database.db_manager.utc_now", return_value=self.now):
            return self.db.insert_appeal(self.vid, "S1", "Please review my supporting evidence.",
                evidence=self.picture() if evidence is None else evidence)

    def picture(self):
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "blue").save(image, format="PNG")
        return ("proof.png", "image", image.getvalue())

    def root(self):
        root = ctk.CTk()
        root.withdraw()
        def cleanup():
            for job in root.tk.splitlist(root.tk.call("after", "info")):
                root.tk.call("after", "cancel", job)
            root.destroy()
            gc.collect()
        self.addCleanup(cleanup)
        return root

    def test_evidence_and_appeal_commit_together_and_alert_survives_restart(self):
        aid = self.submit(self.picture())
        self.assertIsNotNone(aid)
        self.assertEqual(len(self.db.get_evidence_for_appeal(aid)), 1)
        reopened = CBVMSDatabase(self.db.db_path)
        reopened.initialize(process_deadlines=False)
        self.assertEqual(reopened.admin_appeal_unread_count(), 1)
        self.assertEqual(reopened.get_admin_appeal_alerts()[0]["id"], aid)
        reopened.mark_admin_appeal_alert_read(aid)
        self.assertEqual(self.db.admin_appeal_unread_count(), 0)
        self.assertEqual(self.db.get_appeal_for_violation(self.vid)["status"], "pending")

    def test_failed_attachment_rolls_back_appeal_and_does_not_create_alert(self):
        with self.db.connect() as conn:
            conn.execute("""CREATE TRIGGER fail_evidence BEFORE INSERT ON evidence_files
                BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
        self.assertIsNone(self.submit(self.picture()))
        self.assertIsNone(self.db.get_appeal_for_violation(self.vid))
        self.assertEqual(self.db.admin_appeal_unread_count(), 0)

    def test_invalid_picture_cannot_create_appeal(self):
        self.assertIsNone(self.submit(("proof.png", "image", b"not a picture")))
        self.assertIsNone(self.db.get_appeal_for_violation(self.vid))

    def test_missing_picture_pdf_and_text_are_rejected(self):
        with patch("database.db_manager.utc_now", return_value=self.now):
            self.assertIsNone(self.db.insert_appeal(self.vid, "S1", "Explanation without a picture."))
        for evidence in (("proof.pdf", "document", b"%PDF-1.7"),
                         ("reason.txt", "document", b"My explanation"),
                         ("proof.pdf", "image", self.picture()[2])):
            self.assertIsNone(self.submit(evidence))
        self.assertIsNone(self.db.get_appeal_for_violation(self.vid))

    def test_button_stays_present_but_disables_after_five_days(self):
        root = self.root()
        portal = types.SimpleNamespace(db=self.db, student_id="S1", _appeal_buttons=[], _appeal_eligibility={},
            _open_appeal_form=Mock())
        portal._refresh_appeal_buttons = types.MethodType(StudentPortal._refresh_appeal_buttons, portal)
        with patch("database.db_manager.utc_now", return_value=self.now):
            row = self.db.get_visible_violations_for_student('S1')[0]
            button = StudentPortal._make_appeal_button(portal, root, row)
            button.pack()
            self.assertEqual(button.cget("state"), "normal")
            self.assertEqual(button.cget("fg_color"), SP_APPEAL)
            button.invoke()
        self.assertEqual(portal._open_appeal_form.call_count, 1)
        with patch("ui.student_portal.utc_now", return_value=self.now + timedelta(days=5)):
            portal._refresh_appeal_buttons()
            self.assertEqual(button.cget("state"), "normal")
        with patch("database.db_manager.utc_now", return_value=self.now + timedelta(days=5, seconds=1)), \
                patch("ui.student_portal.utc_now", return_value=self.now + timedelta(days=5, seconds=1)):
            portal._refresh_appeal_buttons()
            self.assertTrue(button.winfo_exists())
            self.assertEqual(button.cget("state"), "disabled")
            self.assertEqual(button.cget("fg_color"), SP_DISABLED)
            self.assertEqual(button.cget("text"), "Appeal Period Expired")
            button.invoke()
            self.assertEqual(portal._open_appeal_form.call_count, 1)
            self.assertIsNone(self.db.insert_appeal(self.vid, "S1", "A late appeal with a complete explanation.",
                                                  evidence=self.picture()))

    def test_notification_categories_badges_navigation_and_read_isolation(self):
        root = self.root()
        aid = self.submit()
        broker = Notifier()
        broker.sound_enabled = False
        broker.notify("Student One", "Wrong Uniform")
        open_record = Mock()
        panel = NotificationsPanel(root, notifier=broker, database=self.db, on_open=open_record)
        self.addCleanup(panel.destroy)
        self.assertEqual(panel._category_badges["appeals"].cget("text"), "1")
        self.assertEqual(panel._category_badges["violations"].cget("text"), "1")
        panel._select_category("appeals")
        self.assertEqual([n.id for n in panel._visible_items()], [aid])
        card = panel._list.winfo_children()[0]
        next(w for w in card.winfo_children() if isinstance(w, ctk.CTkButton)
             and w.cget("text") == "Open Appeal Management").invoke()
        open_record.assert_called_once_with("appeals", aid)
        self.assertEqual(self.db.admin_appeal_unread_count(), 0)
        self.assertEqual(broker.unread_count(), 1)
        panel._on_filter_change("Unread")
        self.assertEqual(panel._visible_items(), [])
        panel._select_category("violations")
        self.assertEqual(len(panel._visible_items()), 1)
        card = panel._list.winfo_children()[0]
        next(w for w in card.winfo_children() if isinstance(w, ctk.CTkButton)
             and w.cget("text") == "Open Violation Reports").invoke()
        open_record.assert_called_with("violations", None)
        self.assertEqual(broker.unread_count(), 0)

    def test_records_navigation_selects_appeal_even_when_filtered(self):
        root = self.root()
        aid = self.submit()
        panel = RecordsPanel(root, database=self.db)
        panel._appeal_filter.set("Approved")
        panel.open_alert("appeals", aid)
        self.assertEqual(panel._tab_var.get(), "appeals")
        self.assertEqual(panel._current_appeal["id"], aid)
        panel.open_alert("violations")
        self.assertEqual(panel._tab_var.get(), "violations")
