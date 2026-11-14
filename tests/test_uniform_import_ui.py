"""Real Tk import controls with disposable files, bounded worker gates and timers."""
import gc
import hashlib
import os
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import customtkinter as ctk
import numpy as np
from PIL import Image

from core.uniform_candidate import CandidateService
from core.uniform_dataset import UniformDataset
from ui.uniform_workspace import UniformWorkspace
from ui.window_lifecycle import WorkspaceWindow


class UniformImportUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp.name)
        self.dataset = UniformDataset(self.root_path / 'dataset', auto_process=False)
        self.inputs = self.root_path / 'inputs'
        self.inputs.mkdir()
        self.photos = []
        for index in range(5):
            path = self.inputs / f'photo-{index}.png'
            Image.fromarray(np.random.default_rng(index).integers(0, 256, (200, 200, 3), dtype=np.uint8)).save(path)
            self.photos.append(path)
        self.root = WorkspaceWindow()
        self.root.geometry('1150x900')
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.releases = []
        self.threads = []
        self.views = []
        self.view = self.open_view()

    def open_view(self):
        view = UniformWorkspace(self.root, capture=Mock(), dataset=self.dataset,
            candidate_service=CandidateService(self.dataset, self.root_path / 'candidates'))
        view.pack(fill='both', expand=True)
        self.views.append(view)
        self.wait(lambda: not view.task.busy)
        return view

    def tearDown(self):
        for release in self.releases:
            release.set()
        for view in self.views:
            if view.winfo_exists():
                view.destroy()
        from core.uniform_import import get_import_job
        job = get_import_job(self.dataset)
        if job:
            job.cancel()
            self.wait(lambda: not job.active)
        for thread in self.threads:
            thread.join(5)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self, ms=50):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump(25)
        self.assertTrue(predicate(), 'native import condition timed out')

    def finished(self):
        panel = self.view.import_progress
        def completed():
            if panel.job and panel.job.snapshot()['phase'] == 'waiting_duplicates':
                self.resolve_duplicates()
            return panel.job is not None and not panel.active and panel._poll_id is None
        self.wait(completed)
        self.wait(lambda: not self.view.task.busy)
        return panel.job.snapshot()

    def resolve_duplicates(self):
        panel = self.view.import_progress
        panel.review_duplicates()
        review = panel._review_window
        from ui.uniform_duplicate_review import _ACTIONS
        for row in panel.job.duplicate_rows():
            action = 'add_source' if row['match_type'] == 'exact' else row['default_action']
            label = dict(_ACTIONS[row['match_type']])[action]
            review._choose(row['row_id'], row['match_type'], label)
        review.continue_button.invoke()

    def button(self, title):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        tab = self.view.tabs.tab('Campus Uniform')
        return next(w for w in descendants(tab) if isinstance(w, ctk.CTkButton) and w.cget('text') == title)

    def launch(self, kind, source):
        title, picker = {'paths': ('Upload Photos', 'askopenfilenames'),
                         'folder': ('Import Folder', 'askdirectory'),
                         'zip': ('Import ZIP', 'askopenfilename')}[kind]
        self.wait(lambda: not self.view.task.busy)
        with patch('ui.uniform_workspace.filedialog.' + picker, return_value=source):
            self.button(title).invoke()
        self.assertIsNotNone(self.view.import_progress.job)

    def test_real_upload_folder_zip_controls_animate_and_report_counts(self):
        # Each route reads actual files/archive bytes; only the OS selection result is supplied.
        duplicate = self.inputs / 'copy.png'
        duplicate.write_bytes(self.photos[0].read_bytes())
        (self.inputs / 'broken.png').write_bytes(b'not a photo')
        (self.inputs / 'notes.txt').write_text('unsupported input')
        archive = self.root_path / 'photos.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            for path in self.inputs.iterdir():
                output.write(path, path.name)
        original = self.dataset._import_bytes
        main_thread = threading.get_ident()
        calls = []
        for kind, source in [('paths', tuple(str(p) for p in self.inputs.iterdir())),
                             ('folder', str(self.inputs)), ('zip', str(archive))]:
            with self.subTest(kind=kind):
                entered, release = threading.Event(), threading.Event()
                self.releases.append(release)
                def slow_save(*args, **kwargs):
                    calls.append(threading.get_ident())
                    entered.set()
                    release.wait(5)
                    return original(*args, **kwargs)
                with patch.object(self.dataset, '_import_bytes', side_effect=slow_save):
                    self.launch(kind, source)
                    self.assertTrue(self.view.import_progress.activity._loop_running)
                    self.wait(lambda: self.view.import_progress.job.snapshot()['phase'] == 'waiting_duplicates')
                    self.resolve_duplicates()
                    self.wait(entered.is_set)
                    panel = self.view.import_progress
                    self.pump(150)
                    self.assertTrue(panel.winfo_ismapped())
                    self.assertEqual(panel.cancel_button.cget('state'), 'normal')
                    motion = panel.activity._indeterminate_value
                    self.pump(150)
                    self.assertNotEqual(panel.activity._indeterminate_value, motion)
                    self.assertIn('files (', panel.detail.cget('text'))
                    self.assertIn('%', panel.detail.cget('text'))
                    self.assertIn('.png', panel.detail.cget('text'))
                    release.set()
                    final = self.finished()
                self.assertEqual(final['failed'], 1)
                self.assertEqual(final['skipped'], 1)
                self.assertEqual(final['imported'], 5 if kind == 'paths' else 0)
                self.assertEqual(final['duplicates'], 1 if kind == 'paths' else 6)
                self.assertEqual(final['awaiting'], 5)
                self.assertIn('Awaiting torso processing: 5', panel.counts.cget('text'))
                self.assertFalse(panel.activity._loop_running)
                self.assertIsNone(panel._poll_id)
        self.assertTrue(calls)
        self.assertNotIn(main_thread, calls)

    def test_duplicate_wait_close_reopen_navigation_and_cancel(self):
        self.dataset.import_paths([self.photos[0]], 'campus_uniform')
        before = self.dataset._load()
        self.launch('paths', (str(self.photos[0]),))
        panel = self.view.import_progress
        self.wait(lambda: panel._review_window is not None)
        job = panel.job
        review = panel._review_window
        self.assertEqual(job.snapshot()['phase'], 'waiting_duplicates')
        self.assertIn('Waiting for duplicate', panel.heading.cget('text'))
        review.close()
        self.view.show_category('organizational_shirt')
        self.pump(100)
        panel.review_button.invoke()
        self.assertIsNot(review, panel._review_window)
        self.assertEqual(panel._review_window.job.job_id, job.job_id)
        panel._review_window.cancel_button.invoke()
        self.finished()
        self.assertEqual(self.dataset._load(), before)
        self.assertTrue(job.snapshot()['cancelled'])

    def test_discovery_read_hash_and_tk_delivery_thread_boundaries(self):
        main = threading.get_ident()
        calls = {name: [] for name in ('scan', 'read', 'hash', 'render')}
        def record(name, function):
            def wrapped(*args, **kwargs):
                calls[name].append(threading.get_ident())
                return function(*args, **kwargs)
            return wrapped
        panel = self.view.import_progress
        with patch('os.scandir', side_effect=record('scan', os.scandir)), \
                patch.object(Path, 'open', record('read', Path.open)), \
                patch('hashlib.sha256', side_effect=record('hash', hashlib.sha256)), \
                patch.object(panel, '_render', side_effect=record('render', panel._render)):
            self.launch('folder', str(self.inputs))
            self.finished()
        for name in ('scan', 'read', 'hash'):
            self.assertTrue(calls[name], name)
            self.assertNotIn(main, calls[name], name)
        self.assertTrue(calls['render'])
        self.assertEqual(set(calls['render']), {main})

    def test_cancel_retains_saves_navigation_and_retry_recover_counts(self):
        original = self.dataset._import_bytes
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        calls = 0
        def save(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original(*args, **kwargs)
            if calls == 1:
                entered.set()
                release.wait(5)
            return result
        with patch.object(self.dataset, '_import_bytes', side_effect=save):
            self.launch('paths', tuple(map(str, self.photos)))
            self.wait(entered.is_set)
            self.view.show_category('organizational_shirt')
            self.pump(150)
            self.assertTrue(self.view.import_progress.winfo_ismapped())
            self.view.import_progress.cancel_button.invoke()
            release.set()
            final = self.finished()
        self.assertTrue(final['cancelled'])
        self.assertEqual(final['imported'], 1)
        self.assertEqual(final['unprocessed'], 4)
        self.assertEqual(len(self.dataset.snapshot()['records']), 1)
        self.assertEqual(self.dataset.snapshot()['records'][0]['category'], 'campus_uniform')
        self.view.show_category('campus_uniform')
        self.launch('paths', tuple(map(str, self.photos)))
        retry = self.finished()
        self.assertEqual((retry['imported'], retry['duplicates']), (4, 1))

    def test_partial_limit_has_accurate_summary_and_retry(self):
        with patch('core.uniform_dataset.MAX_ZIP_FILES', 2):
            self.launch('paths', tuple(map(str, self.photos)))
            result = self.finished()
        self.assertEqual((result['imported'], result['unprocessed']), (2, 3))
        self.assertIn('file count limit', self.view.import_progress.detail.cget('text'))
        self.launch('paths', tuple(map(str, self.photos)))
        result = self.finished()
        self.assertEqual((result['imported'], result['duplicates']), (3, 2))

    def test_folder_discovery_is_indeterminate_and_can_be_cancelled(self):
        from core.uniform_duplicates import ImportPlan
        original = ImportPlan.import_folder
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def discover(plan, *args, **kwargs):
            report = kwargs['progress']
            def progress(state):
                report(state)
                if state.get('phase') == 'discovery' and state.get('discovered', 0) > 0 and not entered.is_set():
                    entered.set()
                    release.wait(5)
            kwargs['progress'] = progress
            return original(plan, *args, **kwargs)
        with patch.object(ImportPlan, 'import_folder', discover):
            self.launch('folder', str(self.inputs))
            self.wait(entered.is_set)
            panel = self.view.import_progress
            self.wait(lambda: panel._last.get('discovered', 0) > 0)
            self.assertIn('Finding photos…', panel.heading.cget('text'))
            self.assertEqual(panel.progress.cget('mode'), 'indeterminate')
            self.assertIn('Discovered:', panel.detail.cget('text'))
            panel.cancel_button.invoke()
            release.set()
            result = self.finished()
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['imported'], 0)
        self.assertIsNone(result['total'])

    def test_corrupt_archive_finishes_with_error_and_allows_retry(self):
        archive = self.root_path / 'corrupt.zip'
        archive.write_bytes(b'not a zip archive')
        self.launch('zip', str(archive))
        final = self.finished()
        self.assertTrue(final['error'])
        self.assertEqual(final['imported'], 0)
        self.assertFalse(self.view.import_progress.activity._loop_running)
        self.launch('paths', (str(self.photos[0]),))
        self.assertEqual(self.finished()['imported'], 1)

    def test_destroy_reopen_reattaches_job_without_stale_callbacks(self):
        original = self.dataset._import_bytes
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def save(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)
        with patch.object(self.dataset, '_import_bytes', side_effect=save):
            self.launch('paths', tuple(map(str, self.photos)))
            self.wait(entered.is_set)
            panel = self.view.import_progress
            job = panel.job
            timer = panel._poll_id
            self.view.destroy()
            self.assertIsNone(panel._poll_id)
            self.assertNotIn(timer, self.root.tk.call('after', 'info'))
            self.view = self.open_view()
            self.assertEqual(self.view.import_progress.job.job_id, job.job_id)
            release.set()
            final = self.finished()
        self.assertTrue(final['cancelled'])
        self.assertIn('Cancelled', self.view.import_progress.heading.cget('text'))
        self.pump(200)
        self.assertEqual(self.errors, [])

    def test_progress_continues_through_real_crop_processing(self):
        release = threading.Event()
        self.releases.append(release)
        detector = Mock(use_pose=True)
        detector.detect_persons.return_value = [[0, 0, 200, 200]]
        detector.pose_torso_box.return_value = [20, 20, 180, 180]
        # Real catalog/crop/quality work with deterministic detector results; one processor per dataset.
        def schedule(dataset):
            if self.threads and self.threads[-1].is_alive():
                return self.threads[-1]
            def work():
                release.wait(5)
                dataset.process_pending(detector=detector)
            thread = threading.Thread(target=work)
            self.threads.append(thread)
            thread.start()
            return thread
        self.dataset.auto_process = True
        with patch('core.uniform_processing.schedule', side_effect=schedule):
            self.launch('paths', tuple(map(str, self.photos)))
            panel = self.view.import_progress
            self.wait(lambda: panel._last and panel._last.get('phase') == 'processing')
            self.assertTrue(panel.active)
            self.assertIn('Awaiting torso processing: 5', panel.counts.cget('text'))
            self.assertIn('File import finished.', panel.detail.cget('text'))
            self.assertEqual(panel.cancel_button.cget('state'), 'disabled')
            motion = panel.activity._indeterminate_value
            self.pump(150)
            self.assertNotEqual(motion, panel.activity._indeterminate_value)
            release.set()
            final = self.finished()
        self.assertEqual(final['awaiting'], 0)
        self.assertEqual(final['training_ready'], 5)
        self.assertEqual(final['processing_done'], 5)
        self.assertTrue(all(r['crop_source'] == 'detector' for r in self.dataset.snapshot()['records']))
