"""Five-photo eligibility through real disposable imports, processing and freezing."""
import hashlib
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from core.uniform_candidate import CandidateService, assign_group_splits
from core.uniform_dataset import UniformDataset

GARMENT_CATEGORIES = ('campus_uniform', 'organizational_shirt', 'other_clothing')


class TrainingImportMinimumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = UniformDataset(self.root / 'dataset', auto_process=False)
        self.base = self.root / 'weights.pt'
        self.base.write_bytes(b'disposable freeze fixture; worker launch is mocked')
        self.service = CandidateService(self.dataset, self.root / 'candidates', self.base)
        self.detector = Mock(use_pose=True)
        self.detector.detect_persons.return_value = [[0, 0, 200, 200]]
        self.detector.pose_torso_box.return_value = [20, 20, 180, 180]

    def populate(self, counts):
        self.photos = {}
        for class_index, (category, count) in enumerate(zip(GARMENT_CATEGORIES, counts)):
            paths = []
            for index in range(count):
                path = self.root / f'{category}-{index}.png'
                pixels = np.random.default_rng(class_index * 100 + index).integers(
                    0, 256, (200, 200, 3), dtype=np.uint8)
                Image.fromarray(pixels).save(path)
                paths.append(path)
            self.photos[category] = paths
            self.dataset.import_paths(paths, category)
        self.dataset.process_pending(detector=self.detector)
        for row in self.dataset.snapshot()['records']:
            self.assertTrue(row['training_eligible'])
            self.assertEqual(row['crop'], [20, 20, 180, 180])
            self.assertEqual(row['review_history'], [])

    def wait_for_prepare(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.service._handles_guard:
                preparing = bool(self.service._preparing)
            if not preparing and not self.service.is_busy():
                return
            time.sleep(.01)
        self.fail('Disposable candidate preparation did not finish')

    def test_four_in_any_category_blocks_launch_even_with_exact_copies(self):
        self.populate((5, 5, 5))
        original = self.dataset._load()
        for category in GARMENT_CATEGORIES:
            with self.subTest(category=category):
                catalog = json.loads(json.dumps(original))
                next(r for r in catalog['records'] if r['category'] == category)['status'] = 'excluded'
                self.dataset._save(catalog)
                # Different filenames, identical source bytes, already eligible source.
                source = self.photos[category][1]
                for i in range(3):
                    copy = self.root / f'copy-{i}.png'
                    copy.write_bytes(source.read_bytes())
                    result = self.dataset.import_paths([copy], category)
                    self.assertEqual((result.imported, result.duplicates), (0, 1))
                snap = self.dataset.snapshot()
                self.assertEqual(snap['counts']['eligible_by_category'][category], 4)
                self.assertFalse(snap['experimental_training_eligible'])
                self.assertFalse(self.service.summary()['eligible'])
                with patch('core.uniform_candidate.subprocess.Popen') as popen:
                    run_id = self.service.start()
                    self.wait_for_prepare()
                    popen.assert_not_called()
                state = self.service.status(run_id)
                self.assertEqual(state['status'], 'failed')
                self.assertIn('4 eligible unique photos; requires 5', state['message'])

    def test_five_each_freezes_and_launches_normal_worker(self):
        self.populate((5, 5, 5))
        expected = {c: 5 for c in GARMENT_CATEGORIES}
        self.assertTrue(self.dataset.snapshot()['experimental_training_eligible'])
        self.assertEqual(self.service.summary()['included_by_category'], expected)
        self.assertTrue(self.service.summary()['eligible'])
        child = Mock(pid=12345)
        child.wait.return_value = child.poll.return_value = 0
        with patch('core.uniform_candidate.subprocess.Popen', return_value=child) as popen:
            run_id = self.service.start({'epochs': 1})
            self.wait_for_prepare()
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0][-1], 'start')
        run_dir = self.service._run_dir(run_id)
        self.service._verify_bundle(run_dir)
        config = json.loads((run_dir / 'config.json').read_text())
        self.assertEqual(config['min_per_class'], 5)
        self.assertFalse(config['synthetic_smoke'])
        manifest = json.loads((run_dir / 'dataset_manifest.json').read_text())
        self.assertEqual(manifest['eligibility_snapshot']['included_by_category'], expected)
        self.assertEqual(len(manifest['entries']), 15)
        self.assertEqual(manifest['split_distribution']['train'], expected)

    def test_six_thirty_eight_seven_passes_snapshot_summary_and_freeze(self):
        self.populate((6, 38, 7))
        expected = dict(zip(GARMENT_CATEGORIES, (6, 38, 7)))
        snap = self.dataset.snapshot()
        self.assertEqual(snap['counts']['eligible_by_category'], expected)
        self.assertTrue(snap['experimental_training_eligible'])
        self.assertTrue(self.service.summary()['eligible'])
        run_dir = self.service._run_dir('a' * 32)
        run_dir.mkdir()
        frozen = self.service._freeze(run_dir, self.service.validate_config())
        self.assertEqual(frozen['included'], 51)
        self.assertEqual(frozen['config']['eligibility_snapshot']['included_by_category'], expected)
        self.service._verify_bundle(run_dir)

    def test_pending_failed_quarantined_do_not_satisfy_fifth_photo(self):
        self.populate((5, 5, 5))
        original = self.dataset._load()
        for state in ('queued', 'processing', 'failed', 'quarantined'):
            with self.subTest(state=state):
                catalog = json.loads(json.dumps(original))
                row = catalog['records'][0]
                row['processing_state'] = state
                self.dataset._save(catalog)
                self.assertEqual(self.dataset.snapshot()['counts']['eligible_by_category'][row['category']], 4)
                self.assertFalse(self.service.summary()['eligible'])

    def test_resume_preserves_explicit_ten_and_frozen_bundle(self):
        import torch
        from core.uniform_candidate_worker import _checkpoint
        self.populate((10, 10, 10))
        run_id = 'b' * 32
        run_dir = self.service._run_dir(run_id)
        run_dir.mkdir()
        self.service._freeze(run_dir, self.service.validate_config({'min_per_class': 10, 'epochs': 3}))
        saved_config = (run_dir / 'config.json').read_bytes()
        saved_manifest = (run_dir / 'dataset_manifest.json').read_bytes()
        config = json.loads(saved_config)
        metadata = {'run_uuid': run_id, 'mapping_version': config['mapping_version'],
                    'dataset_manifest_sha256': config['dataset_manifest_sha256'],
                    'base_weights_sha256': config['base_weights_sha256'],
                    'config_sha256': hashlib.sha256(saved_config).hexdigest()}
        model = torch.nn.Linear(4, 3)
        _checkpoint(torch, random, np, model, torch.optim.AdamW(model.parameters()), 1, run_dir, metadata)
        self.service._status_write(run_dir, {'status': 'cancelled'})
        # Resume must work even if the current catalog and original weights no longer qualify.
        catalog = self.dataset._load()
        catalog['records'] = []
        self.dataset._save(catalog)
        self.base.unlink()
        child = Mock(pid=12345)
        child.wait.return_value = child.poll.return_value = 0
        with patch.object(self.service, '_freeze', side_effect=AssertionError('must not refreeze')), \
                patch.object(self.service, 'validate_config', side_effect=AssertionError('must not use new defaults')), \
                patch('core.uniform_candidate.subprocess.Popen', return_value=child) as popen:
            self.assertEqual(self.service.resume(run_id), run_id)
            self.assertEqual(popen.call_args.args[0][-1], 'resume')
        self.assertEqual((run_dir / 'config.json').read_bytes(), saved_config)
        self.assertEqual((run_dir / 'dataset_manifest.json').read_bytes(), saved_manifest)
        self.assertEqual(config['min_per_class'], 10)
        self.assertEqual(self.service.status(run_id)['epochs'], 3)

    def test_holdout_uses_configured_training_minimum(self):
        entries = [{'image_id': f'{c}-{i}', 'category': c, 'group_ids': [f'{c}-{i}']}
                   for c in GARMENT_CATEGORIES for i in range(7)]
        splits, available, _ = assign_group_splits(entries, seed=42)
        self.assertTrue(available)
        for category in GARMENT_CATEGORIES:
            self.assertEqual(sum(splits[r['image_id']] == 'train' for r in entries
                                 if r['category'] == category), 5)
        splits, available, note = assign_group_splits(entries, seed=42, min_per_class=10)
        self.assertFalse(available)
        self.assertEqual(set(splits.values()), {'train'})
        self.assertIn('fewer than 10', note)
