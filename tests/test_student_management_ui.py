"""Hidden widget integration checks; no camera, model loading, or real database."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import customtkinter as ctk
from database.db_manager import CBVMSDatabase
from ui.enrollment import EnrollmentPanel
from ui import student_management as sm
from auth.register import StudentRegistrationWindow
from ui.student_portal import StudentPortal
import types
import io
from datetime import timedelta
from PIL import Image
from core.discipline import utc_now


class StudentManagementUITests(unittest.TestCase):
    def wait_for(self, root, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_summary_tracks_selection_filters_status_and_deleted_student(self):
        root = ctk.CTk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as tmp:
            db = CBVMSDatabase(Path(tmp) / "summary.db")
            db.initialize()
            photo = io.BytesIO()
            Image.new("RGB", (400, 800), "steelblue").save(photo, format="PNG")
            pk = db.insert_student("S1", "Student One", "BSIT", "3A", b"", photo.getvalue())
            second = db.insert_student("S2", "Student Two", "BSIT", "1A", b"", b"")
            panel = EnrollmentPanel(root, db, MagicMock(), lambda: None)
            self.wait_for(root, lambda: len(panel._students) == 2)
            self.assertEqual(panel._details_btn.cget("state"), "disabled")
            panel._tree.selection_set(str(pk))
            panel._on_row_select()
            image = panel._selected_photo_label._cbvms_photo
            self.assertLessEqual(image.width(), 80)
            self.assertLessEqual(image.height(), 80)
            self.assertEqual(image.height(), 2 * image.width())
            self.assertEqual(panel._summary_name.cget("text"), "Student One")
            db.update_student_details("S1", student_status="Graduate", contacts={},
                                      changed_by="osa", reason="Graduated")
            db.impose_suspension("S1", reason="Reviewed", starts_at=utc_now()-timedelta(minutes=1),
                                 ends_at=None, imposed_by="osa")
            panel._reload_students()
            self.wait_for(root, lambda: not panel._list_task.busy)
            self.assertEqual(panel._selected_pk, pk)
            self.assertEqual(panel._summary_standing.cget("text"), "Status: Graduate")
            self.assertIn("indefinite", panel._summary_suspension.cget("text"))
            panel._status_filter.set("Enrolled")
            panel._apply_filter()
            self.assertIsNone(panel._selected_pk)
            self.assertIsNone(panel._selected_photo_label._cbvms_photo)
            self.assertEqual(panel._summary_suspension.cget("text"), "")
            self.assertEqual(panel._delete_btn.cget("state"), "disabled")
            panel._tree.selection_set(str(second))
            panel._on_row_select()
            self.assertEqual(panel._summary_name.cget("text"), "Student Two")
            self.assertEqual(panel._selected_photo_label.cget("text"), "No photo on file")
            with patch("ui.enrollment.messagebox.askyesno", return_value=True):
                panel._delete_selected()
            self.wait_for(root, lambda: len(panel._students) == 1)
            self.assertIsNone(panel._selected_pk)
            self.assertEqual(panel._summary_name.cget("text"), "Select a student")
            self.assertEqual(panel._update_btn.cget("state"), "disabled")
            panel.destroy()

    def test_forms_build_and_save_through_existing_widgets(self):
        try:
            root = ctk.CTk()
        except Exception as exc:
            self.skipTest(f"Tk runtime unavailable: {exc}")
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as tmp:
            db = CBVMSDatabase(Path(tmp) / "ui.db")
            db.initialize()
            db.insert_student("S1", "Student One", "BSIT", "3A", b"", b"")
            panel = EnrollmentPanel(root, db, MagicMock(), lambda: None, username="osa.tester")
            self.wait_for(root, lambda: len(panel._students) == 1)
            panel._tree.selection_set("1")
            panel._on_row_select()
            panel._open_enroll_flow()
            root.update_idletasks()
            self.assertEqual(len(panel._entries), 4)
            self.assertEqual(panel._academics.college.get(), "Select college")
            self.assertEqual(panel._academics.course.cget("state"), "disabled")
            panel._enroll_close()
            windows = []
            original = sm._window

            def hidden_window(*args):
                win = original(*args)
                win.withdraw()
                windows.append(win)
                return win

            with patch.object(sm, "_window", hidden_window):
                sm.open_student_details(panel, db, "S1", "osa.tester", panel._reload_students)
                sm.open_premises_log(panel, db)
                root.update_idletasks()
                details = windows[0]
                self.assertEqual(panel._details_btn.cget("text"), "Edit Details")
                def widgets(widget):
                    yield widget
                    for child in widget.winfo_children():
                        yield from widgets(child)
                self.wait_for(root, lambda: any(isinstance(w, ctk.CTkTabview) for w in widgets(details)))
                tabs = next(w for w in widgets(details) if isinstance(w, ctk.CTkTabview))
                self.assertEqual(list(tabs._tab_dict), ["Details", "Status History"])
                options = [w for w in widgets(details) if isinstance(w, ctk.CTkOptionMenu)]
                options[0].set("Graduate")
                # The section entry precedes the status-change reason.
                entries = [w for w in widgets(details) if isinstance(w, ctk.CTkEntry)]
                entries[1].insert(0, "Graduation verified")
                save = next(w for w in widgets(details) if isinstance(w, ctk.CTkButton)
                            and w.cget("text") == "Save Details")
                save.invoke()
                self.wait_for(root, lambda: not details.winfo_exists())
                self.wait_for(root, lambda: not panel._list_task.busy)
                self.assertEqual(db.get_student_by_student_id("S1")["student_status"], "Graduate")
                self.assertEqual(db.get_student_status_history("S1")[0]["changed_by"], "osa.tester")
            holder = ctk.CTkFrame(root)
            StudentRegistrationWindow._build_form(holder, holder)
            self.assertEqual(len(holder._contact_entries), 2)
            root.update_idletasks()
            portal = types.SimpleNamespace(db=db, student_id="S1", _student=db.get_student_by_student_id("S1"),
                _active_suspension=None,
                _standing_labels=[], _scroll_host=lambda *a: ctk.CTkScrollableFrame(root),
                _card=StudentPortal._card, _profile_image=None,
                _change_profile_photo=MagicMock(),
                _open_update_profile=lambda: None, _activity_log_card=lambda *a, **k: None)
            StudentPortal._panel_profile(portal)
            self.assertEqual(portal._standing_labels[0][0].cget("text"), "Graduate")
            self.assertEqual(portal._standing_labels[1][0].cget("text"), "No active suspension")
            for win in windows:
                if win.winfo_exists():
                    win.destroy()
            panel.destroy()
            holder.destroy()


if __name__ == "__main__":
    unittest.main()
