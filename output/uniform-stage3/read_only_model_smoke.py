"""Reuse retained Stage 2 synthetic weights; no training/evaluation/activation."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from datetime import datetime,timezone
import os,json
import numpy as np
import torch
from core.uniform_model_registry import (candidate_descriptor,CandidatePredictor,UniformModelRegistry,DeploymentPredictor,digest,policy_rule)
print(json.dumps({'pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat(),'command':sys.executable+' output/uniform-stage3/read_only_model_smoke.py'}),flush=True)
torch.set_num_threads(1)
path=ROOT/'data/uniform_candidate_smoke/645a629ef7034ed8ae06d3a4743ba75d/candidates/ffe234076d3d428eb28c332838b9e5fd'
active_paths=[ROOT/'models/uniform_cls.pt',ROOT/'models/uniform_color_ref.json',ROOT/'yolov8n-cls.pt']
before={str(p.relative_to(ROOT)):digest(p) for p in active_paths}
pointer=ROOT/'data/uniform_models/active.json'
pointer_before=pointer.read_bytes() if pointer.exists() else None
info=candidate_descriptor(path)
assert info['synthetic'] is True
crop=np.zeros((123,211,3),np.uint8);crop[:,:,0]=150
model=CandidatePredictor(path,policy_rule(.8))  # Explicit fixture operating point, not a recommended threshold.
result=model(crop)
assert set(result['probabilities'])==set(info['labels'])
assert abs(sum(result['probabilities'].values())-1)<1e-5
legacy=UniformModelRegistry().legacy()
baseline=DeploymentPredictor(legacy)
old=baseline(crop)
assert set(old['probabilities'])==set(legacy['labels'])
assert before=={str(p.relative_to(ROOT)):digest(p) for p in active_paths}
assert (pointer.read_bytes() if pointer.exists() else None)==pointer_before
report={'status':'passed','synthetic_candidate':str(path.relative_to(ROOT)),'candidate_sha256':info['id'],
        'checks':['complete retained bundle','state-dict architecture/class mapping/preprocessing','finite probability outputs','legacy loader and color fusion','active/base hashes unchanged','production pointer unchanged'],
        'limitations':['One synthetic raster; no accuracy, speed, physical-camera or independent evaluation claim.','No training or activation.'],
        'protected_sha256':before}
(ROOT/'output/uniform-stage3/read-only-model-smoke.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
