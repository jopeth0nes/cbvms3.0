from datetime import timedelta
from core.discipline import utc_now
"""Real Tk widgets and temporary records; camera and model access is unnecessary."""
import gc
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import customtkinter as ctk

from database.db_manager import CBVMSDatabase
from ui.dashboard import CBVMSDashboard
from ui.enrollment import EnrollmentPanel
from ui.suspensions_panel import (
    SuspensionsPanel, ALL_YEARS, ALL_COURSES, YEAR_LEVELS, NO_STUDENT_MATCHES, SELECT_STUDENT,
)


class SuspensionsUITests(unittest.TestCase):
    SID = "2023-00883"
    OTHER = "2023-883"

    def setUp(self):
        # Finalize fonts from previous destroyed Tk roots on Tk's thread, not
        # during a later database worker's incidental cyclic collection.
        gc.collect()
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CBVMSDatabase(Path(self.tmp.name) / "ui.db")
        self.db.initialize()
        for sid, name in ((self.SID, "First Student"), (self.OTHER, "Second Student")):
            self.db.insert_student(sid, name, "BSIT", "3A", b"", b"")
        self.vid = self.db.log_violation(self.SID, "First Student", "wrong_uniform", status="confirmed")
        self.db.process_expired_deadlines(now=utc_now()+timedelta(days=6))
        self.panel = SuspensionsPanel(self.root, database=self.db, username="osa.tester")
        self.addCleanup(self.panel.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def wait_loaded(self):
        deadline = time.monotonic() + 5
        while self.panel._loading and time.monotonic() < deadline:
            self.root.after(20,self.root.quit)
            self.root.mainloop()
        self.root.update()
        self.assertFalse(self.panel._loading, "Student data load did not finish")
        self.assertEqual(self.errors, [])

    def select(self, sid):
        self.panel.select_student(sid)
        self.wait_loaded()

    def test_selection_switch_filter_and_failed_load_clear_previous_data(self):
        self.select(self.SID)
        self.assertIn(self.SID, self.panel._identity.cget("text"))
        self.assertIn("1/3", self.panel._strike_summary.cget("text"))
        self.assertEqual(len(self.panel._violation_tree.get_children()), 1)
        self.assertIn("Counts this semester", self.panel._violation_tree.item(str(self.vid), "values"))
        self.panel._lift_reason.insert(0, "First student's draft")
        self.panel.select_student(self.OTHER)
        self.assertEqual(self.panel._violation_tree.get_children(), ())
        self.assertEqual(self.panel._lift_reason.get(), "")
        self.assertEqual(self.panel._lift_reason.cget("state"), "disabled")
        self.wait_loaded()
        self.assertIn(self.OTHER, self.panel._identity.cget("text"))
        self.assertIn("0/3", self.panel._strike_summary.cget("text"))
        self.assertEqual(self.panel._violation_empty.cget("text"), "No violation history for this student.")
        self.panel._search_var.set("no-match")
        self.assertIsNone(self.panel.student_id)
        self.assertEqual(self.panel._strike_summary.cget("text"), "")
        self.assertEqual(self.panel._message.cget("text"), NO_STUDENT_MATCHES)
        self.panel.refresh()
        self.wait_loaded()
        self.assertEqual(self.panel._message.cget("text"), NO_STUDENT_MATCHES)
        self.panel.on_show(self.SID)
        self.wait_loaded()
        self.assertEqual(self.panel.student_id, self.SID)
        with patch.object(self.db, "get_discipline_history_for_student", side_effect=sqlite3.OperationalError("test read failure")):
            self.panel.refresh()
            self.wait_loaded()
        self.assertEqual(self.panel._violation_tree.get_children(), ())
        self.assertEqual(self.panel._suspension_status.cget("text"), "")
        self.assertEqual(self.panel._lift_reason.cget("state"), "disabled")
        self.assertIn("test read failure", self.panel._message.cget("text"))
        self.panel.refresh()
        self.wait_loaded()
        self.assertEqual(len(self.panel._violation_tree.get_children()), 1)

    def test_out_of_order_response_cannot_restore_another_student(self):
        # Simulate worker responses arriving in the reverse of selection order.
        with patch.object(self.panel, "_fetch"):
            self.panel.select_student(self.SID)
            old_generation = self.panel._generation
            self.panel.select_student(self.OTHER)
            latest = self.panel._generation
        self.panel._fetch(latest, self.OTHER)
        self.panel._fetch(old_generation, self.SID)
        self.wait_loaded()
        self.assertEqual(self.panel.student_id, self.OTHER)
        self.assertIn("Second Student", self.panel._identity.cget("text"))
        self.assertEqual(self.panel._violation_tree.get_children(), ())

    def test_automatic_policy_replaces_manage_tab(self):
        self.select(self.SID)
        self.assertNotIn('Manage Suspension', self.panel._tabs._tab_dict)
        self.assertIn('3 strikes = 2 days', self.panel._explanation.cget('text'))
        for _ in range(6):
            self.db.log_violation(self.SID, 'First Student', 'wrong_uniform', status='confirmed')
        self.db.process_expired_deadlines(now=utc_now()+timedelta(days=6))
        self.panel.refresh()
        self.wait_loaded()
        self.assertEqual(len(self.panel._history_tree.get_children()), 3)
        self.assertIn('PARENT CALL REQUIRED', self.panel._explanation.cget('text'))


    def test_student_record_shortcut_carries_string_id_and_details_are_separate(self):
        callback = MagicMock(side_effect=self.panel.on_show)
        enrollment = EnrollmentPanel(self.root, self.db, MagicMock(), lambda: None,
                                     on_open_suspensions=callback)
        self.addCleanup(enrollment.destroy)
        pk = self.db.get_student_by_student_id(self.SID)["id"]
        deadline = time.monotonic() + 5
        while enrollment._list_task.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(enrollment._list_task.busy)
        enrollment._tree.selection_set(str(pk))
        enrollment._on_row_select()
        self.assertEqual(enrollment._details_btn.cget("text"), "Edit Details")
        with patch("ui.enrollment.open_student_details") as details:
            enrollment._details_btn.invoke()
            self.assertEqual(details.call_args.args[2], self.SID)
        enrollment._suspensions_btn.invoke()
        self.wait_loaded()
        callback.assert_called_once_with(self.SID)
        self.assertEqual(self.panel.student_id, self.SID)

    def test_actual_sidebar_button_switches_to_suspensions(self):
        # Build the real sidebar and navigation using a lightweight Tk host.
        root = self.root
        root.username = "osa.tester"
        root._active_nav = "live"
        root._invalidate_monitor = MagicMock()
        root._open_alerts_from_bell = lambda: None
        root._logout = lambda: None
        root._on_nav_select = lambda key: CBVMSDashboard._on_nav_select(root, key)
        root._enrollment_panel = root._training_panel = root._settings_panel = None
        root._suspensions_panel = self.panel
        root._suspension_student_id = None
        root._views = {"suspensions": self.panel}
        root._center_title = MagicMock()
        root._view_host = MagicMock()
        root._fade_transition = lambda callback: callback()
        root._is_superadmin = False
        CBVMSDashboard._build_left_sidebar(root)
        self.assertIn("Suspensions", root._nav_buttons["suspensions"].cget("text"))
        root._nav_buttons["suspensions"].invoke()
        self.wait_loaded()
        self.assertEqual(root._active_nav, "suspensions")
        root._invalidate_monitor.assert_called_once()
        CBVMSDashboard._open_student_suspensions(root, self.SID)
        self.wait_loaded()
        self.assertEqual(self.panel.student_id, self.SID)
        self.assertIn(self.SID, self.panel._identity.cget("text"))

    def test_visible_layout_resizes_and_keeps_actions_reachable(self):
        def settle():
            # CTkTabview.set schedules old-tab removal after 100 ms. Let that
            # transition finish before checking mapping or scrolling geometry.
            self.root.after(160, self.root.quit)
            self.root.mainloop()
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.panel.grid(row=0, column=0, sticky="nsew")
        self.root.geometry("720x720")
        self.root.deiconify()
        self.select(self.SID)
        self.panel._violation_tree.selection_set(str(self.vid))
        self.panel._on_violation_select()
        for width in (720, 1000):
            self.root.geometry(f"{width}x720")
            settle()
            controls = (self.panel._year_filter, self.panel._course_filter, self.panel._clear_filters_btn)
            for left, right in zip(controls, controls[1:]):
                self.assertLessEqual(left.winfo_rootx() + left.winfo_width(), right.winfo_rootx())
            self.assertLessEqual(controls[-1].winfo_rootx() + controls[-1].winfo_width(),
                                 self.root.winfo_rootx() + self.root.winfo_width())
            self.assertLessEqual(controls[0].winfo_rooty() + controls[0].winfo_height(),
                                 self.panel._student_tree.winfo_rooty())
            for tab in ("Violation History", "Suspension History"):
                self.panel._tabs.set(tab)
                settle()
                self.assertLessEqual(self.panel._message.winfo_rooty() + self.panel._message.winfo_height(),
                                     self.root.winfo_rooty() + self.root.winfo_height())
                footer = {"Violation History": self.panel._violation_detail,
                          "Suspension History": self.panel._lift_btn}.get(tab)
                if footer is not None:
                    self.assertTrue(footer.winfo_ismapped())
                    self.assertLessEqual(footer.winfo_rooty() + footer.winfo_height(),
                                         self.panel._tabs.winfo_rooty() + self.panel._tabs.winfo_height())
        self.assertEqual(self.errors, [])

    def seed_filter_students(self):
        for sid, name, year, course in (
            ("0001-00001", "Alice One", "1", "BSCS"),
            ("0001-00002", "Alice First", "First Year", "bscs"),
            ("0002-00003", "Beta Two", "2ND YEAR - A", "BSIT"),
            ("0002-00004", "Beta Second", "2", "Nursing"),
            ("0003-00005", "Gamma Third", "Third Year", "BSCS"),
            ("0004-00006", "Dana Four", "4A", "BSIT"),
            ("0004-00007", "Dana Fourth", "4th Year", "Nursing"),
            ("0001-00008", "Missing Year", "", "BSIT"),
            ("0002-00009", "Missing Course", "2A", ""),
            ("0004-00010", "Missing Both", "", ""),
            ("missing-null", "Null Values", "", ""),
            ("unrecognized", "Unrecognized Year", "Unknown", "BSIT"),
        ):
            with self.db.connect() as conn:
                conn.execute('INSERT INTO students(student_id,name,course,year_and_section) VALUES(?,?,?,?)',
                             (sid, name, course, year))
        with self.db.connect() as conn:
            conn.execute("UPDATE students SET course=NULL, year_and_section=NULL WHERE student_id='missing-null'")
            conn.execute("UPDATE students SET course='  bscs  ' WHERE student_id='0001-00002'")
        self.panel.refresh()
        self.wait_loaded()

    def shown_students(self):
        return set(self.panel._student_tree.get_children())

    def test_each_year_course_and_all_combinations_use_saved_fields(self):
        self.seed_filter_students()
        all_students = self.shown_students()
        self.assertEqual(len(all_students), 14)
        self.assertEqual(self.panel._year_var.get(), ALL_YEARS)
        self.assertEqual(self.panel._course_var.get(), ALL_COURSES)
        self.assertEqual(self.panel._course_filter.cget("values"), [ALL_COURSES, "Computer Science", "Information Technology", "Nursing", "Unspecified/Needs review"])
        self.assertEqual(self.panel._student_tree.item("0001-00001", "values"),
                         ("Alice One", "0001-00001", "1st Year", "Computer Science"))
        self.assertEqual(self.panel._student_tree.item("missing-null", "values")[-2:], ("—", "Unspecified/Needs review"))
        years = {
            ALL_YEARS: all_students,
            "1st Year": {"0001-00001", "0001-00002"},
            "2nd Year": {"0002-00003", "0002-00004", "0002-00009"},
            "3rd Year": {self.SID, self.OTHER, "0003-00005"},
            "4th Year": {"0004-00006", "0004-00007"},
        }
        courses = {
            ALL_COURSES: all_students,
            "Computer Science": {"0001-00001", "0001-00002", "0003-00005"},
            "Information Technology": {self.SID, self.OTHER, "0002-00003", "0004-00006", "0001-00008", "unrecognized"},
            "Nursing": {"0002-00004", "0004-00007"},
        }
        # No Refresh: every dropdown change must filter the original complete list.
        for year, year_ids in years.items():
            for course, course_ids in courses.items():
                with self.subTest(year=year, course=course):
                    self.panel._year_var.set(year)
                    self.panel._course_var.set(course)
                    expected = year_ids & course_ids
                    self.assertEqual(self.shown_students(), expected)
                    self.assertEqual(self.panel._result_count.cget("text"), f"{len(expected)} of 14 students")

    def test_filters_combine_with_name_and_exact_string_id_search_and_clear(self):
        self.seed_filter_students()
        self.panel._year_var.set("3rd Year")
        self.panel._course_var.set("Information Technology")
        for search, expected in (("FIRST student", {self.SID}), (self.SID, {self.SID}),
                                 (self.OTHER, {self.OTHER}), ("0003-00005", set()), ("no-match", set())):
            self.panel._search_var.set(search)
            self.assertEqual(self.shown_students(), expected)
        self.assertEqual(self.panel._message.cget("text"), NO_STUDENT_MATCHES)
        self.panel._clear_filters_btn.invoke()
        self.assertEqual(self.panel._search_var.get(), "")
        self.assertEqual(self.panel._year_var.get(), ALL_YEARS)
        self.assertEqual(self.panel._course_var.get(), ALL_COURSES)
        self.assertEqual(len(self.shown_students()), 14)

    def test_refresh_preserves_filters_selection_and_updates_course_options(self):
        self.select(self.SID)
        self.panel._year_var.set("3rd Year")
        self.panel._course_var.set("Information Technology")
        self.panel._search_var.set("First")
        self.db.insert_student("0003-00100", "First Transfer", "BSIT", "Third Year", b"", b"")
        self.db.insert_student("0004-00101", "New Course", "Computer Engineering", "4A", b"", b"")
        self.panel.refresh()
        self.wait_loaded()
        self.assertEqual((self.panel._year_var.get(), self.panel._course_var.get(), self.panel._search_var.get()),
                         ("3rd Year", "Information Technology", "First"))
        self.assertEqual(self.shown_students(), {self.SID, "0003-00100"})
        self.assertEqual(self.panel.student_id, self.SID)
        self.assertEqual(self.panel._student_tree.selection(), (self.SID,))
        self.assertIn("Computer Engineering", self.panel._course_filter.cget("values"))
        self.assertEqual(self.panel._lift_reason.cget("state"), "normal")
        # A saved year change on Refresh also excludes and clears the selection.
        with self.db.connect() as conn:
            conn.execute("UPDATE students SET year_and_section='4th Year - A', report_year_level='4th Year', report_section='A' WHERE student_id=?", (self.SID,))
        self.panel.refresh()
        self.wait_loaded()
        self.assertIsNone(self.panel.student_id)
        self.assertEqual(self.shown_students(), {"0003-00100"})
        self.assertEqual(self.panel._identity.cget("text"), SELECT_STUDENT)
        self.assertEqual(self.panel._lift_reason.cget("state"), "disabled")

    def test_filter_exclusion_clears_history_and_form_without_database_writes(self):
        self.select(self.SID)
        self.panel._lift_reason.insert(0, "Draft for the first student")
        self.panel._year_var.set("3rd Year")
        self.panel._course_var.set("Information Technology")
        self.assertEqual(self.panel.student_id, self.SID)
        self.assertEqual(self.panel._lift_reason.get(), "Draft for the first student")
        with self.db.connect() as conn:
            before = list(conn.iterdump())
        with patch.object(self.panel, "refresh") as refresh:
            for year in YEAR_LEVELS:
                self.panel._year_var.set(year)
            self.panel._clear_filters_btn.invoke()
            refresh.assert_not_called()
        self.assertIsNone(self.panel.student_id)
        self.assertEqual(self.panel._student_tree.selection(), ())
        self.assertEqual(self.panel._identity.cget("text"), SELECT_STUDENT)
        self.assertEqual(self.panel._violation_tree.get_children(), ())
        self.assertEqual(self.panel._history_tree.get_children(), ())
        self.assertEqual(self.panel._lift_reason.get(), "")
        self.assertEqual(self.panel._lift_reason.cget("state"), "disabled")
        self.assertEqual(self.panel._lift_btn.cget("state"), "disabled")
        with self.db.connect() as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_filter_during_reload_keeps_fresh_directory_and_discards_excluded_details(self):
        self.select(self.SID)
        with patch.object(self.panel, "_fetch"):
            self.panel.refresh()
            generation = self.panel._generation
            self.panel._year_var.set("4th Year")
        self.db.insert_student("0004-00999", "New Fourth Year", "BSIT", "Fourth Year", b"", b"")
        self.panel._fetch(generation, self.SID)
        self.wait_loaded()
        self.assertEqual(self.shown_students(), {"0004-00999"})
        self.assertIsNone(self.panel.student_id)
        self.assertEqual(self.panel._identity.cget("text"), SELECT_STUDENT)
        self.assertEqual(self.panel._violation_tree.get_children(), ())
        self.assertEqual(self.panel._lift_reason.cget("state"), "disabled")


if __name__ == "__main__":
    unittest.main()
