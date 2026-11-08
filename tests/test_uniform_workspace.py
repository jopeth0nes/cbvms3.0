"""Native Stage 1 controls using disposable datasets and cached synthetic frames."""
import tempfile
import gc
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import customtkinter as ctk
import numpy as np
from PIL import Image

from core.uniform_dataset import UniformDataset, CATEGORIES
from core.uniform_candidate import CandidateService
from ui.uniform_workspace import UniformWorkspace, ReviewWindow
from ui.window_lifecycle import WorkspaceWindow


class UniformWorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Match main.py's root transition: Tk finalizers belong on the UI thread.
        gc.collect()
        self.tmp = tempfile.TemporaryDirectory()
        self.dataset = UniformDataset(Path(self.tmp.name)/'dataset')
        self.source = Path(self.tmp.name)/'original.png'
        image = Image.new('RGB', (400, 300), 'navy')
        image.paste('teal', (100, 60, 280, 260))
        image.save(self.source)
        self.root = WorkspaceWindow()
        self.addCleanup(self.root.destroy)  # Also clean up if asynchronous setUp fails.
        self.root.geometry('1150x850')
        self.errors = []
        self.root.report_callback_exception = lambda *a: self.errors.append(a)
        self.capture = Mock()
        self.view = UniformWorkspace(self.root, capture=self.capture, dataset=self.dataset,
            candidate_service=CandidateService(self.dataset, candidates_root=Path(self.tmp.name)/'candidates'))
        self.view.pack(fill='both', expand=True)
        self.wait()

    def tearDown(self):
        self.root.destroy()
        self.tmp.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self, ms=30):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def wait(self, task=None):
        task = task or self.view.task
        end = time.monotonic()+8
        while task.busy and time.monotonic()<end:
            self.pump()
        self.assertFalse(task.busy)
        self.pump()

    def descendants(self, w):
        yield w
        for child in w.winfo_children():
            yield from self.descendants(child)

    def test_named_categories_and_models_entry(self):
        self.assertEqual(list(self.view.tabs._tab_dict),
            ['Campus Uniform', 'Organizational Shirt', 'Wrong Uniform', 'Evaluation & Models'])
        self.view.show_category('other_clothing')
        self.wait()
        wrong_gallery_text = self.view.views['other_clothing'][1].cget('text')
        self.assertIn('Add clear examples of clothing outside the Campus Uniform and Organizational Shirt categories.', wrong_gallery_text)
        labels = {w.cget('text') for w in self.descendants(self.view) if isinstance(w, ctk.CTkButton)}
        self.assertTrue({'Upload Photos', 'Import Folder', 'Import ZIP', 'Capture from Camera'} <= labels)
        self.view.show_category('organizational_shirt')
        self.wait()
        self.assertEqual(self.view.category(), 'organizational_shirt')
        buttons = {w.cget('text'):w for w in self.descendants(self.view) if isinstance(w, ctk.CTkButton)}
        self.assertIn('Train candidate with approved datasets', buttons)
        self.assertEqual(buttons['Open Evaluation & Models'].cget('state'), 'normal')
        self.assertIn('active model unchanged', self.view.status.cget('text'))

    def test_shared_action_opens_real_readiness_from_organizational_shirt(self):
        self.view.show_category('organizational_shirt'); self.wait()
        self.view.open_candidates(); self.wait()
        window = self.view._candidate_window
        self.wait(window.task)
        self.assertEqual(window.title(), 'Train candidate with approved datasets')
        coverage = window.coverage.cget('text')
        self.assertIn('Campus Uniform: 0 approved • 0 eligible/included', coverage)
        self.assertIn('Organizational Shirt: 0 approved • 0 eligible/included', coverage)
        self.assertIn('Wrong Uniform: 0 approved • 0 eligible/included', coverage)
        self.assertIn('Wrong Uniform has 0 eligible approved examples; requires 10.', window.readiness.cget('text'))
        self.assertEqual(window.start_button.cget('state'), 'disabled')
        self.assertTrue(window.start_button.winfo_ismapped())
        self.assertEqual(list((Path(self.tmp.name)/'candidates').glob('*/candidate.pt')), [])
        window.destroy()

    def test_upload_gallery_review_approval_and_label_correction(self):
        with patch('ui.uniform_workspace.filedialog.askopenfilenames', return_value=(str(self.source),)):
            self.view.upload('organizational_shirt')
        self.wait()
        self.view.show_category('organizational_shirt')
        self.wait()
        self.assertIn('1 unique images', self.view.views['organizational_shirt'][1].cget('text'))
        record = self.dataset.snapshot()['records'][0]
        self.view.open_review(record)
        review = self.view._review
        self.wait(review.task)
        review.save('approved')
        self.assertIn('Confirm', review.message.cget('text'))
        review.set_crop([80, 40, 300, 280])
        review.category.set(CATEGORIES['campus_uniform'])
        review.confirm.select()
        review.save('approved')
        self.wait(review.task)
        self.wait()
        result = self.dataset.snapshot()['records'][0]
        self.assertEqual(result['crop'], [80, 40, 300, 280])
        self.assertEqual(result['category'], 'campus_uniform')
        self.assertEqual(result['status'], 'approved')
        self.assertEqual(self.source.read_bytes(), (self.dataset.root/result['original_path']).read_bytes())

    def test_batch_gallery_selects_three_of_five_and_refreshes_trainer_eligibility(self):
        paths = []
        for index in range(5):
            path = Path(self.tmp.name) / f'batch-{index}.png'
            pixels = np.random.default_rng(700 + index).integers(0, 256, (180, 200, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(path)
            paths.append(path)
        ids = self.dataset.import_paths(paths, 'campus_uniform')
        for index, iid in enumerate(ids):
            self.dataset.review(iid, category='campus_uniform', crop=[20, 20, 180, 160],
                                status='pending_review', flags=['obstruction'] if index == 4 else [],
                                notes=f'fixture-{index}')
        other = Path(self.tmp.name) / 'other-category.png'
        Image.fromarray(np.random.default_rng(999).integers(0, 256, (180, 200, 3), dtype=np.uint8)).save(other)
        other_id = self.dataset.import_paths([other], 'organizational_shirt')[0]
        self.dataset.review(other_id, category='organizational_shirt', crop=[20, 20, 180, 160], status='pending_review', flags=[])
        self.view.show_category('campus_uniform'); self.wait()
        buttons = {w.cget('text'): w for w in self.descendants(self.view.tabs.tab('Campus Uniform'))
                   if isinstance(w, ctk.CTkButton)}
        buttons['Review multiple'].invoke(); self.wait()
        gallery = self.view.views['campus_uniform'][0]
        cards = sorted(gallery.winfo_children(), key=lambda card: (card.grid_info()['row'], card.grid_info()['column']))
        checks = [next(w for w in card.winfo_children() if isinstance(w, ctk.CTkCheckBox))
                  for card in cards if any(isinstance(w, ctk.CTkCheckBox) for w in card.winfo_children())]
        self.assertEqual(len(checks), 4)  # blocked fifth row has a reason and individual review only
        self.assertTrue(all(not check.get() for check in checks))
        for check in checks[:3]:
            check.toggle()
        button = self.view.views['campus_uniform'][4]
        self.assertEqual(button.cget('text'), 'Approve selected (3)')
        before = CandidateService(self.dataset, candidates_root=Path(self.tmp.name)/'candidates').summary()['included_by_category']['campus_uniform']
        button.invoke(); self.wait()
        after = CandidateService(self.dataset, candidates_root=Path(self.tmp.name)/'candidates').summary()['included_by_category']['campus_uniform']
        self.assertEqual(after - before, 3)
        self.assertIn('3 approved, 0 skipped, 0 failed', self.view.status.cget('text'))
        rows = {r['image_id']: r for r in self.dataset.snapshot()['records']}
        self.assertEqual([rows[iid]['status'] for iid in ids],
                         ['approved', 'approved', 'approved', 'pending_review', 'pending_review'])
        self.assertEqual(sum(rows[iid]['status'] == 'approved' for iid in ids), 3)
        self.assertEqual(sum(rows[iid]['status'] == 'pending_review' for iid in ids), 2)
        self.view.refresh(); self.wait()
        self.assertEqual(sum(r['status'] == 'approved' for r in self.dataset.snapshot()['records']), 3)
        self.assertIn('3 approved • 2 pending review', self.view.views['campus_uniform'][1].cget('text'))
        self.assertEqual(next(r for r in self.dataset.snapshot()['records'] if r['image_id'] == other_id)['status'], 'pending_review')
        self.view.destroy()
        self.view = UniformWorkspace(self.root, capture=self.capture, dataset=UniformDataset(self.dataset.root),
            candidate_service=CandidateService(UniformDataset(self.dataset.root), candidates_root=Path(self.tmp.name)/'candidates'))
        self.view.pack(fill='both', expand=True); self.wait()
        self.view.show_category('campus_uniform'); self.wait()
        self.assertIn('3 approved • 2 pending review', self.view.views['campus_uniform'][1].cget('text'))
        reopened = {r['image_id']: r for r in self.view.dataset.snapshot()['records']}
        self.assertEqual([reopened[iid]['status'] for iid in ids],
                         ['approved', 'approved', 'approved', 'pending_review', 'pending_review'])
        self.assertEqual(self.view.candidate_service.summary()['included_by_category']['campus_uniform'] - before, 3)

    def test_batch_selection_clears_when_category_or_page_changes(self):
        path = Path(self.tmp.name) / 'scope.png'
        Image.fromarray(np.random.default_rng(811).integers(0, 256, (180, 200, 3), dtype=np.uint8)).save(path)
        iid = self.dataset.import_paths([path], 'campus_uniform')[0]
        self.dataset.review(iid, category='campus_uniform', crop=[20, 20, 180, 160], status='pending_review', flags=[])
        second = Path(self.tmp.name) / 'scope-second.png'
        Image.fromarray(np.random.default_rng(813).integers(0, 256, (180, 200, 3), dtype=np.uint8)).save(second)
        second_id = self.dataset.import_paths([second], 'campus_uniform')[0]
        self.dataset.review(second_id, category='campus_uniform', crop=[20, 20, 180, 160], status='pending_review', flags=[])
        other = Path(self.tmp.name) / 'scope-other.png'
        Image.fromarray(np.random.default_rng(812).integers(0, 256, (180, 200, 3), dtype=np.uint8)).save(other)
        other_id = self.dataset.import_paths([other], 'organizational_shirt')[0]
        self.dataset.review(other_id, category='organizational_shirt', crop=[20, 20, 180, 160], status='pending_review', flags=[])
        self.view.show_category('campus_uniform'); self.wait()
        self.view.toggle_batch('campus_uniform'); self.wait()
        card = self.view.views['campus_uniform'][0].winfo_children()[0]
        check = next(w for w in card.winfo_children() if isinstance(w, ctk.CTkCheckBox))
        check.toggle()
        self.assertEqual(self.view.views['campus_uniform'][4].cget('text'), 'Approve selected (1)')
        with patch('ui.uniform_workspace.PAGE_SIZE', 1):
            self.view.refresh(); self.wait()
            card = self.view.views['campus_uniform'][0].winfo_children()[0]
            next(w for w in card.winfo_children() if isinstance(w, ctk.CTkCheckBox)).toggle()
            self.assertEqual(self.view.views['campus_uniform'][4].cget('text'), 'Approve selected (1)')
            footer = next(w for w in self.descendants(self.view)
                          if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Next')
            footer.invoke(); self.wait()
            self.assertEqual(self.view.page, 1)
        self.assertEqual(self.view.views['campus_uniform'][4].cget('text'), 'Approve selected (0)')
        with patch('ui.uniform_workspace.PAGE_SIZE', 1):
            previous = next(w for w in self.descendants(self.view)
                            if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Previous')
            previous.invoke(); self.wait()
        card = self.view.views['campus_uniform'][0].winfo_children()[0]
        check = next(w for w in card.winfo_children() if isinstance(w, ctk.CTkCheckBox))
        self.assertFalse(check.get())
        check.toggle()
        self.view.tabs._segmented_button._buttons_dict['Organizational Shirt'].invoke(); self.wait()
        self.view.tabs._segmented_button._buttons_dict['Campus Uniform'].invoke(); self.wait()
        self.assertEqual(self.view.views['campus_uniform'][4].cget('text'), 'Approve selected (0)')
        self.assertEqual(self.dataset.snapshot()['records'][0]['status'], 'pending_review')
        self.assertEqual(next(r for r in self.dataset.snapshot()['records'] if r['image_id'] == other_id)['status'], 'pending_review')

    def test_unreadable_original_blocks_only_its_gallery_card(self):
        iid = self.dataset.import_paths([self.source], 'campus_uniform')[0]
        self.dataset.review(iid, category='campus_uniform', crop=[20, 20, 180, 160],
                            status='pending_review', flags=[])
        row = self.dataset.snapshot()['records'][0]
        (self.dataset.root / row['original_path']).unlink()
        self.view.show_category('campus_uniform'); self.wait()
        self.view.toggle_batch('campus_uniform'); self.wait()
        card = self.view.views['campus_uniform'][0].winfo_children()[0]
        self.assertTrue(any(isinstance(w, ctk.CTkLabel) and 'Original image unavailable' in w.cget('text')
                            for w in card.winfo_children()))
        self.assertFalse(any(isinstance(w, ctk.CTkCheckBox) for w in card.winfo_children()))

    def test_batch_rechecks_crop_after_selection(self):
        path = Path(self.tmp.name) / 'stale-crop.png'
        Image.fromarray(np.random.default_rng(820).integers(0, 256, (180, 200, 3), dtype=np.uint8)).save(path)
        iid = self.dataset.import_paths([path], 'campus_uniform')[0]
        self.dataset.review(iid, category='campus_uniform', crop=[20, 20, 180, 160], status='pending_review', flags=[])
        self.view.show_category('campus_uniform'); self.wait()
        next(w for w in self.descendants(self.view) if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Review multiple').invoke()
        self.wait()
        card = self.view.views['campus_uniform'][0].winfo_children()[0]
        next(w for w in card.winfo_children() if isinstance(w, ctk.CTkCheckBox)).toggle()
        self.dataset.review(iid, category='campus_uniform', crop=[21, 20, 180, 160], status='pending_review', flags=[])
        self.view.views['campus_uniform'][4].invoke(); self.wait()
        row = next(r for r in self.dataset.snapshot()['records'] if r['image_id'] == iid)
        self.assertEqual(row['status'], 'pending_review')
        self.assertEqual(row['crop'], [21, 20, 180, 160])
        self.assertIn('crop', self.view.status.cget('text'))
        self.assertIn('changed since display', self.view.status.cget('text'))

    def test_batch_organizational_shirt_refreshes_open_trainer_counts(self):
        path = Path(self.tmp.name) / 'organizational-batch.png'
        Image.fromarray(np.random.default_rng(860).integers(
            0, 256, (180, 200, 3), dtype=np.uint8)).save(path)
        iid = self.dataset.import_paths([path], 'organizational_shirt')[0]
        self.dataset.review(iid, category='organizational_shirt', crop=[20, 20, 180, 160],
                            status='pending_review', flags=[])
        self.view.show_category('organizational_shirt'); self.wait()
        self.view.open_candidates(); self.wait()
        window = self.view._candidate_window
        self.wait(window.task)
        self.assertIn('Organizational Shirt: 0 approved • 0 eligible/included', window.coverage.cget('text'))
        next(w for w in self.descendants(self.view.tabs.tab('Organizational Shirt'))
             if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Review multiple').invoke()
        self.wait()
        self.assertEqual(self.view.views['organizational_shirt'][4].cget('text'), 'Approve selected (0)')
        check = next(w for w in self.descendants(self.view.views['organizational_shirt'][0])
                     if isinstance(w, ctk.CTkCheckBox))
        check.toggle()
        # An already-running readiness read must finish before the new one starts.
        release = threading.Event()
        try:
            window.task.run(lambda: release.wait(3), lambda _: None, self.fail)
            self.view.views['organizational_shirt'][4].invoke(); self.wait()
        finally:
            release.set()
        deadline = time.monotonic() + 8
        while ('Organizational Shirt: 1 approved • 1 eligible/included' not in window.coverage.cget('text')
               and time.monotonic() < deadline):
            self.pump()
        self.assertIn('Organizational Shirt: 1 approved • 1 eligible/included', window.coverage.cget('text'))
        self.assertIn('requires 10', window.readiness.cget('text'))
        self.assertEqual(window.start_button.cget('state'), 'disabled')
        window.destroy()

    def test_batch_stable_ids_survive_reorder_and_refresh_clears_selection(self):
        ids = []
        for index in range(2):
            path = Path(self.tmp.name) / f'reorder-{index}.png'
            Image.fromarray(np.random.default_rng(850 + index).integers(
                0, 256, (180, 200, 3), dtype=np.uint8)).save(path)
            iid = self.dataset.import_paths([path], 'other_clothing')[0]
            self.dataset.review(iid, category='other_clothing', crop=[20, 20, 180, 160],
                                status='pending_review', flags=[])
            ids.append(iid)
        self.view.show_category('other_clothing'); self.wait()
        next(w for w in self.descendants(self.view.tabs.tab('Wrong Uniform'))
             if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Review multiple').invoke()
        self.wait()
        card = self.view.views['other_clothing'][0].winfo_children()[0]
        next(w for w in card.winfo_children() if isinstance(w, ctk.CTkCheckBox)).toggle()
        with self.dataset._locked():
            catalog = self.dataset._load()
            catalog['records'].reverse()
            self.dataset._save(catalog)
        self.view.views['other_clothing'][4].invoke(); self.wait()
        rows = {r['image_id']: r for r in self.dataset.snapshot()['records']}
        self.assertEqual(rows[ids[0]]['status'], 'approved')
        self.assertEqual(rows[ids[1]]['status'], 'pending_review')
        self.assertNotIn('logo_annotation', rows[ids[0]])
        check = next(w for w in self.descendants(self.view.views['other_clothing'][0])
                     if isinstance(w, ctk.CTkCheckBox))
        check.toggle()
        self.view.refresh()
        self.assertEqual(self.view.views['other_clothing'][4].cget('text'), 'Approve selected (0)')
        self.assertEqual(check.cget('state'), 'disabled')
        self.wait()
        self.view.views['other_clothing'][4].invoke(); self.wait()
        self.assertEqual(next(r for r in self.dataset.snapshot()['records']
                              if r['image_id'] == ids[1])['status'], 'pending_review')

    def test_batch_double_click_partial_failure_and_retry_from_gallery(self):
        ids = []
        for index in range(2):
            path = Path(self.tmp.name) / f'partial-{index}.png'
            Image.fromarray(np.random.default_rng(830 + index).integers(0, 256, (180, 200, 3), dtype=np.uint8)).save(path)
            iid = self.dataset.import_paths([path], 'campus_uniform')[0]
            self.dataset.review(iid, category='campus_uniform', crop=[20, 20, 180, 160], status='pending_review', flags=[])
            ids.append(iid)
        self.view.show_category('campus_uniform'); self.wait()
        next(w for w in self.descendants(self.view) if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Review multiple').invoke()
        self.wait()
        cards = self.view.views['campus_uniform'][0].winfo_children()
        for card in cards:
            for widget in card.winfo_children():
                if isinstance(widget, ctk.CTkCheckBox): widget.toggle()
        button = self.view.views['campus_uniform'][4]
        entered, release = threading.Event(), threading.Event()
        original_save = self.dataset._save
        calls = [0]
        ui_thread = threading.get_ident()
        def fail_second(catalog):
            self.assertNotEqual(threading.get_ident(), ui_thread)
            calls[0] += 1
            if calls[0] == 1:
                entered.set(); release.wait(2)
            if calls[0] == 2:
                raise OSError('fixture second-row save failure')
            original_save(catalog)
        with patch.object(self.dataset, '_save', side_effect=fail_second):
            button.invoke()
            self.assertTrue(entered.wait(1))
            heartbeat = []
            self.root.after(0, lambda: heartbeat.append(True))
            self.pump()
            self.assertEqual(heartbeat, [True])
            button.invoke()  # A repeated click while the worker is busy cannot start another write.
            release.set()
            self.wait()
        rows = {r['image_id']: r for r in self.dataset.snapshot()['records']}
        self.assertEqual(sum(rows[iid]['status'] == 'approved' for iid in ids), 1)
        self.assertEqual(sum(rows[iid]['status'] == 'pending_review' for iid in ids), 1)
        self.assertIn('1 approved, 0 skipped, 1 failed', self.view.status.cget('text'))
        self.view.show_category('campus_uniform'); self.wait()
        for card in self.view.views['campus_uniform'][0].winfo_children():
            checks = [w for w in card.winfo_children() if isinstance(w, ctk.CTkCheckBox)]
            if checks: checks[0].toggle()
        self.view.views['campus_uniform'][4].invoke(); self.wait()
        rows = {r['image_id']: r for r in self.dataset.snapshot()['records']}
        self.assertTrue(all(rows[iid]['status'] == 'approved' for iid in ids))
        self.assertTrue(all(len(rows[iid]['review_history']) == 2 for iid in ids))

    def test_limit_failure_shows_progress_and_refreshes_partial_gallery(self):
        self.view.show_category("other_clothing"); self.wait()
        paths = []
        for index in range(3):
            path = Path(self.tmp.name) / f'limit-{index}.png'
            image = Image.fromarray(np.random.default_rng(1200 + index).integers(
                0, 256, (80, 90, 3), dtype=np.uint8))
            image.save(path)
            paths.append(str(path))
        with patch('ui.uniform_workspace.filedialog.askopenfilenames', return_value=tuple(paths)), \
             patch('core.uniform_dataset.MAX_ZIP_FILES', 2):
            self.view.upload('other_clothing')
            self.wait()
            self.wait()
        status = self.view.status.cget('text')
        self.assertIn('file count limit (2 files', status)
        self.assertIn('Imported: 2', status)
        self.assertIn('skipped as duplicates: 0', status)
        self.assertIn('left unprocessed: 1', status)
        self.assertIn('2 unique images', self.view.views['other_clothing'][1].cget('text'))

    def test_import_and_decode_stay_off_tk_thread(self):
        main_thread = threading.get_ident()
        calls = []
        original = self.dataset.import_paths
        def work(*a, **k):
            calls.append(threading.get_ident())
            return original(*a, **k)
        with patch.object(self.dataset, 'import_paths', side_effect=work), patch('ui.uniform_workspace.filedialog.askopenfilenames', return_value=(str(self.source),)):
            self.view.upload('other_clothing')
            self.wait()
        self.assertTrue(calls)
        self.assertNotIn(main_thread, calls)

    def test_crop_proposal_does_not_approve_and_remains_editable(self):
        iid = self.dataset.import_paths([self.source], 'organizational_shirt')[0]
        record = self.dataset.snapshot()['records'][0]
        self.view.open_review(record)
        review = self.view._review
        self.wait(review.task)
        with patch.object(self.dataset, 'propose_crop', return_value=[30, 20, 330, 270]):
            review.propose()
            self.wait(review.task)
        self.assertEqual(review.crop(), [30, 20, 330, 270])
        self.assertEqual(self.dataset.snapshot()['records'][0]['status'], 'pending_review')
        review.set_crop([20, 10, 350, 280])
        self.assertEqual(review.crop(), [20, 10, 350, 280])

    def test_capture_uses_supplied_frame_and_shared_group_without_flip(self):
        frame = np.zeros((200, 300, 3), dtype=np.uint8)
        frame[:, :80] = (0, 0, 255)
        done = Mock()
        self.view.import_frames([frame], 'organizational_shirt', 'camera-session:test', done)
        self.wait()
        done.assert_called_once()
        record = self.dataset.snapshot()['records'][0]
        self.assertEqual(record['capture_group'], 'camera-session:test')
        self.assertEqual(self.dataset.image(record['image_id']).getpixel((10, 50)), (255, 0, 0))
        self.assertEqual(record['status'], 'pending_review')

    def test_destroy_during_background_work_has_no_tk_callback(self):
        gate = threading.Event()
        finished = threading.Event()
        def operation():
            gate.wait(2)
            finished.set()
        self.view.run(operation, 'Testing lifecycle')
        self.view.destroy()
        gate.set()
        self.assertTrue(finished.wait(2))
        self.pump(100)

    def test_training_panel_keeps_sections_and_routes_camera_saves(self):
        from ui.training_panel import TrainingPanel
        trainer = Mock()
        trainer.get_sample_counts.return_value = {'no_earring':0, 'with_earring':0}
        trainer.list_samples.return_value = []
        trainer.is_trained.return_value = False
        with patch('ui.uniform_workspace.UniformDataset', return_value=self.dataset):
            panel = TrainingPanel(self.root, trainer=trainer, get_frame=lambda: np.zeros((120,160,3),dtype=np.uint8))
        panel.pack(fill='both', expand=True)
        self.wait(panel._uniform_workspace.task)
        self.assertEqual(list(panel._tabview._tab_dict), ['Uniform Check', 'DYCI Logo & Digital Shirts'])
        panel._tabview.set('Earring Check')
        self.assertEqual(panel._tabview.get(), 'Uniform Check')
        panel._open_capture_screen('uniform_stage1', 'organizational_shirt')
        state = panel._capture_state
        panel._do_capture(state, np.zeros((120,160,3),dtype=np.uint8))
        panel._save_changes(state)
        self.wait(panel._uniform_workspace.task)
        trainer.add_sample.assert_not_called()
        trainer.train.assert_not_called()
        self.assertEqual(len(self.dataset.snapshot()['records']), 1)
        with patch.object(panel._uniform_workspace.candidate_service, 'is_busy', return_value=True), patch('ui.training_panel.show_toast'):
            panel._open_capture_screen('earring', 'no_earring')
            panel._start_training('earring')
            panel._start_evaluation('earring')
        self.assertIsNone(panel._capture_screen)
        trainer.train.assert_not_called()
        trainer.evaluate.assert_not_called()
        panel.on_hide()
        panel.destroy()
