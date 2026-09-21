"""Hidden widget integration checks; no camera, model loading, or real database."""
import tempfile
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


class StudentManagementUITests(unittest.TestCase):
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
            panel._tree.selection_set("1")
            panel._on_row_select()
            panel._open_enroll_flow()
            root.update_idletasks()
            self.assertEqual(len(panel._entries), 6)
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
                def widgets(widget):
                    yield widget
                    for child in widget.winfo_children():
                        yield from widgets(child)
                options = [w for w in widgets(details) if isinstance(w, ctk.CTkOptionMenu)]
                options[0].set("Graduate")
                # First entry is the required status-change reason.
                entries = [w for w in widgets(details) if isinstance(w, ctk.CTkEntry)]
                entries[0].insert(0, "Graduation verified")
                save = next(w for w in widgets(details) if isinstance(w, ctk.CTkButton)
                            and w.cget("text") == "Save Details")
                save.invoke()
                self.assertEqual(db.get_student_by_student_id("S1")["student_status"], "Graduate")
                self.assertEqual(db.get_student_status_history("S1")[0]["changed_by"], "osa.tester")
                sm.open_student_details(panel, db, "S1", "osa.tester", panel._reload_students, "Suspension")
                tabview = next(w for w in widgets(windows[-1]) if isinstance(w, ctk.CTkTabview))
                suspension_tab = tabview.tab("Suspension")
                entries = [w for w in widgets(suspension_tab) if isinstance(w, ctk.CTkEntry)]
                entries[2].insert(0, "One-day suspension reviewed by OSA")
                assign = next(w for w in widgets(suspension_tab) if isinstance(w, ctk.CTkButton)
                              and w.cget("text") == "Assign Suspension")
                assign.invoke()
                self.assertIsNotNone(db.get_active_suspension("S1"))
                tabview = next(w for w in widgets(windows[-1]) if isinstance(w, ctk.CTkTabview))
                suspension_tab = tabview.tab("Suspension")
                entries = [w for w in widgets(suspension_tab) if isinstance(w, ctk.CTkEntry)]
                entries[-1].insert(0, "OSA clearance")
                lift = next(w for w in widgets(suspension_tab) if isinstance(w, ctk.CTkButton)
                            and w.cget("text").startswith("Lift / Cancel #"))
                lift.invoke()
                self.assertIsNone(db.get_active_suspension("S1"))
                self.assertEqual(db.get_suspension_history("S1")[0]["lift_reason"], "OSA clearance")
            holder = ctk.CTkFrame(root)
            StudentRegistrationWindow._build_form(holder, holder)
            self.assertEqual(len(holder._contact_entries), 2)
            root.update_idletasks()
            portal = types.SimpleNamespace(db=db, student_id="S1", _student=db.get_student_by_student_id("S1"),
                _standing_labels=[], _scroll_host=lambda *a: ctk.CTkScrollableFrame(root),
                _card=StudentPortal._card, _photo_from_blob=lambda *a: None,
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
