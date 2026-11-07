"""Native candidate workflow with disposable state; no model or camera jobs."""
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import Mock

from ui.uniform_candidate_panel import CandidateTrainingWindow
from ui.window_lifecycle import WorkspaceWindow


class CandidateUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = WorkspaceWindow()
        self.errors = []
        self.root.report_callback_exception = lambda *a: self.errors.append(a)
        self.service = Mock()
        self.service.summary.return_value = dict(included_by_category={'campus_uniform': 0,
            'organizational_shirt': 0, 'other_clothing': 0}, groups_by_category={}, pending=963,
            excluded=0, quality_blocked=0, eligible=False, reasons=['Campus Uniform needs reviewed examples.'])
        self.service.status.return_value = dict(run_id=None, status='idle', epoch=0, epochs=0,
                                                message='No candidate job exists.', resumable=False)
        self.service.is_busy.return_value = False
        self.service.list_runs.return_value = []
        self.busy = False
        self.view = CandidateTrainingWindow(self.root, self.service, self.tmp.name,
                                             training_busy=lambda: self.busy)
        self.wait()

    def tearDown(self):
        self.root.destroy()
        self.tmp.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self, ms=40):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def wait(self):
        end = time.monotonic() + 5
        while self.view.task.busy and time.monotonic() < end:
            self.pump()
        self.assertFalse(self.view.task.busy)

    def eligible(self):
        self.service.summary.return_value.update(eligible=True, reasons=[],
            included_by_category={c:10 for c in ('campus_uniform','organizational_shirt','other_clothing')})
        self.view.refresh()
        self.wait()
        self.view.ack.select()
        self.view._buttons()

    def test_missing_approvals_block_and_show_counts(self):
        self.assertIn('Pending: 963', self.view.coverage.cget('text'))
        self.assertIn('Training blocked', self.view.readiness.cget('text'))
        self.view.ack.select()
        self.view.start()
        self.service.start.assert_not_called()
        self.assertEqual(self.view.start_button.cget('state'), 'disabled')

    def test_start_off_tk_with_resource_ack_and_bounded_config(self):
        main = threading.get_ident()
        threads = []
        self.service.start.side_effect = lambda config: threads.append(threading.get_ident()) or 'a'*32
        self.eligible()
        self.view.ack.deselect()
        self.view.start()
        self.service.start.assert_not_called()
        self.view.ack.select()
        self.view.start()
        self.wait()
        self.service.start.assert_called_once_with(dict(epochs=5,batch=4,threads=2,min_per_class=10,synthetic_smoke=False))
        self.assertNotIn(main, threads)

    def test_other_training_blocks_start_and_resume(self):
        self.eligible()
        self.busy = True
        self.view.start()
        self.view.current.update(resumable=True)
        self.view.resume()
        self.service.start.assert_not_called()
        self.service.resume.assert_not_called()

    def test_cancel_progress_and_recover_resume(self):
        state = dict(run_id='a'*32,status='running',epoch=1,epochs=5,message='Epoch complete.',resumable=True)
        self.service.status.return_value = state
        self.view.refresh(); self.wait()
        self.assertEqual(self.view.cancel_button.cget('state'), 'normal')
        self.assertAlmostEqual(self.view.progress.get(), .2)
        self.view.cancel(); self.wait()
        self.service.cancel.assert_called_once_with('a'*32)
        state.update(status='cancelled')
        self.view.refresh(); self.wait()
        self.view.ack.select()
        self.view.resume(); self.wait()
        self.service.resume.assert_called_once_with('a'*32)
        self.assertIn(str(Path(self.tmp.name)/('a'*32)), self.view.artifact.cget('text'))

    def test_terminal_status_waits_for_worker_lock_release(self):
        self.eligible()
        self.service.status.return_value = dict(run_id='a'*32,status='cancelled',epoch=1,epochs=5,resumable=True)
        self.service.is_busy.return_value = True
        self.view.refresh(); self.wait()
        self.assertEqual(self.view.start_button.cget('state'), 'disabled')
        self.assertEqual(self.view.resume_button.cget('state'), 'disabled')
        self.service.is_busy.return_value = False
        self.view.refresh(); self.wait()
        self.assertEqual(self.view.resume_button.cget('state'), 'normal')

    def test_recover_selected_older_candidate(self):
        old = dict(run_id='b'*32,status='cancelled',epoch=1,epochs=5,message='Cancelled.',resumable=True)
        self.service.list_runs.return_value = [old]
        self.service.status.side_effect = lambda run_id=None: old if run_id else dict(run_id='a'*32,status='completed',epoch=5,epochs=5,resumable=False)
        self.view.refresh(); self.wait()
        self.view.select_run('b'*32 + ' • cancelled'); self.wait()
        self.view.ack.select()
        self.view.resume(); self.wait()
        self.service.resume.assert_called_once_with('b'*32)

    def test_failure_report_and_close_during_operation(self):
        self.eligible()
        self.service.start.side_effect = ValueError('Frozen input hash mismatch')
        self.view.start(); self.wait()
        self.assertIn('Frozen input hash mismatch', self.view.message.cget('text'))
        gate = threading.Event()
        done = threading.Event()
        self.service.start.side_effect = lambda _: (gate.wait(2), done.set())
        self.view.start()
        self.view.destroy()
        gate.set()
        self.assertTrue(done.wait(2))
        self.pump(100)
        self.service.cancel.assert_not_called()
