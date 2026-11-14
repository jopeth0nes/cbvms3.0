"""Opt-in integrated native import → real candidate smoke; never activates a model.

CBVMS_RUN_REAL_TRAINING_SMOKE=1 .venv/bin/python -m unittest \
    tests.test_training_import_integrated_ui.IntegratedImportUITests.test_import_to_candidate -v
"""
import hashlib
import json
import os
import shutil
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import customtkinter as ctk
import numpy as np
from PIL import Image

from core.uniform_dataset import CATEGORIES
from ui.uniform_candidate_panel import TRAIN_ACTION
from tests import test_uniform_import_ui as import_ui


class IntegratedImportUITests(unittest.TestCase):
    # Reuse the disposable Tk harness, without rediscovering its Stage 3 tests.
    setUp = import_ui.UniformImportUITests.setUp
    tearDown = import_ui.UniformImportUITests.tearDown
    open_view = import_ui.UniformImportUITests.open_view
    pump = import_ui.UniformImportUITests.pump
    wait = import_ui.UniformImportUITests.wait
    finished = import_ui.UniformImportUITests.finished
    launch = import_ui.UniformImportUITests.launch
    resolve_duplicates = import_ui.UniformImportUITests.resolve_duplicates

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def button(self, title):
        tab = self.view.tabs.tab(CATEGORIES[self.category])
        return next(w for w in self.descendants(tab)
                    if isinstance(w, ctk.CTkButton) and w.cget('text') == title)

    def choose_review(self, choices):
        panel = self.view.import_progress
        self.wait(lambda: panel._review_window is not None)
        review = panel._review_window
        self.view.tabs._segmented_button._buttons_dict['Evaluation & Models'].invoke()
        self.pump(100)
        self.assertTrue(panel.winfo_ismapped())
        from ui.uniform_duplicate_review import _ACTIONS
        for index, row in enumerate(panel.job.duplicate_rows()):
            page = index // 4
            while review._page < page:
                review.next_button.invoke()
            frames = [w for w in review._page_widgets if isinstance(w, ctk.CTkFrame)]
            frame = frames[index % 4]
            menu = next(w for w in frame.winfo_children() if isinstance(w, ctk.CTkOptionMenu))
            action = choices(row)
            menu._dropdown_callback(dict(_ACTIONS[row['match_type']])[action])
        review.close()
        panel.review_button.invoke()
        self.assertEqual(panel._review_window.job.job_id, panel.job.job_id)
        panel._review_window.continue_button.invoke()

    def candidate_window(self):
        button = next(w for w in self.descendants(self.view)
                      if isinstance(w, ctk.CTkButton) and w.cget('text') == TRAIN_ACTION)
        button.invoke()
        self.wait(lambda: self.view._candidate_window is not None)
        window = self.view._candidate_window
        self.wait(lambda: not window.task.busy)
        return window

    @unittest.skipUnless(os.environ.get('CBVMS_RUN_REAL_TRAINING_SMOKE') == '1', 'explicit real-training smoke opt-in')
    def test_import_to_candidate(self):
        evidence = Path(os.environ.get('CBVMS_STAGE4_EVIDENCE', '/private/tmp/cbvms-stage4-evidence'))
        evidence.mkdir(parents=True, exist_ok=True)
        protected = [Path('yolov8n-cls.pt'), Path('models/uniform_cls.pt'), Path('data/uniform_models/active.json')]
        fingerprints = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in protected}
        before = fingerprints()
        stages = []
        categories = ('campus_uniform', 'organizational_shirt', 'other_clothing')
        detector = Mock(use_pose=True)
        detector.detect_persons.return_value = [[0, 0, 200, 200]]
        detector.pose_torso_box.return_value = [20, 20, 180, 180]
        # Deterministic detector only; real queued processing, crop/quality/catalog code.
        # The installed detector subprocess is separately covered by retained Stage 3 evidence.
        import subprocess
        from core.uniform_processing import schedule as real_schedule
        real_run = subprocess.run
        def processing_process(command, *args, **kwargs):
            if command[1:3] == ['-m', 'core.uniform_processing']:
                self.dataset.process_pending(detector=detector)
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)
        def schedule(dataset):
            handle = real_schedule(dataset)
            if handle not in self.threads:
                self.threads.append(handle)
            return handle
        self.dataset.auto_process = True
        originals = {}
        with patch('subprocess.run', side_effect=processing_process), \
                patch('core.uniform_processing.schedule', side_effect=schedule):
            for ci, category in enumerate(categories):
                self.category = category
                self.view.show_category(category)
                paths = []
                for i in range(4):
                    path = self.inputs / f'base-{ci}-{i}.png'
                    Image.fromarray(np.random.default_rng(100 + ci*20+i).integers(0,256,(200,200,3),dtype=np.uint8)).save(path)
                    paths.append(path)
                originals[category] = paths
                self.launch('paths', tuple(map(str, paths)))
                state = self.finished()
                self.assertEqual((state['imported'], state['training_ready']), (4,4))
            self.assertEqual(set(self.dataset.snapshot()['counts']['eligible_by_category'].values()), {4})
            candidate = self.candidate_window()
            candidate.ack.toggle()
            self.assertEqual(candidate.start_button.cget('state'), 'disabled')
            stages.append({'stage':'four_each','summary':candidate.summary})
            candidate.destroy(); self.view._candidate_window = None

            for ci, (category, kind) in enumerate(zip(categories, ('paths','folder','zip'))):
                self.category = category
                self.view.show_category(category)
                folder = self.root_path / f'mixture-{ci}';folder.mkdir()
                for name, original in [('skip.png', originals[category][0]), ('add.png', originals[category][1])]:
                    (folder/name).write_bytes(original.read_bytes())
                new = folder/'new.png'
                if kind == 'folder':
                    with Image.open(originals[category][2]) as image:
                        pixels = np.array(image)
                    pixels[0,0,0] ^= 1  # Different SHA, same dHash; useful view remains distinct.
                else:
                    pixels = np.random.default_rng(1000+ci).integers(0,256,(200,200,3),dtype=np.uint8)
                Image.fromarray(pixels).save(new)
                (folder/'copy-new.png').write_bytes(new.read_bytes())
                if kind == 'paths': (folder/'corrupt.png').write_bytes(b'corrupt disposable photo')
                source = tuple(map(str,folder.iterdir())) if kind=='paths' else str(folder)
                if kind=='zip':
                    archive=self.root_path/'mixture.zip'
                    with zipfile.ZipFile(archive,'w') as z:
                        for p in folder.iterdir(): z.write(p,p.name)
                    source=str(archive)
                entered, release = threading.Event(), threading.Event()
                self.releases.append(release)
                save = self.dataset._import_bytes
                def slow_save(*args, **kwargs):
                    entered.set();release.wait(10)
                    return save(*args, **kwargs)
                with patch.object(self.dataset,'_import_bytes',side_effect=slow_save):
                    self.launch(kind,source)
                    self.assertTrue(self.view.import_progress.activity._loop_running)
                    self.wait(lambda: self.view.import_progress.job.snapshot()['phase']=='waiting_duplicates')
                    self.choose_review(lambda r: 'keep_both' if r['match_type']=='similar' else
                                       'add_source' if r['incoming_source'].endswith('add.png') else 'skip')
                    self.wait(entered.is_set)
                    panel=self.view.import_progress
                    self.pump(150);motion=panel.activity._indeterminate_value;self.pump(150)
                    self.assertNotEqual(motion,panel.activity._indeterminate_value)
                    self.assertIn('files (',panel.detail.cget('text'))
                    release.set();state=self.finished()
                self.assertEqual((state['imported'],state['duplicates'],state['source_added'],state['skipped']), (1,3,1,2))
                self.assertEqual(state['failed'],int(kind=='paths'))
                records=[r for r in self.dataset.snapshot()['records'] if r['category']==category]
                self.assertEqual((len(records),sum(len(r['sources']) for r in records)), (5,6))
                self.assertTrue(all(r['training_eligible'] and r['status']!='approved' for r in records))
                stages.append({'stage':kind,'state':state})

            # Cancel after an additional source was saved; never invent a sixth eligible photo.
            self.category='campus_uniform';self.view.show_category(self.category)
            extra=self.root_path/'cancel-source.png';extra.write_bytes(originals[self.category][0].read_bytes())
            extra2=self.root_path/'retry-source.png';extra2.write_bytes(originals[self.category][1].read_bytes())
            save=self.dataset._import_bytes
            def cancel_after_save(*a,**k):
                result=save(*a,**k)
                self.view.import_progress.job.cancel()
                return result
            with patch.object(self.dataset,'_import_bytes',side_effect=cancel_after_save):
                self.launch('paths',(str(extra),str(extra2)))
                self.choose_review(lambda r:'add_source')
                cancelled=self.finished()
            self.assertEqual((cancelled['source_added'],cancelled['unprocessed']), (1,1))
            self.assertTrue(cancelled['cancelled'])
            # A failed source save is accurately counted; retry is idempotent for the first.
            def fail_second(data,path,*a,**k):
                if path==str(extra2): raise OSError('injected disposable source failure')
                return save(data,path,*a,**k)
            with patch.object(self.dataset,'_import_bytes',side_effect=fail_second):
                self.launch('paths',(str(extra),str(extra2)))
                self.choose_review(lambda r:'add_source');failed=self.finished()
            self.assertEqual((failed['source_added'],failed['failed']), (0,1))
            self.launch('paths',(str(extra),str(extra2)))
            self.choose_review(lambda r:'add_source');retry=self.finished()
            self.assertEqual((retry['source_added'],retry['failed']), (1,0))
            stages.extend([{'stage':'cancel','state':cancelled},{'stage':'failure','state':failed},{'stage':'retry','state':retry}])
            # Explicitly move a canonical category, reprocess, then restore it via UI.
            for category in ('organizational_shirt','campus_uniform'):
                self.category=category;self.view.show_category(category)
                self.launch('paths',(str(originals['campus_uniform'][0]),))
                self.choose_review(lambda r:'change_category')
                conflict=self.finished();self.assertEqual(conflict['training_ready'],1)
            snapshot=self.dataset.snapshot()
            self.assertEqual(set(snapshot['counts']['eligible_by_category'].values()),{5})
            self.view.destroy();self.view=self.open_view()
            candidate=self.candidate_window()
            self.assertEqual(set(candidate.summary['included_by_category'].values()),{5})
            self.assertEqual(candidate.start_button.cget('state'),'disabled')
            candidate.ack.toggle()
            self.assertEqual(candidate.start_button.cget('state'),'normal')
            for name,value in [('epochs','1'),('batch','2'),('threads','1')]:candidate.options[name].set(value)
            service=candidate.service
            candidate.start_button.invoke()  # Real freeze, subprocess, optimizer and checkpoint.
            self.wait(lambda: service.status().get('status') not in {'idle','preparing'},timeout=30)
            initial=service.status();run_id=initial['run_id'];run_dir=service.candidates_root/run_id
            self.wait(lambda: service.status(run_id)['status'] in {'completed','failed','cancelled'} and not service.is_busy(),timeout=180)
            final=service.status(run_id)
            for name in ('status.json','config.json','dataset_manifest.json','train.log','metrics.json','last_checkpoint.json','limitations.txt'):
                if (run_dir/name).exists():shutil.copyfile(run_dir/name,evidence/name)
            (evidence/'integration.json').write_text(json.dumps({'stages':stages,'initial_job':initial,'final_job':final,
                'counts':snapshot['counts'],'protected_before':before,'protected_after':fingerprints()},indent=2))
            self.assertEqual(final['status'],'completed', (run_dir/'train.log').read_text())
            self.assertEqual(final['epoch'],1)
            self.assertTrue((run_dir/'candidate.pt').is_file())
            self.assertTrue(final['nondeployable'])
            self.assertIn('pid=',(run_dir/'train.log').read_text())
            candidate.destroy();self.assertIsNone(candidate._poll)
            self.assertIsNone(candidate.task._poll_id)
            self.assertEqual(fingerprints(),before)
