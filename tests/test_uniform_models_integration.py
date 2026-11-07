"""Stage 3 orchestration/runtime contract tests; disposable files and model doubles."""
import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np

from core.uniform_models_service import UniformModelsService
from core.uniform_model_registry import UniformModelRegistry, PREPROCESS, json_hash, policy_rule
from core.uniform_evaluation import EvaluationService
from core.uniform_candidate import _TrainingLock
from core.trainer import ViolationTrainer
from tests.test_live_pipeline import PipelineFixture
from tests import test_uniform_evaluation as evaluation_fixtures


class ServiceTests(unittest.TestCase):
    def setUp(self):
        # Reuse image/manifest construction only, not the evaluator test suite.
        self.fx=evaluation_fixtures.EvaluationFixture()
        self.fx.setUp()
        self.root=self.fx.root
        self.actor={'role':'superadmin','username':'fixture-reviewer'}
        self.registry=UniformModelRegistry(self.root/'registry',actor_provider=lambda:self.actor)
        self.service=UniformModelsService(registry=self.registry,evaluator=self.fx.service,candidates_root=self.root/'candidates')
        self.base={**self.fx.baseline,'kind':'legacy','policy':{'kind':'fixture-baseline'}}
        self.candidate={**self.fx.candidate,'path':str(self.root/'fake-candidate'),'mapping_version':'uniform-garments-v1'}
        for info in (self.base,self.candidate):
            data={k:info[k] for k in ('training_hashes','training_groups','training_dhashes')}
            data.update(model_id=info['id'],complete=True,reviewed_by='human-fixture',statement='Synthetic test inventory declaration')
            path=self.root/(info['id']+'.json');path.write_text(json.dumps(data))
            self.service.import_inventory(path,info['id'])

    def tearDown(self): self.fx.tearDown()

    def run_evaluation(self,**kwargs):
        baseline,candidate=self.fx._predictors()
        with patch.object(self.registry,'current',return_value=self.base), \
             patch('core.uniform_models_service.candidate_descriptor',return_value=self.candidate), \
             patch('core.uniform_models_service.DeploymentPredictor',return_value=baseline), \
             patch('core.uniform_models_service.CandidatePredictor',return_value=candidate):
            return self.service.evaluate(self.candidate['path'],self.fx.set_id,.8,**kwargs)

    def test_paired_service_report_and_training_exclusion(self):
        checks=[]
        def progress(*args):
            slot=_TrainingLock(self.service.candidates_root)
            self.assertFalse(slot.acquire())
            checks.append(args)
        report=self.service.report(self.run_evaluation(progress=progress))
        self.assertEqual(report['report']['status'],'completed')
        self.assertEqual(len(checks),3)
        self.assertEqual(report['report']['candidate']['policy_version'],json_hash(policy_rule(.8)))
        self.assertFalse((self.registry.root/'active.json').exists())
        slot=_TrainingLock(self.service.candidates_root);self.assertTrue(slot.acquire());slot.release()

    def test_existing_training_and_missing_provenance_block_before_model_load(self):
        slot=_TrainingLock(self.service.candidates_root);self.assertTrue(slot.acquire())
        try:
            with self.assertRaisesRegex(RuntimeError,'candidate slot'):self.run_evaluation()
        finally:slot.release()
        self.service._inventory_path(self.base['id']).unlink()
        with patch('core.uniform_models_service.DeploymentPredictor') as loader:
            with self.assertRaisesRegex(ValueError,'Missing complete reviewed'):self.run_evaluation()
            loader.assert_not_called()

    def test_cancellation_and_changed_baseline_stop_paired_work(self):
        cancel=threading.Event()
        report_id=self.run_evaluation(cancelled=cancel,progress=lambda *args:cancel.set())
        report=self.service.report(report_id)['report']
        self.assertEqual(report['status'],'cancelled')
        self.assertEqual(len(report['predictions']),1)
        self.assertIsNone(report['metrics'])
        with patch.object(self.registry,'matches',return_value=False):
            report_id=self.run_evaluation()
        self.assertEqual(self.service.report(report_id)['report']['status'],'cancelled')

    def test_inventory_is_bound_complete_authorized_and_immutable(self):
        with self.assertRaisesRegex(ValueError,'cover exactly'):
            self.service.descriptor({**self.candidate,'training_hashes':['a'*64]})
        path=self.root/'altered.json'
        data=json.loads(self.service._inventory_path(self.base['id']).read_text())
        data['training_groups']=['another-session'];path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'already frozen'):self.service.import_inventory(path,self.base['id'])
        self.actor=None
        with self.assertRaises(PermissionError):self.service.import_set(self.fx.manifest_path)


class VersionedTrainer:
    def __init__(self):self.version='v1';self.available=True
    def uniform_version(self):return self.version
    def uniform_version_matches(self,token):return token==self.version
    def is_trained(self,module):return module=='uniform'
    def uniform_decision(self,crop,matcher,token):
        return {'policy_label':'violation','policy_confidence':.9}
    @contextmanager
    def uniform_result_guard(self,token):yield self.uniform_version_matches(token)


class RuntimeTests(unittest.TestCase):
    def test_versioned_startup_and_cached_loader_use_selected_bundle(self):
        from core.monitor_models import MonitorModels
        with tempfile.TemporaryDirectory() as temp:
            trainer=ViolationTrainer();trainer.uniform_registry=UniformModelRegistry(Path(temp)/'registry')
            loaded=Mock(return_value={'policy_label':'compliant','policy_confidence':.9})
            matcher=Mock()
            with patch.object(trainer.uniform_registry,'token',return_value='new-deployment'), \
                 patch.object(trainer.uniform_registry,'current',return_value={'fixture':True}), \
                 patch('core.uniform_model_registry.DeploymentPredictor',return_value=loaded) as loader:
                bundle=MonitorModels(Mock(),Mock(),trainer,matcher)
                self.assertTrue(bundle._load_uniform())
                trainer.uniform_decision(np.zeros((20,30,3),np.uint8),matcher,'new-deployment')
                self.assertEqual(loader.call_count,1)
                self.assertEqual(trainer._uniform_deployment_token,'new-deployment')

    def test_model_switch_rejects_old_results_and_preserves_identity_and_cooldowns(self):
        fx=PipelineFixture();trainer=VersionedTrainer();fx.processor.trainer=trainer
        result=fx.confirmed()
        self.assertEqual(result.task.uniform_model_version,'v1')
        track=fx.processor.state._tracks[0]
        old_id,old_hits,earrings=track.candidate_id,track.identity_hits,tuple(track.earrings)
        fx.processor.cooldowns['fixture']=123
        trainer.version='v2'
        with patch('core.live_pipeline.time.monotonic',return_value=fx.now):
            self.assertFalse(result.task.valid())
            self.assertEqual(result.task.rejection(),'uniform_model_changed')
        fx.persist(result);fx.database.log_violation.assert_not_called()
        fresh=fx.analyze(5)
        self.assertEqual(fresh.task.uniform_model_version,'v2')
        self.assertEqual(track.candidate_id,old_id)
        self.assertGreaterEqual(track.identity_hits,old_hits)
        self.assertEqual(tuple(track.earrings)[:len(earrings)],earrings)
        self.assertLessEqual(len(track.uniform),1)
        self.assertEqual(fx.processor.cooldowns['fixture'],123)

    def test_trainer_legacy_policy_and_versioned_loader_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            trainer=ViolationTrainer();trainer.uniform_registry=UniformModelRegistry(Path(temp)/'registry')
            matcher=Mock();matcher.is_loaded.return_value=False
            with patch.object(trainer,'is_trained',return_value=True),patch.object(trainer,'predict_proba',return_value={'correct_uniform':.9}):
                result=trainer.uniform_decision(np.zeros((20,30,3),np.uint8),matcher,'legacy-default')
                self.assertEqual(result['policy_label'],'compliant')
            with patch.object(trainer,'is_trained',return_value=True),patch.object(trainer,'predict_proba',return_value=None):
                self.assertIsNone(trainer.uniform_decision(np.zeros((20,30,3),np.uint8),matcher,'legacy-default')['policy_label'])
            with self.assertRaisesRegex(ValueError,'version changed'):
                trainer.uniform_decision(np.zeros((20,30,3),np.uint8),matcher,'stale')
            with patch.object(trainer.uniform_registry,'matches',return_value=True),patch.object(trainer.uniform_registry,'current',return_value={}),patch('core.uniform_model_registry.DeploymentPredictor',side_effect=ValueError('invalid bundle')):
                with self.assertRaisesRegex(ValueError,'invalid bundle'):trainer.uniform_decision(np.zeros((20,30,3),np.uint8),matcher,'deployment')
            self.assertIsNone(trainer._uniform_deployment_token)


if __name__=='__main__':unittest.main()
