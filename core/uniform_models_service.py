"""Background-only orchestration for independent evaluation and explicit deployments."""
from contextlib import contextmanager
from pathlib import Path
import threading

from core.uniform_candidate import _TrainingLock
from core.uniform_evaluation import EvaluationService
from core.uniform_model_registry import (ROOT, UniformModelRegistry, DeploymentPredictor,
    CandidatePredictor, candidate_descriptor, policy_rule, json_hash, read_json, atomic_json, digest, safe_file)


class UniformModelsService:
    def __init__(self, *, registry=None, evaluator=None, candidates_root=None, actor_provider=None):
        self.evaluator = evaluator or EvaluationService()
        self.registry = registry or UniformModelRegistry(actor_provider=actor_provider, evaluation_service=self.evaluator)
        self.registry.evaluation_service = self.evaluator
        self.candidates_root = Path(candidates_root or ROOT/'data'/'uniform_candidates')
        self._operation = threading.Lock()

    def inventory(self):
        current = self.registry.current()
        candidates = []
        for path in sorted(self.candidates_root.iterdir()) if self.candidates_root.exists() else []:
            if path.is_symlink() or not path.is_dir() or not (path/'status.json').is_file():
                continue
            try:
                status = read_json(path/'status.json')
                config = read_json(path/'config.json')
                candidates.append({'run_id':path.name,'path':str(path),'status':status.get('status'),
                    'id':digest(safe_file(path,'candidate.pt')) if (path/'candidate.pt').is_file() else None,
                    'synthetic':bool(config.get('synthetic_smoke')),
                    'dataset_version':config.get('dataset_manifest_sha256'),
                    'preprocessing':config.get('model_preprocess')})
            except (ValueError,OSError) as exc:
                candidates.append({'run_id':path.name,'status':'invalid','error':str(exc)})
        try:
            self.registry.actor()
            authorized = True
        except PermissionError:
            authorized = False
        try:
            self.descriptor(current)
            provenance = 'Reviewed training inventory available; overlap checks run before evaluation.'
        except (ValueError,OSError) as exc:
            provenance = 'Independent evaluation blocked: '+str(exc)
        return {'active':current,'token':self.registry.token(),'pointer':self.registry.pointer(),
                'recovery_available':(self.registry.root/'switch-journal.json').is_file(),
                'candidates':candidates,'sets':self.evaluator.list_sets(),
                'reports':self.evaluator.list_reports(),'authorized':authorized,'provenance':provenance}

    def _inventory_path(self, model_id):
        return self.evaluator.root/'training_inventories'/(json_hash({'model_id':model_id})+'.json')

    def import_inventory(self, path, model_id):
        actor = self.registry.actor()
        value = read_json(path)
        if (value.get('model_id')!=model_id or value.get('complete') is not True
                or not str(value.get('reviewed_by','')).strip() or not str(value.get('statement','')).strip()):
            raise ValueError('Provide a complete, reviewed training inventory for the selected model ID')
        descriptor = {'id':model_id,'labels':['inventory'],'preprocessing_version':'inventory',
                      'synthetic':False, **{k:value.get(k) for k in ('training_hashes','training_groups','training_dhashes')}}
        self.evaluator._validate_descriptor(descriptor,'training inventory')
        target = self._inventory_path(model_id)
        if target.parent.is_symlink(): raise ValueError('Unsafe inventory storage')
        target.parent.mkdir(exist_ok=True,mode=0o700)
        value['imported_by'] = actor['username']
        if target.exists() and read_json(target)!=value:
            raise ValueError('Training inventory already frozen for this model; review the existing inventory')
        atomic_json(target,value)
        return model_id

    def descriptor(self, info):
        target = self._inventory_path(info['id'])
        if not target.is_file():
            raise ValueError('Missing complete reviewed training hashes, capture groups and near-duplicate inventory for '+info['id'])
        inventory = read_json(target)
        if inventory.get('model_id')!=info['id'] or inventory.get('complete') is not True:
            raise ValueError('Training inventory identity mismatch')
        result = {**info,**{k:inventory.get(k) for k in ('training_hashes','training_groups','training_dhashes')}}
        if info.get('kind')=='candidate' or info.get('mapping_version')=='uniform-garments-v1':
            if set(result['training_hashes'])!=set(info['training_hashes']):
                raise ValueError('Reviewed inventory does not cover exactly the frozen candidate training images')
            if not set(info['training_groups']) <= set(result['training_groups']):
                raise ValueError('Reviewed inventory loses known candidate capture groups')
        result['policy_version'] = json_hash(info['policy']) if 'policy' in info else None
        return self.evaluator._validate_descriptor(result,'model')

    def import_set(self,path):
        self.registry.actor()
        return self.evaluator.import_set(path)

    def evaluate(self, path, set_id, threshold, *, cancelled=None, progress=None):
        self.registry.actor()
        if not self._operation.acquire(blocking=False):
            raise RuntimeError('Another model operation is running')
        try:
            # Reuse the existing training slot through inference to prevent a
            # competing job from starting between the check and model loading.
            with self._training_slot():
                token = self.registry.token()
                base = self.registry.current()
                candidate = candidate_descriptor(path)
                rule = policy_rule(threshold)
                candidate['policy'] = rule
                base_desc,candidate_desc = self.descriptor(base),self.descriptor(candidate)
                # Validate/freeze prerequisites before expensive model initialization.
                dataset = self.evaluator.load_set(set_id)
                if dataset['independence']['baseline_id']!=base['id']:
                    raise ValueError('Independent dataset was reviewed for a different baseline')
                if self.evaluator._overlap_check(dataset,base_desc,candidate_desc)['possible_overlap']:
                    raise ValueError('Evaluation overlaps a model training inventory')
                if cancelled is not None and cancelled.is_set():
                    raise RuntimeError('Evaluation cancelled before model loading')
                baseline_predict = DeploymentPredictor(base)
                candidate_predict = CandidatePredictor(path,rule)
                def stop():
                    return ((cancelled is not None and cancelled.is_set()) or not self.registry.matches(token))
                return self.evaluator.evaluate(set_id,base_desc,candidate_desc,baseline_predict,candidate_predict,
                                               cancelled=stop,progress=progress)
        finally:
            self._operation.release()

    @contextmanager
    def _training_slot(self):
        slot = _TrainingLock(self.candidates_root)
        if not slot.acquire():
            raise RuntimeError('Training or evaluation is already using the candidate slot')
        try:
            yield
        finally:
            slot.release()

    def report(self,report_id):
        report = self.evaluator.load_report(report_id)
        return {'report':report,'checksum':self.evaluator.report_checksum(report_id)}

    def activate(self,path,report_id,threshold,review,token):
        return self.registry.activate(path,report_id,policy_rule(threshold),review,expected_token=token)

    def rollback(self,token,reason):
        return self.registry.rollback(expected_token=token,reason=reason)
