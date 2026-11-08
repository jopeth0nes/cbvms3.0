"""Native Stage 1 controls using disposable datasets and cached synthetic frames."""
import tempfile
import io
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
from ui.uniform_workspace import UniformWorkspace, ReviewWindow, GARMENTS
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

    def test_global_approve_all_confirmation_cancel_and_all_pages_categories(self):
        def add(category, name, seed, status='pending_review', flags=None, crop=True):
            data=io.BytesIO()
            Image.fromarray(np.random.default_rng(seed).integers(0,256,(180,200,3),dtype=np.uint8)).save(data,format='PNG')
            path=Path(self.tmp.name)/name; path.write_bytes(data.getvalue())
            iid=self.dataset.import_paths([path],category)[0]
            if status != 'pending_review' or flags or crop:
                self.dataset.review(iid,category=category,crop=[20,20,180,160] if crop else [20,20,180,160],
                                    status=status,flags=flags or [])
                if not crop:
                    with self.dataset._locked():
                        catalog=self.dataset._load(); row=next(r for r in catalog['records'] if r['image_id']==iid)
                        row['crop']=None; self.dataset._save(catalog)
            return iid
        ids=[]
        for i in range(14):
            ids.append(add('campus_uniform',f'all-campus-{i}.png',1000+i,
                           flags=['provenance_association_uncertain'] if i==0 else [], crop=i!=1))
        ids.append(add('organizational_shirt','all-org.png',1100))
        ids.append(add('other_clothing','all-other.png',1101,flags=['near_duplicate_review']))
        excluded=add('campus_uniform','all-excluded.png',1102,status='excluded')
        legacy=add('unassigned','all-legacy.png',1103)
        # Exact source occurrence remains one hash-addressed row.
        record=next(r for r in self.dataset.snapshot()['records'] if r['image_id']==ids[0])
        self.dataset.import_paths([self.dataset.root/record['original_path']], 'campus_uniform')
        self.assertEqual(len(self.dataset.snapshot()['records']),18)
        for category in ('campus_uniform','organizational_shirt','other_clothing'):
            self.view.show_category(category); self.wait()
            self.view.confirm_approve_all()
            dialog=self.view._approval_dialog
            buttons=[w for w in self.descendants(dialog) if isinstance(w,ctk.CTkButton)]
            self.assertEqual({b.cget('text') for b in buttons},{'Cancel','Approve All'})
            self.pump()
            for button in buttons:
                self.assertTrue(button.winfo_ismapped())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                     dialog.winfo_rooty() + dialog.winfo_height())
            next(b for b in buttons if b.cget('text')=='Cancel').invoke()
            self.assertEqual(sum(r['status']=='pending_review' and r['category'] in GARMENTS for r in self.dataset.snapshot()['records']),16)
        # Run confirmation from another category tab; page 2 is intentionally selected.
        self.view.show_category('campus_uniform'); self.wait(); self.view.move_page(1); self.wait()
        self.view.open_candidates(); self.wait(); candidate_window=self.view._candidate_window; self.wait(candidate_window.task)
        self.view.show_category('other_clothing'); self.wait()
        self.view.confirm_approve_all(); dialog=self.view._approval_dialog
        next(w for w in self.descendants(dialog) if isinstance(w,ctk.CTkButton) and w.cget('text')=='Approve All').invoke()
        self.wait()
        rows={r['image_id']:r for r in self.dataset.snapshot()['records']}
        self.assertTrue(all(rows[iid]['status']=='approved' for iid in ids))
        self.assertEqual(rows[excluded]['status'],'excluded'); self.assertEqual(rows[legacy]['status'],'pending_review')
        self.assertEqual(rows[ids[0]]['flags'],['provenance_association_uncertain'])
        self.assertIsNone(rows[ids[1]]['crop'])
        self.assertIn('not-training-eligible among all approved garment photos',self.view.status.cget('text'))
        self.assertIn('16 approved',self.view.status.cget('text'))
        self.wait(candidate_window.task)
        self.assertIn('Campus Uniform: 14 approved • 12 eligible/included',candidate_window.coverage.cget('text'))
        self.view.destroy()
        reopened=UniformDataset(Path(self.tmp.name)/'dataset')
        saved={r['image_id']:r for r in reopened.snapshot()['records']}
        self.assertTrue(all(saved[iid]['status']=='approved' for iid in ids))
        self.assertEqual(len(saved[ids[0]]['review_history']),2)

    def test_global_approve_all_save_failure_is_atomic_responsive_and_retryable(self):
        ids=[]
        for i,category in enumerate(('campus_uniform','organizational_shirt','other_clothing')):
            data=io.BytesIO(); Image.fromarray(np.random.default_rng(1200+i).integers(0,256,(160,180,3),dtype=np.uint8)).save(data,format='PNG')
            path=Path(self.tmp.name)/f'failbulk-{i}.png'; path.write_bytes(data.getvalue())
            ids.append(self.dataset.import_paths([path],category)[0])
        before=self.dataset.snapshot()['records']
        entered,release=threading.Event(),threading.Event()
        original=self.dataset._save
        original_run = self.view.task.run
        callbacks = {}
        def capture_callbacks(operation, done, error):
            callbacks['error'] = error
            return original_run(operation, done, error)
        save_calls=[0]
        def fail(catalog):
            save_calls[0]+=1
            entered.set(); release.wait(2); raise OSError('fixture save error')
        with patch.object(self.dataset,'_save',side_effect=fail), patch.object(self.view.task, 'run', side_effect=capture_callbacks):
            self.view.confirm_approve_all(); dialog=self.view._approval_dialog
            approve=next(w for w in self.descendants(dialog) if isinstance(w,ctk.CTkButton) and w.cget('text')=='Approve All')
            approve.invoke()
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.view.approve_all_button.cget('state'),'disabled')
            self.view.approve_all_button.invoke()
            self.assertEqual(save_calls[0],1)
            heartbeat=[]; self.root.after(1,lambda:heartbeat.append(True)); self.pump(20)
            self.assertTrue(heartbeat)
            self.assertIn('Approving all pending',self.view.status.cget('text'))
            callbacks['error']('Still working. Please wait.')
            self.assertTrue(self.view.task.busy)
            self.assertEqual(self.view.approve_all_button.cget('state'), 'disabled')
            self.assertIn('Still working', self.view.status.cget('text'))
            self.assertNotIn('failed', self.view.status.cget('text'))
            release.set(); self.wait()
        self.assertIn('Approve all failed',self.view.status.cget('text'))
        self.assertIn('failed 3',self.view.status.cget('text'))
        self.assertIn('remaining pending 3',self.view.status.cget('text'))
        self.assertEqual(self.dataset.snapshot()['records'],before)
        with patch.object(self.dataset,'_save',wraps=original):
            self.view.confirm_approve_all(); dialog=self.view._approval_dialog
            next(w for w in self.descendants(dialog) if isinstance(w,ctk.CTkButton) and w.cget('text')=='Approve All').invoke()
            self.wait()
        self.assertIn('3 approved of 3 pending',self.view.status.cget('text'))
        self.assertTrue(all(r['status']=='approved' for r in self.dataset.snapshot()['records']))

    def test_global_button_approves_from_every_category_tab(self):
        for index, current in enumerate(('Campus Uniform', 'Organizational Shirt', 'Wrong Uniform', 'Evaluation & Models')):
            with self.subTest(tab=current):
                ids = []
                for number, category in enumerate(GARMENTS):
                    pixels = np.random.default_rng(1500 + index * 3 + number).integers(0, 256, (180, 200, 3), dtype=np.uint8)
                    path = Path(self.tmp.name) / f'tab-{index}-{number}.png'
                    Image.fromarray(pixels).save(path)
                    ids.append(self.dataset.import_paths([path], category)[0])
                self.view.tabs._segmented_button._buttons_dict[current].invoke(); self.wait()
                self.view.approve_all_button.invoke()
                dialog = self.view._approval_dialog
                next(w for w in self.descendants(dialog) if isinstance(w, ctk.CTkButton)
                     and w.cget('text') == 'Approve All').invoke()
                self.wait()
                rows = {r['image_id']: r for r in self.dataset.snapshot()['records']}
                self.assertTrue(all(rows[iid]['status'] == 'approved' for iid in ids))
                self.assertTrue(all(len(rows[iid]['review_history']) == 1 for iid in ids))
                self.assertIn('3 approved of 3 pending', self.view.status.cget('text'))

    def test_bulk_failure_callback_does_not_read_catalog_on_ui_thread(self):
        data=io.BytesIO(); Image.fromarray(np.random.default_rng(991).integers(0,256,(100,120,3),dtype=np.uint8)).save(data,format='PNG')
        path=Path(self.tmp.name)/'callback-fail.png'; path.write_bytes(data.getvalue())
        self.dataset.import_paths([path],'campus_uniform')
        original_snapshot=self.dataset.snapshot
        def snapshot_checked():
            self.assertNotEqual(threading.current_thread(), threading.main_thread())
            return original_snapshot()
        with patch.object(self.dataset,'_save',side_effect=OSError('disk full')), \
             patch.object(self.dataset,'snapshot',side_effect=snapshot_checked):
            self.view.confirm_approve_all(); dialog=self.view._approval_dialog
            next(w for w in self.descendants(dialog) if isinstance(w,ctk.CTkButton) and w.cget('text')=='Approve All').invoke()
            self.wait()
        self.assertIn('failed 1',self.view.status.cget('text'))
        self.assertIn('not-training-eligible among all approved garment photos 0',self.view.status.cget('text'))

    def test_capture_and_import_guard_during_closed_review_write(self):
        entered,release=threading.Event(),threading.Event()
        data=io.BytesIO(); Image.fromarray(np.random.default_rng(992).integers(0,256,(100,120,3),dtype=np.uint8)).save(data,format='PNG')
        path=Path(self.tmp.name)/'review-race.png'; path.write_bytes(data.getvalue())
        iid=self.dataset.import_paths([path],'campus_uniform')[0]
        record=next(r for r in self.dataset.snapshot()['records'] if r['image_id']==iid)
        self.view.open_review(record); review=self.view._review; self.wait(review.task)
        original_review=self.dataset.review
        def slow_review(*args,**kwargs):
            entered.set(); release.wait(2)
            return original_review(*args,**kwargs)
        with patch.object(self.dataset,'review',side_effect=slow_review):
            review.save('pending_review')
            self.assertTrue(entered.wait(1))
            review.save('pending_review')
            review.propose()
            self.assertTrue(self.view._review_write_pending.is_set())
            review.destroy()
            self.assertTrue(self.view._review_write_pending.is_set())
            self.view.confirm_approve_all()
            self.assertIsNone(getattr(self.view,'_approval_dialog',None))
            with patch('ui.uniform_workspace.filedialog.askopenfilenames') as picker:
                self.view.upload('campus_uniform'); picker.assert_not_called()
            self.view.start_capture('campus_uniform')
            self.capture.assert_not_called()
            release.set()
            end=time.monotonic()+2
            while self.view._review_write_pending.is_set() and time.monotonic()<end:
                self.pump(15)
        self.assertFalse(self.view._review_write_pending.is_set())

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
