import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, Mock
import numpy as np
from PIL import Image
from core.uniform_dataset import UniformDataset, visually_similar
from core.uniform_import import start_import


class DuplicateImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ds = UniformDataset(self.root / 'catalog', auto_process=False)

    def photo(self, name, seed=0):
        path = self.root / name
        Image.fromarray(np.random.default_rng(seed).integers(0, 256, (200,200,3), dtype=np.uint8)).save(path)
        return path

    def wait(self, job, phase=None):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            state = job.snapshot()
            if state['phase'] == phase or phase is None and not state['active']:
                return state
            time.sleep(.01)
        self.fail(str(job.snapshot()))

    def choose(self, job, action):
        self.wait(job, 'waiting_duplicates')
        for row in job.duplicate_rows():
            job.set_duplicate_choice(row['row_id'], action)
        job.submit_duplicate_choices()
        return self.wait(job)

    def test_exact_defaults_skip_preserves_record_add_source_is_idempotent(self):
        p = self.photo('first.png'); copy = self.root/'copy.png'; copy.write_bytes(p.read_bytes())
        self.ds.import_paths([p], 'campus_uniform')
        before = self.ds._load()
        job = start_import(self.ds, 'paths', [copy], 'campus_uniform')
        self.wait(job, 'waiting_duplicates'); self.assertEqual(set(job.duplicate_choices().values()), {'skip'})
        job.submit_duplicate_choices(); state=self.wait(job)
        self.assertEqual(self.ds._load(), before)
        self.assertEqual((state['duplicates'],state['skipped']), (1,1))
        for added in (1,0):
            state=self.choose(start_import(self.ds,'paths',[copy],'campus_uniform'),'add_source')
            self.assertEqual(state['source_added'],added)
        self.assertEqual(len(self.ds.snapshot()['records'][0]['sources']),2)

    def test_within_batch_and_folder_zip_paths(self):
        folder=self.root/'input';folder.mkdir()
        p=self.photo('a.png'); (folder/'a.png').write_bytes(p.read_bytes());(folder/'b.png').write_bytes(p.read_bytes())
        archive=self.root/'input.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for path in folder.iterdir(): z.write(path,path.name)
        for kind,source in [('folder',folder),('zip',archive)]:
            job=start_import(self.ds,kind,source,'campus_uniform')
            state=self.choose(job,'add_source')
            self.assertEqual(state['failed'],0)
        self.assertEqual(len(self.ds.snapshot()['records']),1)
        self.assertFalse(self.ds.snapshot()['experimental_training_eligible'])

    def test_conflict_all_choices_and_reprocessing(self):
        p=self.photo('a.png');self.ds.import_paths([p],'campus_uniform')
        before=self.ds._load()
        state=self.choose(start_import(self.ds,'paths',[p],'organizational_shirt'),'skip')
        self.assertEqual(self.ds._load(),before)
        self.choose(start_import(self.ds,'paths',[p],'organizational_shirt'),'use_existing')
        self.assertEqual(self.ds.snapshot()['records'][0]['category'],'campus_uniform')
        self.choose(start_import(self.ds,'paths',[p],'organizational_shirt'),'change_category')
        row=self.ds.snapshot()['records'][0]
        self.assertEqual(row['category'],'organizational_shirt');self.assertEqual(row['processing_state'],'queued')
        self.assertNotIn('label_conflict',row['flags'])
        detector=Mock(use_pose=True);detector.detect_persons.return_value=[[0,0,200,200]];detector.pose_torso_box.return_value=[20,20,180,180]
        self.ds.process_pending(detector=detector)
        self.assertTrue(self.ds.snapshot()['records'][0]['training_eligible'])

    def test_similar_choices_and_inspection_correction(self):
        a=self.root/'a.png';b=self.root/'b.png'
        Image.new('RGB',(100,100),'red').save(a);Image.new('RGB',(100,100),'blue').save(b)
        first=self.ds.import_paths([a],'campus_uniform')[0]
        job=start_import(self.ds,'paths',[b],'campus_uniform')
        self.wait(job,'waiting_duplicates');self.assertEqual(job.duplicate_rows()[0]['match_type'],'similar')
        self.choose(job,'skip');self.assertEqual(len(self.ds.snapshot()['records']),1)
        self.choose(start_import(self.ds,'paths',[b],'campus_uniform'),'keep_both')
        rows=self.ds.snapshot()['records'];second=next(r['image_id'] for r in rows if r['image_id']!=first)
        self.assertEqual(rows[0]['duplicate_group_size'],2)
        self.ds.review(first,category='campus_uniform',crop=[0,0,80,80],status='pending_review',unrelated_image_ids=[second])
        rows=self.ds.snapshot()['records'];self.assertFalse(visually_similar(*rows));self.assertEqual(rows[0]['duplicate_group_size'],1)

    def test_catalog_race_rejected_and_retry_partial_failure(self):
        p=self.photo('a.png');q=self.photo('b.png',99)
        save=self.ds._import_bytes
        def fail_second(data,*args,**kwargs):
            if args[0]==str(q):raise OSError('disposable injected failure')
            return save(data,*args,**kwargs)
        with patch.object(self.ds,'_import_bytes',side_effect=fail_second):
            state=self.wait(start_import(self.ds,'paths',[p,q],'campus_uniform'))
        self.assertEqual((state['imported'],state['failed']),(1,1))
        job=start_import(self.ds,'paths',[p,q],'campus_uniform');self.wait(job,'waiting_duplicates')
        job.submit_duplicate_choices();state=self.wait(job);self.assertEqual((state['imported'],state['skipped']),(1,1))
        job=start_import(self.ds,'paths',[p],'organizational_shirt');self.wait(job,'waiting_duplicates')
        with self.ds._locked():
            cat=self.ds._load();cat['records'][0]['category']='other_clothing';self.ds._save(cat)
        state=self.choose(job,'change_category');self.assertEqual(state['failed'],1)
        self.assertIn('Dataset changed',state['error'])

    def test_cancel_waiting_and_after_save_retains_partial(self):
        a=self.photo('a.png');b=self.root/'b.png';b.write_bytes(a.read_bytes())
        job=start_import(self.ds,'paths',[a,b],'campus_uniform');self.wait(job,'waiting_duplicates');job.cancel()
        self.assertTrue(self.wait(job)['cancelled']);self.assertEqual(self.ds.snapshot()['records'],[])
        save=self.ds._import_bytes
        def cancel_after(*args,**kwargs):
            result=save(*args,**kwargs);job.cancel();return result
        job=start_import(self.ds,'paths',[a,b],'campus_uniform');self.wait(job,'waiting_duplicates')
        with patch.object(self.ds,'_import_bytes',side_effect=cancel_after):
            job.submit_duplicate_choices();state=self.wait(job)
        self.assertEqual((state['imported'],state['unprocessed']),(1,1))

    def test_four_eligible_cannot_become_five_through_added_sources(self):
        photos=[self.photo(f'{i}.png',i+20) for i in range(4)]
        self.ds.import_paths(photos,'campus_uniform')
        detector=Mock(use_pose=True);detector.detect_persons.return_value=[[0,0,200,200]];detector.pose_torso_box.return_value=[20,20,180,180]
        self.ds.process_pending(detector=detector)
        copies=[]
        for i in range(8):
            p=self.root/f'copy{i}.png';p.write_bytes(photos[0].read_bytes());copies.append(p)
        self.choose(start_import(self.ds,'paths',copies,'campus_uniform'),'add_source')
        snapshot=self.ds.snapshot()
        self.assertEqual(snapshot['counts']['eligible_by_category']['campus_uniform'],4)
        self.assertFalse(snapshot['experimental_training_eligible'])

    def test_processing_launch_failure_still_counts_completed_save(self):
        p=self.photo('a.png')
        self.ds.auto_process=True
        with patch.object(self.ds,'schedule_processing',side_effect=RuntimeError('disposable startup failure')):
            result=self.wait(start_import(self.ds,'paths',[p],'campus_uniform'))
        self.assertEqual((result['imported'],result['failed'],result['awaiting']),(1,0,1))
        self.assertIn('startup failure',result['error'])
        detector=Mock(use_pose=True);detector.detect_persons.return_value=[[0,0,200,200]];detector.pose_torso_box.return_value=[20,20,180,180]
        def resume():
            self.ds.process_pending(detector=detector)
            return Mock(is_alive=Mock(return_value=False))
        with patch.object(self.ds,'schedule_processing',side_effect=resume):
            result=self.choose(start_import(self.ds,'paths',[p],'campus_uniform'),'skip')
        self.assertEqual((result['imported'],result['skipped'],result['training_ready']),(0,1,1))
        self.assertEqual(len(self.ds.snapshot()['records'][0]['sources']),1)

