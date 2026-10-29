"""Exact log actions through native widgets, disposable data only."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests import test_attendance_ui as fixtures
from ui.violation_log import ViolationLogPanel


class ActivityLogNavigationTests(unittest.TestCase):
    setUp = fixtures.AttendanceUITests.setUp
    tearDown = fixtures.AttendanceUITests.tearDown
    until = fixtures.AttendanceUITests.until

    def test_exact_attendance_and_unknown_select_despite_filters_and_busy_initial_read(self):
        panel = self.panel
        panel.filters['start'].set('invalid-date')
        panel.filters['student_id'].set('unrelated')
        # Initial constructor read may still be running; latest generation wins.
        panel.open_exact_event(1)
        self.until(lambda: not panel.task.busy and panel._pending_exact_event_id is None)
        self.assertEqual(panel.tree.selection(), ('1',))
        self.assertEqual([row['id'] for row in panel.rows], [1])
        first = self.db.log_security_event('synthetic-1', event_key='test-unknown-1')
        second = self.db.log_security_event('synthetic-2', event_key='test-unknown-2')
        panel.filters['semester_id'].set('unrelated')
        panel.open_exact_unknown(first)
        panel.open_exact_unknown(second)
        self.until(lambda: not panel.task.busy and panel._pending_exact_unknown_id is None)
        self.assertEqual(panel.tree.selection(), (str(second),))
        self.assertEqual([row['id'] for row in panel.rows], [second])
        # Ordinary filtering is restored after the one-shot exact request.
        panel.filters['search'].set('no-match')
        panel.refresh()
        self.until(lambda: not panel.task.busy)
        self.assertEqual(panel.rows, [])

    def test_violation_exact_lookup_is_one_shot_and_restores_filters(self):
        # Verify the actual page query used by the native destination, without
        # publishing a violation or running its deadline/notification actions.
        with self.db.connect() as conn:
            conn.execute("INSERT INTO violations(student_id,student_name,violation_type,timestamp,status) VALUES(?,?,?,?,?)",
                         ('0001-A', 'Synthetic', 'wrong_uniform', '2026-10-04T00:00:00+00:00', 'pending_review'))
            event_id = conn.execute('SELECT MAX(id) FROM violations').fetchone()[0]
        panel = SimpleNamespace(database=self.db, username='admin',
            _pending_exact_violation_id=event_id, _selected_violation_id=event_id,
            _filters_to_where=MagicMock(return_value=('WHERE v.id=-1', [])), _page=1)
        ViolationLogPanel._fetch_page(panel)
        self.assertEqual([row['id'] for row in panel._rows], [event_id])
        panel._filters_to_where.assert_not_called()
        ViolationLogPanel._fetch_page(panel)
        panel._filters_to_where.assert_called_once()
        self.assertEqual(panel._rows, [])
        self.db.student_session_ref = object()
        panel._pending_exact_violation_id = event_id
        with self.assertRaises(PermissionError):
            ViolationLogPanel._fetch_page(panel)
        self.db.student_session_ref = None

    def test_native_violation_selection_and_details_restore_ordinary_filters(self):
        with self.db.connect() as conn:
            event_id = conn.execute(
                "INSERT INTO violations(student_id,student_name,violation_type,timestamp,status) VALUES(?,?,?,?,?)",
                ('0001-A', 'Synthetic', 'wrong_uniform', '2026-10-04T00:00:00+00:00',
                 'pending_review')).lastrowid
        # Exercise real widgets and detail loading, suppressing unrelated deadline writes.
        with patch.object(self.db, 'process_expired_deadlines'):
            panel = ViolationLogPanel(self.root, database=self.db, username='admin')
            panel.grid(row=0, column=0, sticky='nsew')
            panel._search_var.set('no-match')
            panel._from_var.set('2099-01-01')
            self.until(lambda: not panel._tree.get_children())
            panel.open_exact_violation(event_id)
            self.root.update()
            self.assertEqual(panel._tree.selection(), (str(event_id),))
            self.assertEqual(panel._selected_violation_row['id'], event_id)
            self.assertEqual(panel._detail_name.cget('text'), 'Student: Synthetic')
            self.assertIsNone(panel._pending_exact_violation_id)
            panel.refresh()
            self.assertEqual(panel._tree.get_children(), ())
            self.assertIsNone(panel._selected_violation_row)
            panel.destroy()
