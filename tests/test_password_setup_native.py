"""Native Tk event-loop tests, always using disposable student records."""
import gc
import tempfile
import threading

import customtkinter as ctk
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from auth.auth_manager import AuthManager
from auth.login import CBVMSLoginWindow
from database.db_manager import CBVMSDatabase
from database.student_credentials import SessionExpired
from ui.student_portal import StudentPortal


class PasswordSetupNativeTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CBVMSDatabase(Path(self.tmp.name) / 'native.db')
        self.db.initialize(process_deadlines=False)
        self.db.insert_student('S1', 'Native Student', 'BSIT', '1A', b'', b'', account_password='RandomTemp42')
        self.auth = AuthManager(self.db)
        self.errors = []
        self.app = None
        self.open_login()

    def open_login(self):
        self.app = CBVMSLoginWindow(self.auth)
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.app._intro.finish()
        self.app.deiconify()
        self.pump()

    def tearDown(self):
        if self.app and not getattr(self.app, '_destroying', False):
            self.app._on_close()
        self.app = None
        gc.collect()
        self.assertEqual(self.errors, [])
        self.tmp.cleanup()

    def pump(self, seconds=.03):
        if getattr(self.app, '_destroying', False):
            return
        self.app.after(int(seconds * 1000), self.app.quit)
        self.app.mainloop()

    def until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump()
        self.assertTrue(predicate(), 'Native transition did not finish')

    def login(self, password='RandomTemp42'):
        self.app.username_entry.delete(0, 'end')
        self.app.username_entry.insert(0, 'S1')
        self.app.password_entry.insert(0, password)
        self.app.login_btn.invoke()
        self.until(lambda: self.app._pending is not None or self.app.result is not None or not self.app._signing_in)

    def fill(self, new='Native personal passphrase', confirmation=None):
        for entry, value in zip(self.app.setup_entries, (new, new if confirmation is None else confirmation)):
            entry.delete(0, 'end')
            entry.insert(0, value)

    def test_setup_only_after_authentication_and_premature_welcome_blocked(self):
        self.login('incorrect')
        self.assertIsNone(self.app._pending)
        self.assertIsNone(self.app.result)
        self.login()
        self.assertIsNone(self.app.result)
        self.until(lambda: self.app._setup_panel.winfo_ismapped())
        self.app._show_welcome('Premature')
        self.pump(.8)
        self.assertIsNone(self.app._welcome_job)
        self.assertFalse(getattr(self.app, '_destroying', False))
        with self.assertRaises(SessionExpired):
            StudentPortal(student_id='S1', display_name='Invalid', database=self.db,
                          session_token=self.app._pending['session_token'])
        # Layout checks at the fixed window size: both actions remain visible.
        self.assertLess(self.app.save_password_btn.winfo_rooty() + self.app.save_password_btn.winfo_height(),
                        self.app.winfo_rooty() + self.app.winfo_height() - 35)

    def test_validation_double_click_commit_and_portal_navigation(self):
        self.login()
        self.fill('short')
        self.app.save_password_btn.invoke()
        self.assertIn('eight', self.app.setup_error.cget('text'))
        self.fill('long password', 'different')
        self.app.save_password_btn.invoke()
        self.assertIn('match', self.app.setup_error.cget('text'))
        self.fill('RandomTemp42')
        self.app.save_password_btn.invoke()
        self.until(lambda: self.app._operation is None)
        self.assertIn('different', self.app.setup_error.cget('text'))
        self.fill()
        self.app.save_password_btn.invoke()
        generation = self.app._operation_generation
        self.app._save_setup_password()
        self.assertEqual(generation, self.app._operation_generation)
        self.until(lambda: getattr(self.app, '_destroying', False))
        result = self.app.result
        self.assertFalse(result['must_change_password'])
        self.app = None
        gc.collect()
        self.app = StudentPortal(student_id='S1', display_name='Native Student', database=self.db,
                                 session_token=result['session_token'])
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.until(lambda: self.app._request is None)
        self.assertIn(self.app._page_state, ('loaded', 'empty'))
        self.app._nav_btns['settings'].invoke()
        self.until(lambda: self.app._request is None)
        self.assertEqual(self.app._active, 'settings')
        self.app._on_close()
        self.assertTrue(self.app._refresh.done.wait(3))
        self.assertIsNone(self.auth.authenticate('S1', 'RandomTemp42'))
        self.assertFalse(self.auth.authenticate('S1', 'Native personal passphrase')['must_change_password'])

    def test_back_close_reopen_discard_restricted_state(self):
        self.login()
        old = self.app._pending['session_token']
        self.app._back_to_login()
        self.assertIsNone(self.app.result)
        self.assertIsNone(self.app._pending)
        self.pump(.2)
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(old)
        self.login()
        self.app._on_close()
        self.assertIsNone(self.app.result)
        self.app = None
        gc.collect()
        self.open_login()
        self.login()
        self.assertTrue(self.app._pending['must_change_password'])

    def test_reset_with_setup_open_cannot_overwrite(self):
        self.login()
        self.db.reset_student_password('S1', 'Newer admin temporary')
        self.fill()
        self.app.save_password_btn.invoke()
        self.until(lambda: self.app._operation is None)
        self.assertIsNone(self.app.result)
        self.assertIn('sign in again', self.app.setup_error.cget('text'))
        self.assertIsNotNone(self.auth.authenticate('S1', 'Newer admin temporary'))

    def test_save_failure_stays_on_screen_then_retry(self):
        self.login()
        self.fill()
        with patch.object(CBVMSDatabase, 'change_student_password', side_effect=OSError('disk unavailable')):
            self.app.save_password_btn.invoke()
            self.until(lambda: self.app._operation is None)
        self.assertIsNone(self.app.result)
        self.assertIsNotNone(self.app._pending)
        self.assertIn('try again', self.app.setup_error.cget('text'))
        self.app.save_password_btn.invoke()
        self.until(lambda: getattr(self.app, '_destroying', False))
        self.assertFalse(self.app.result['must_change_password'])

    def test_reset_during_welcome_returns_to_login(self):
        pending = self.auth.authenticate('S1', 'RandomTemp42')
        self.db.change_student_password(pending['session_token'], 'Completed password', 'Completed password')
        self.login('Completed password')
        self.assertIsNotNone(self.app.result)
        self.db.reset_student_password('S1', 'Reset in welcome')
        self.until(lambda: self.app.result is None and not self.app._signing_in)
        self.assertFalse(getattr(self.app, '_destroying', False))
        self.assertIn('sign in again', self.app.error_label.cget('text'))

    def test_cancel_during_save_discards_late_completion(self):
        self.login()
        self.fill()
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = CBVMSDatabase.change_student_password
        def slow(db, *args, **kwargs):
            started.set()
            release.wait(3)
            try:
                return original(db, *args, **kwargs)
            finally:
                finished.set()
        with patch.object(CBVMSDatabase, 'change_student_password', slow):
            self.app.save_password_btn.invoke()
            self.assertTrue(started.wait(1))
            self.app._back_to_login()
            release.set()
            self.until(finished.is_set)
        self.assertIsNone(self.app.result)
        self.assertIsNone(self.app._pending)
        self.assertTrue(self.auth.authenticate('S1', 'RandomTemp42')['must_change_password'])

    def test_slow_save_recovers_and_cancels_worker(self):
        self.login()
        self.fill()
        release, finished = threading.Event(), threading.Event()
        original = CBVMSDatabase.change_student_password
        def slow(db, *args, **kwargs):
            release.wait(3)
            try:
                return original(db, *args, **kwargs)
            finally:
                finished.set()
        self.app.OPERATION_TIMEOUT = .05
        with patch.object(CBVMSDatabase, 'change_student_password', slow):
            self.app.save_password_btn.invoke()
            self.until(lambda: self.app._operation is None)
            self.assertIn('too long', self.app.setup_error.cget('text'))
            release.set()
            self.until(finished.is_set)
        self.assertIsNone(self.app.result)
        self.assertEqual(self.app.save_password_btn.cget('state'), 'normal')
        self.assertTrue(self.auth.authenticate('S1', 'RandomTemp42')['must_change_password'])

    def test_profile_password_change_and_live_reset_revocation(self):
        session = self.auth.authenticate('S1', 'RandomTemp42')
        session = self.db.change_student_password(session['session_token'], 'First personal password', 'First personal password')
        self.app._on_close()
        self.app = None
        gc.collect()
        self.app = StudentPortal(student_id='S1', display_name='Student', database=self.db,
                                 session_token=session['session_token'])
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.until(lambda: self.app._request is None)
        self.app._nav_btns['profile'].invoke()
        self.until(lambda: self.app._request is None)
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)
        fields = [w for w in widgets(self.app._page_body) if isinstance(w, ctk.CTkEntry) and w.cget('show')]
        self.assertEqual(len(fields), 3)
        for field, value in zip(fields, ('First personal password', 'Second personal password', 'Second personal password')):
            field.insert(0, value)
        button = next(w for w in widgets(self.app._page_body) if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Save Password')
        button.invoke()
        self.until(lambda: self.app._action_request is None)
        self.assertNotEqual(self.app._session_ref['token'], session['session_token'])
        self.assertFalse(self.auth.authenticate('S1', 'Second personal password')['must_change_password'])
        self.app._nav_btns['notifications'].invoke()
        self.until(lambda: self.app._request is None)
        self.assertIn(self.app._page_state, ('loaded', 'empty'))
        self.db.reset_student_password('S1', 'Administrator new temporary')
        self.until(lambda: getattr(self.app, '_destroying', False), timeout=6)
        self.assertTrue(self.app.logged_out)
        self.assertTrue(self.app._refresh.done.wait(3))

    def test_administrator_login_keeps_normal_welcome(self):
        self.app.username_entry.insert(0, 'admin')
        self.app.password_entry.insert(0, 'admin123')
        self.app.login_btn.invoke()
        self.until(lambda: getattr(self.app, '_destroying', False))
        self.assertEqual(self.app.result['role'], 'admin')
        self.assertIsNone(self.app._pending)

    def test_account_manager_reset_requires_setup_and_prevents_duplicate_click(self):
        from ui.account_manager import AccountManagerPanel
        session = self.auth.authenticate('S1', 'RandomTemp42')
        full = self.db.change_student_password(session['session_token'], 'Personal before reset', 'Personal before reset')
        panel = AccountManagerPanel(self.app, database=self.db)
        panel._selected_account = panel._accounts[0]
        panel._pw_entry.insert(0, 'Admin issued temporary')
        panel._pw_confirm.insert(0, 'Admin issued temporary')
        panel._do_reset()
        panel._do_reset()
        self.until(lambda: not panel._resetting)
        self.assertIn('next login', panel._status_lbl.cget('text'))
        with self.assertRaises(SessionExpired):
            self.db.get_student_session(full['session_token'])
        self.assertTrue(self.auth.authenticate('S1', 'Admin issued temporary')['must_change_password'])
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT credential_version FROM student_accounts').fetchone()[0], 3)
        panel.destroy()
