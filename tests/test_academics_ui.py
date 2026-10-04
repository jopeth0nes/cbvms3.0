"""Real Tk dependency, edit, stale-response and off-thread registration checks."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import customtkinter as ctk
from core.academics import COLLEGES, COURSES, SELECT_COLLEGE, SELECT_COURSE, courses_for
from core.face_capture import CaptureSession, FaceCapture
from database.db_manager import CBVMSDatabase
from ui.academic_fields import AcademicFields
from ui.background_task import BackgroundTask
from auth.register import StudentRegistrationWindow
from tests.test_face_capture import sample, face


class AcademicUITests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'ui.db')
        self.db.initialize(process_deadlines=False)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        for job in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.tk.call('after', 'cancel', job)
        self.root.destroy()
        self.tmp.cleanup()
        self.assertEqual(self.errors, [])

    def until(self, predicate):
        end = time.monotonic() + 5
        while not predicate() and time.monotonic() < end:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_every_dependent_option_and_college_change(self):
        form = AcademicFields(self.root)
        self.assertEqual(form.college.get(), SELECT_COLLEGE)
        self.assertEqual(form.course.get(), SELECT_COURSE)
        self.assertEqual(form.course.cget('state'), 'disabled')
        for college in COLLEGES:
            form.college.set(college)
            form.change_college(college)
            self.assertEqual(form.course.get(), SELECT_COURSE)
            self.assertEqual(form.course.cget('values'), [SELECT_COURSE, *courses_for(college)])
            self.assertEqual(form.course.cget('state'), 'normal')
            for course in courses_for(college):
                form.course.set(course)
                form.year.set('Not applicable')
                self.assertEqual(form.values()['course'], course)
            form.change_college(college)
            self.assertEqual(form.course.get(), courses_for(college)[-1])
        form.college.set(SELECT_COLLEGE)
        form.change_college(SELECT_COLLEGE)
        self.assertEqual(form.course.cget('state'), 'disabled')
        with self.assertRaises(ValueError):
            form.values()

    def test_unchanged_legacy_is_editable_and_changed_pair_is_validated(self):
        form = AcademicFields(self.root, dict(course='Mystery', college_department='Former college', year_and_section='unknown'))
        self.assertIsNone(form.changes())
        form.college.set(COLLEGES[0])
        form.change_college(COLLEGES[0])
        with self.assertRaises(ValueError):
            form.changes()
        form.course.set('Information Technology')
        form.year.set('7')
        self.assertEqual(form.changes()['report_year_level'], '7th Year')

    def test_worker_is_bounded_keeps_tk_live_and_discards_destroyed_view_result(self):
        owner = ctk.CTkFrame(self.root)
        worker = BackgroundTask(owner)
        release, entered = threading.Event(), threading.Event()
        result, errors = [], []
        def operation():
            self.assertNotEqual(threading.get_ident(), main_thread)
            entered.set()
            release.wait(3)
            return 1
        main_thread = threading.get_ident()
        worker.run(operation, result.append, errors.append)
        self.until(entered.is_set)
        self.assertFalse(worker.run(lambda: 2, result.append, errors.append))
        heartbeat = []
        self.root.after(1, lambda: heartbeat.append(True))
        self.until(lambda: heartbeat)
        owner.destroy()
        release.set()
        self.root.after(100, lambda: heartbeat.append(True))
        self.until(lambda: len(heartbeat) == 2)
        self.assertEqual(result, [])
        self.assertIn('still running', errors[0])

    def test_registration_saves_academics_frozen_face_account_off_tk(self):
        win = ctk.CTkToplevel(self.root)
        win.withdraw()
        holder = ctk.CTkFrame(win)
        StudentRegistrationWindow._build_form(win, holder)
        for key, value in dict(name='Student', username='new-user', password='fixture-password', sid='0001-02/A').items():
            getattr(win, '_e_' + key).insert(0, value)
        win._academics.college.set(COLLEGES[0])
        win._academics.change_college(COLLEGES[0])
        win._academics.course.set('Information Technology')
        win._academics.year.set('Not applicable')
        win.database = self.db
        win._submitting, win._alive = False, True
        win._recognizer = MagicMock()
        win._set_err = MagicMock()
        win._cap_btn = ctk.CTkButton(win)
        win._on_success = MagicMock()
        win._capture_key = StudentRegistrationWindow._capture_key.__get__(win)
        win._capture_session = CaptureSession(win._capture_key())
        frozen = FaceCapture.freeze(win._capture_key(), sample(3), face())
        win._capture_session.frozen = frozen
        win._captured_frame = frozen.frame
        main_thread, saved_threads = threading.get_ident(), []
        original = self.db.insert_student
        def insert(**kwargs):
            saved_threads.append(threading.get_ident())
            return original(**kwargs)
        self.db.insert_student = insert
        StudentRegistrationWindow._submit(win)
        self.until(lambda: win._on_success.called)
        row = self.db.get_student_by_student_id('0001-02/A')
        self.assertEqual(row['college_department'], COLLEGES[0])
        self.assertEqual(row['course'], 'Information Technology')
        self.assertEqual(row['photo'], frozen.photo)
        self.assertTrue(row['encoding'])
        self.assertEqual(row['registration_pending'], 1)
        self.assertIsNotNone(self.db.verify_student_account('new-user', 'fixture-password'))
        self.assertEqual(len(saved_threads), 1)
        self.assertNotEqual(saved_threads[0], main_thread)
        StudentRegistrationWindow._submit(win)
        self.assertEqual(len(saved_threads), 1)
        win.destroy()

    def test_reports_import_validates_off_tk_and_retry_updates_catalog_filters(self):
        from ui.reports_panel import ReportsPanel
        from core.report_csv import write_csv, ROSTER
        panel = ReportsPanel(self.root, database=self.db)
        panel.refresh()
        self.until(lambda: not panel.task.busy)
        path = Path(self.tmp.name) / 'roster.csv'
        good = dict(zip(ROSTER, ['0001/A', 'Name', *COURSES['it'], 'A', 'Not applicable']))
        bad = dict(good, student_id='0002/A', college_department=COLLEGES[1])
        write_csv(path, 'Roster', [good, bad])
        from core.report_csv import read_csv
        threads = []
        def read(*args):
            threads.append(threading.get_ident())
            return read_csv(*args)
        with patch('ui.reports_panel.filedialog.askopenfilename', return_value=str(path)), \
                patch('ui.reports_panel.messagebox.askyesno', return_value=True), \
                patch('ui.reports_panel.read_csv', side_effect=read):
            panel.import_csv()
            self.until(lambda: not panel.task.busy)
            self.assertIn('Line 3:', panel.message.cget('text'))
            self.assertEqual(self.db.get_all_students(), [])
            write_csv(path, 'Roster', [good])
            panel.import_csv()
            self.until(lambda: len(panel.rows) == 1 and not panel.task.busy)
        self.assertTrue(all(t != threading.get_ident() for t in threads))
        panel.filters['college_department'].set(COLLEGES[0])
        panel.filter_rows()
        self.assertEqual(panel.menus['course'].cget('values'), ['All', *courses_for(COLLEGES[0])])
        panel.filters['college_department'].set(COLLEGES[1])
        panel.filter_rows()
        self.assertEqual(panel.filtered, [])
        panel.destroy()



if __name__ == '__main__':
    unittest.main()
