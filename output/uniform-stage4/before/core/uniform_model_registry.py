"""Immutable uniform deployments, atomic pointer switching and version guards.

Reading an absent registry uses the existing legacy assets without creating a
pointer. Mutation requires a freshly validated superadmin session and a reviewed
independent report. Tests use explicit isolated roots; no production test bypass.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid
from importlib.metadata import version as package_version

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / 'data' / 'uniform_models'
PREPROCESS = {'version':'trainer-letterbox-rgb-0-1-v1','size':224,'color':'RGB','range':[0.0,1.0],'augmentation':'none'}
LEGACY_PREPROCESS = {'version':'legacy-letterbox224-ultralytics-'+package_version('ultralytics')+'-v1',
    'letterbox_size':224,'backend':'ultralytics','backend_version':package_version('ultralytics'),
    'transforms':'checkpoint/default classification prediction transforms'}
CLASSES = ['campus_uniform','organizational_shirt','other_clothing']
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def read_json(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 32*1024*1024:
        raise ValueError('Unsafe or oversized model metadata')
    value = json.loads(path.read_text(),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if not isinstance(value,dict):
        raise ValueError('Model metadata must be an object')
    return value


def atomic_json(path,value):
    path = Path(path)
    fd,temp = tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True,indent=2,allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp,path)
        directory = os.open(path.parent,os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def safe_file(root,relative):
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('Model path escapes bundle')
    path = root
    for part in rel.parts:
        path /= part
        if path.is_symlink(): raise ValueError('Model bundle symlink rejected')
    if not path.is_file() or root not in path.resolve().parents:
        raise ValueError('Model bundle file missing')
    return path


def policy_rule(threshold):
    if type(threshold) not in (int,float) or not math.isfinite(threshold) or not .5 <= threshold <= 1:
        raise ValueError('Choose an explicit policy confidence between 0.5 and 1 before evaluation')
    return {'kind':'garment_probability_sum','accepted_classes':CLASSES[:2],
            'minimum_confidence':float(threshold),'version':'garment-policy-v1'}


def candidate_descriptor(path):
    """Validate completed original candidate without modifying it or unpickling it."""
    from core.uniform_candidate import CandidateService
    path = Path(path)
    if path.is_symlink(): raise ValueError('Candidate directory cannot be a symlink')
    path = path.resolve()
    service = CandidateService(ROOT/'data'/'uniform_stage1',candidates_root=path.parent)
    service._verify_bundle(path)
    service._checkpoint_epoch(path)
    state,config = read_json(path/'status.json'),read_json(path/'config.json')
    if state.get('status') != 'completed': raise ValueError('Candidate is not complete')
    checksums = read_json(path/'bundle_checksums.json')
    required = {'candidate.pt','base_weights.pt','class_map.json','config.json','dataset_manifest.json','architecture.json','limitations.txt','last_checkpoint.json'}
    if not required <= set(checksums): raise ValueError('Incomplete candidate bundle')
    for relative,expected in checksums.items():
        if digest(safe_file(path,relative)) != expected: raise ValueError('Candidate bundle checksum mismatch: '+relative)
    manifest = read_json(path/'dataset_manifest.json')
    if manifest.get('model_preprocess') != PREPROCESS: raise ValueError('Unsupported candidate preprocessing')
    if config.get('base_weights_sha256') != digest(ROOT/'yolov8n-cls.pt'):
        raise ValueError('Candidate backbone is not the installed trusted local architecture')
    return {'id':digest(path/'candidate.pt'),'path':str(path),'labels':CLASSES,
            'preprocessing_version':PREPROCESS['version'],'dataset_version':manifest['dataset_snapshot_sha256'],
            'training_hashes':[e['original_sha256'] for e in manifest['entries']],
            'training_groups':sorted({g for e in manifest['entries'] for g in e['group_ids']}),
            'training_dhashes':[], 'synthetic':bool(config.get('synthetic_smoke')),
            'mapping_version':config['mapping_version'],'limitations':manifest['limitations']}


class CandidatePredictor:
    """Read-only local state-dict inference; no training or downloads."""
    def __init__(self,path,rule=None):
        import torch
        from ultralytics.nn.tasks import ClassificationModel
        self.torch = torch
        self.path, self.rule = Path(path),rule
        if read_json(self.path/'config.json').get('base_weights_sha256') != digest(ROOT/'yolov8n-cls.pt'):
            raise ValueError('Installed backbone changed; candidate compatibility must be revalidated')
        weights = torch.load(self.path/'candidate.pt',map_location='cpu',weights_only=True)
        if weights.get('classes') != dict(enumerate(CLASSES)) or weights.get('preprocessing') != PREPROCESS:
            raise ValueError('Weights, class map and preprocessing disagree')
        if (weights.get('mapping_version')!='uniform-garments-v1' or weights.get('input_size')!=224
                or weights.get('architecture')!='ultralytics-yolov8n-classification'):
            raise ValueError('Candidate architecture, mapping version or input size is incompatible')
        model = torch.load(ROOT/'yolov8n-cls.pt',map_location='cpu',weights_only=False)['model'].float()
        if not isinstance(model,ClassificationModel) or model.yaml.get('scale') != 'n':
            raise ValueError('Unsupported installed architecture')
        model.model[-1].linear = torch.nn.Linear(model.model[-1].linear.in_features,3)
        model.load_state_dict(weights['state_dict'],strict=True)
        if any(t.is_floating_point() and not torch.isfinite(t).all() for t in weights['state_dict'].values()):
            raise ValueError('Candidate contains nonfinite weights')
        self.model = model.eval()
        self.model.requires_grad_(False)
        self.lock = threading.Lock()

    def probabilities(self,bgr):
        import cv2
        import numpy as np
        from core.trainer import _letterbox
        image = cv2.cvtColor(_letterbox(bgr,224),cv2.COLOR_BGR2RGB).astype(np.float32)/255.
        tensor = self.torch.from_numpy(image.transpose(2,0,1).copy()).unsqueeze(0)
        with self.lock,self.torch.inference_mode():
            probs = self.model(tensor)[0].tolist()
        if len(probs)!=3 or not all(math.isfinite(p) and 0<=p<=1 for p in probs):
            raise ValueError('Invalid classifier probability output')
        return dict(zip(CLASSES,probs))

    def __call__(self,bgr):
        probs = self.probabilities(bgr)
        label = max(probs,key=probs.get)
        outcome,confidence = None,None
        if self.rule is not None:
            compliant = sum(probs[k] for k in self.rule['accepted_classes'])
            confidence = max(compliant,1-compliant)
            if confidence >= self.rule['minimum_confidence']:
                outcome = 'compliant' if compliant>=.5 else 'violation'
        return {'label':label,'probabilities':probs,'policy_label':outcome,'policy_confidence':confidence}


class UniformModelRegistry:
    def __init__(self,root=None,*,actor_provider=None,legacy_root=None,evaluation_service=None):
        self.root = Path(root or DEFAULT_ROOT).resolve()
        self.legacy_root = Path(legacy_root or ROOT).resolve()
        self.actor_provider = actor_provider or (lambda:None)
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.root),threading.RLock())
        self._legacy = None
        self.evaluation_service = evaluation_service

    def actor(self):
        actor = self.actor_provider()
        if not isinstance(actor,dict) or actor.get('role')!='superadmin' or not actor.get('username'):
            raise PermissionError('A current superadmin staff session is required')
        return actor

    def legacy(self):
        # Called only by background inventory/evaluation workers. Rehash to detect
        # externally changed legacy assets before preserving or evaluating them.
        files = {name:digest(self.legacy_root/'models'/name) for name in ('uniform_cls.pt','uniform_color_ref.json') if (self.legacy_root/'models'/name).is_file()}
        if 'uniform_cls.pt' not in files: raise ValueError('Legacy uniform classifier unavailable')
        self._legacy = {'id':'legacy-'+json_hash(files),'kind':'legacy','labels':['correct_uniform','wrong_uniform'],
            'mapping_version':'legacy-uniform-binary-v1','preprocessing_version':LEGACY_PREPROCESS['version'],
            'preprocessing':LEGACY_PREPROCESS,'dataset_version':'legacy-training-provenance-incomplete',
            'files':files,'path':str(self.legacy_root/'models'),'training_hashes':[],
            'training_groups':[],'training_dhashes':[],'synthetic':False,
            'policy':{'kind':'legacy_color_fusion','minimum_confidence':.60,'version':'existing-live-policy-v1'}}
        return dict(self._legacy)

    def pointer(self):
        path = self.root/'active.json'
        if not path.exists(): return None
        result = read_json(path)
        if result.get('schema_version')!=1 or not isinstance(result.get('generation'),str):
            raise ValueError('Invalid active model pointer; refusing fallback')
        self.bundle_path(result.get('active'))
        return result

    def token(self):
        pointer = self.pointer()
        return pointer['generation'] if pointer else 'legacy-default'

    def matches(self,token):
        try: return token == self.token()
        except Exception: return False

    def bundle_path(self,version):
        if not isinstance(version,str) or len(version)!=64 or any(c not in '0123456789abcdef' for c in version):
            raise ValueError('Invalid deployment version')
        path = self.root/'bundles'/version
        if path.is_symlink() or (self.root/'bundles').is_symlink(): raise ValueError('Deployment symlink rejected')
        return path

    def current(self):
        pointer = self.pointer()
        if pointer is None: return self.legacy()
        info = self.validate_deployment(pointer['active'])
        return {**info,'path':str(self.bundle_path(pointer['active'])),'deployment_id':pointer['active']}

    def validate_deployment(self,version):
        path = self.bundle_path(version)
        info = read_json(path/'bundle.json')
        if json_hash(info)!=version: raise ValueError('Deployment metadata checksum mismatch')
        expected_preprocess = PREPROCESS if info.get('kind')=='candidate' else LEGACY_PREPROCESS
        if info.get('preprocessing') != expected_preprocess or info.get('kind') not in ('candidate','legacy'):
            raise ValueError('Incompatible deployment bundle')
        expected_labels = CLASSES if info['kind']=='candidate' else ['correct_uniform','wrong_uniform']
        if info.get('labels')!=expected_labels: raise ValueError('Deployment class mapping mismatch')
        for relative,expected in info['files'].items():
            if digest(safe_file(path,relative))!=expected: raise ValueError('Deployment file checksum mismatch')
        required = {'candidate.pt','class_map.json','config.json','dataset_manifest.json','evaluation.json','review.json'} if info['kind']=='candidate' else {'uniform_cls.pt'}
        if not required <= set(info['files']): raise ValueError('Incomplete deployment snapshot')
        if info['kind']=='candidate':
            mapping = read_json(path/'class_map.json')
            manifest = read_json(path/'dataset_manifest.json')
            config = read_json(path/'config.json')
            if mapping != {'mapping_version':'uniform-garments-v1','classes':[{'index':i,'category':c} for i,c in enumerate(CLASSES)]}:
                raise ValueError('Deployment class map contents disagree')
            if manifest.get('model_preprocess') != PREPROCESS or config.get('synthetic_smoke'):
                raise ValueError('Incompatible or synthetic deployment')
            if info['id'] != info['files']['candidate.pt'] or info.get('policy') != policy_rule(info['policy']['minimum_confidence']):
                raise ValueError('Deployment identity or policy mismatch')
            if digest(path/'dataset_manifest.json') != config.get('dataset_manifest_sha256'):
                raise ValueError('Deployment dataset/config mismatch')
            if manifest.get('dataset_snapshot_sha256')!=info.get('dataset_version'):
                raise ValueError('Deployment dataset version mismatch')
            report,review = read_json(path/'evaluation.json'),read_json(path/'review.json')
            if (json_hash(report)!=info.get('accepted_report') or review.get('report_sha256')!=json_hash(report)
                    or review.get('accepted') is not True or review.get('policy')!=info['policy']
                    or report.get('candidate',{}).get('id')!=info['id']
                    or report.get('candidate',{}).get('policy_version')!=json_hash(info['policy'])
                    or report.get('status')!='completed' or report.get('independent') is not True
                    or report.get('synthetic') or not review.get('actor')):
                raise ValueError('Deployment evaluation/acceptance proof mismatch')
        elif info.get('id') != 'legacy-'+json_hash(info['files']):
            raise ValueError('Preserved baseline identity mismatch')
        return info

    @contextmanager
    def _locked(self,exclusive=True):
        with self.lock:
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            path = self.root/'.switch.lock'
            if path.is_symlink(): raise ValueError('Unsafe model lock')
            with path.open('a+') as stream:
                fcntl.flock(stream,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                try: yield
                finally: fcntl.flock(stream,fcntl.LOCK_UN)

    @contextmanager
    def result_guard(self,token):
        # Switching waits for an accepted old-version persistence operation to end.
        with self._locked(exclusive=False):
            yield self.matches(token)

    def _publish_bundle(self,info,sources):
        bundles = self.root/'bundles'; bundles.mkdir(exist_ok=True,mode=0o700)
        staging = Path(tempfile.mkdtemp(prefix='.prepare-',dir=bundles))
        try:
            files = {}
            for name,source in sources.items():
                if Path(name).name!=name: raise ValueError('Invalid deployment file name')
                if isinstance(source,dict): atomic_json(staging/name,source)
                else: shutil.copyfile(source,staging/name)
                files[name] = digest(staging/name)
                fd = os.open(staging/name,os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
            value = {**info,'files':files}
            version = json_hash(value)
            atomic_json(staging/'bundle.json',value)
            target = bundles/version
            if target.exists(): self.validate_deployment(version)
            else: os.replace(staging,target)
            directory = os.open(bundles,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
            self.validate_deployment(version)
            return version
        finally:
            if staging.exists(): shutil.rmtree(staging)  # only this operation's staging copies

    def _preserve_current(self):
        pointer = self.pointer()
        if pointer:
            self.validate_deployment(pointer['active'])
            return pointer['active']
        legacy = self.legacy()
        sources = {name:self.legacy_root/'models'/name for name in legacy['files']}
        version = self._publish_bundle({k:v for k,v in legacy.items() if k not in ('path','files')},sources)
        if self.validate_deployment(version)['files'] != legacy['files']:
            raise ValueError('Legacy assets changed during preservation; refresh before switching')
        return version

    def _switch(self,target,previous,actor,reason):
        old = self.pointer()
        pointer = {'schema_version':1,'generation':uuid.uuid4().hex,'active':target,'previous':previous,
                   'actor':actor['username'],'reason':reason,'at':datetime.now(timezone.utc).isoformat()}
        # Transaction intent is durable first; active.json is the sole commit point.
        tx = {'old':old,'new':pointer,'state':'prepared'}
        atomic_json(self.root/'switch-journal.json',tx)
        self.actor()  # role/session can be revoked during validation/preload
        atomic_json(self.root/'active.json',pointer)
        atomic_json(self.root/'switch-journal.json',{**tx,'state':'committed'})
        return pointer

    def recover(self):
        """Never guess a pointer after interruption: validate the committed snapshot."""
        with self._locked():
            self.actor()
            pointer = self.pointer()
            if pointer: self.validate_deployment(pointer['active'])
            journal = self.root/'switch-journal.json'
            if journal.exists():
                value = read_json(journal)
                state = 'committed' if pointer==value.get('new') else 'aborted' if pointer==value.get('old') else 'inconsistent'
                if state=='inconsistent': raise ValueError('Model switch recovery requires manual inspection')
                atomic_json(journal,{**value,'state':state})
            return pointer

    def activate(self,candidate_path,report_id,rule,review,*,expected_token):
        actor = self.actor()
        if self.evaluation_service is None:
            raise ValueError('Evaluation service is required')
        report = self.evaluation_service.load_report(report_id)
        frozen_set = self.evaluation_service.load_set(report['set_id'])
        if (report.get('dataset_sha256') != frozen_set.get('dataset_sha256')
                or report.get('frozen_manifest_sha256') != digest(self.evaluation_service._set_dir(report['set_id'])/'frozen_manifest.json')):
            raise ValueError('Evaluation dataset no longer matches the report')
        if review.get('report_sha256') != self.evaluation_service.report_checksum(report_id):
            raise ValueError('Evaluation report changed after review; refresh and review again')
        candidate = candidate_descriptor(candidate_path)
        rule = policy_rule(rule['minimum_confidence'])
        if candidate['synthetic'] or report.get('synthetic'):
            raise ValueError('Synthetic candidates/evaluations cannot be activated')
        if report.get('status')!='completed' or report.get('independent') is not True:
            raise ValueError('Completed independent evaluation is required')
        if report.get('candidate',{}).get('id')!=candidate['id']:
            raise ValueError('Evaluation does not describe these candidate weights')
        if report.get('candidate',{}).get('policy_version')!=json_hash(rule):
            raise ValueError('Policy changed after evaluation; it must be fixed before final testing')
        if not review.get('criteria','').strip() or not review.get('rationale','').strip() or review.get('accepted') is not True:
            raise ValueError('Explicit acceptance criteria and a written review are required')
        metrics = report.get('per_model',{}).get('candidate',{}).get('classification',{}).get('per_class',{})
        if any(metrics.get(label,{}).get('support',0)<=0 for label in CLASSES):
            raise ValueError('Independent evaluation is missing a garment category')
        if report['candidate'].get('preprocessing_version')!=candidate['preprocessing_version']:
            raise ValueError('Evaluation preprocessing does not match candidate')
        if report['candidate'].get('dataset_version')!=candidate['dataset_version']:
            raise ValueError('Evaluation training dataset version does not match candidate')
        policy_rows = report.get('predictions',[])
        if not policy_rows or not all(any(row.get('policy_label')==label for row in policy_rows) for label in ('compliant','violation')):
            raise ValueError('Activation requires explicitly policy-labeled compliant and violation examples')
        with self._locked():
            actor = self.actor()
            if not self.matches(expected_token): raise ValueError('Active model changed; refresh and review again')
            baseline = self.current()
            if (report.get('baseline',{}).get('id')!=baseline['id']
                    or report.get('baseline',{}).get('policy_version')!=json_hash(baseline['policy'])):
                raise ValueError('Evaluation baseline is no longer the active model')
            if report.get('baseline',{}).get('preprocessing_version')!=baseline['preprocessing_version']:
                raise ValueError('Baseline preprocessing changed since evaluation')
            previous = self._preserve_current()
            if self.validate_deployment(previous)['id']!=baseline['id']:
                raise ValueError('Baseline assets changed while preserving rollback; refresh and review again')
            proof = {**review,'actor':actor['username'],'source_report_sha256':review['report_sha256'],
                     'report_sha256':json_hash(report),'policy':rule}
            sources = {name:Path(candidate_path)/name for name in ('candidate.pt','class_map.json','config.json','dataset_manifest.json')}
            sources.update({'evaluation.json':report,'review.json':proof})
            info = {k:v for k,v in candidate.items() if k!='path'}
            info.update(kind='candidate',preprocessing=PREPROCESS,policy=rule,accepted_report=json_hash(report))
            target = self._publish_bundle(info,sources)
            DeploymentPredictor({**self.validate_deployment(target),'path':str(self.bundle_path(target))})(__import__('numpy').zeros((224,224,3),dtype='uint8'))
            return self._switch(target,previous,actor,review['rationale'])

    def rollback(self,*,expected_token,reason):
        actor = self.actor()
        if not str(reason).strip(): raise ValueError('A rollback reason is required')
        with self._locked():
            actor = self.actor()
            if not self.matches(expected_token): raise ValueError('Active model changed; refresh first')
            pointer = self.pointer()
            if not pointer or not pointer.get('previous'): raise ValueError('No preserved compatible rollback bundle exists')
            target = pointer['previous']
            info = self.validate_deployment(target)
            DeploymentPredictor({**info,'path':str(self.bundle_path(target))})(__import__('numpy').zeros((224,224,3),dtype='uint8'))
            return self._switch(target,pointer['active'],actor,reason)


class DeploymentPredictor:
    """One immutable deployment's classification and policy prediction."""
    def __init__(self,info):
        self.info,self.rule = info,info['policy']
        self.path = Path(info['path'])
        self.candidate = CandidatePredictor(self.path,self.rule) if info['kind']=='candidate' else None
        self.color = None
        if self.candidate is None:
            from ultralytics import YOLO
            from core.uniform_matcher import UniformColorMatcher
            self.model = YOLO(str(self.path/'uniform_cls.pt'))
            if self.model.task!='classify' or set(self.model.names.values()) != {'correct_uniform','wrong_uniform'}:
                raise ValueError('Incompatible preserved baseline classifier')
            color_path = self.path/'uniform_color_ref.json'
            if color_path.is_file():
                data = read_json(color_path)
                lo,hi,n = int(data['hue_lo']),int(data['hue_hi']),int(data.get('n_samples',0))
                if not (-180<lo<180 and lo<hi<360 and hi-lo<180 and n>0): raise ValueError('Invalid preserved color reference')
                self.color = object.__new__(UniformColorMatcher)
                self.color._hue_lo,self.color._hue_hi,self.color._n_samples = lo,hi,n
            self.lock = threading.Lock()

    def probabilities(self,bgr):
        if self.candidate: return self.candidate.probabilities(bgr)
        from core.trainer import _letterbox
        with self.lock:
            result = self.model.predict(_letterbox(bgr,224),verbose=False)[0]
            return dict(zip((result.names[i] for i in range(len(result.names))),result.probs.data.tolist()))

    def __call__(self,bgr):
        if self.candidate: return self.candidate(bgr)
        from core.uniform_matcher import fuse_uniform_prob
        probs = self.probabilities(bgr)
        pcol = None
        if self.color:
            verdict,p = self.color.is_uniform(bgr)
            if verdict is not None: pcol=p
        fused = fuse_uniform_prob(probs.get('correct_uniform'),pcol)
        confidence = max(fused,1-fused) if fused is not None else 0.
        outcome = ('compliant' if fused>=.5 else 'violation') if confidence>=self.rule['minimum_confidence'] else None
        return {'label':max(probs,key=probs.get),'probabilities':probs,'policy_label':outcome,'policy_confidence':confidence}
