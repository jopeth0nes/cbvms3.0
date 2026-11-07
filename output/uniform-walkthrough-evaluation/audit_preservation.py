"""Read-only recovery/preservation audit; writes only this evaluation's evidence."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def read(path):return json.loads((ROOT/path).read_text())
def changed(pins):return [p for p,h in pins.items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
def diff_sections(value):
    return {p.splitlines()[0].split(' b/',1)[1]:p for p in value.split('diff --git ')[1:]
            if p.splitlines()[0].split(' b/',1)[1]!='scripts/replay_camera_assessment.py'}

protected=read('output/uniform-stage1/protected-artifacts-sha256.json')
active=read('output/uniform-stage1/final-audit.json')['model_artifacts_sha256']
sources=read('output/uniform-walkthrough-evaluation/initial-source-sha256.json')
source_changes=changed(sources)
assert set(source_changes)<= {'scripts/replay_camera_assessment.py','tests/test_camera_replay.py'}
assert not changed(protected) and not changed(active)
assert diff_sections((OUT/'initial-tracked.diff').read_text())==diff_sections(
    subprocess.check_output(['git','diff'],cwd=ROOT,text=True))
catalog=read('data/uniform_stage1/catalog.json')
records=catalog['records'];records=list(records.values()) if isinstance(records,dict) else records
old=read('output/uniform-stage5/input-audit.json')
assert sha(ROOT/'data/uniform_stage1/catalog.json')==old['catalog_sha256']
archive=read('output/uniform-stage1/input-audit.json')['archive']
assert sha(archive['zip'])==archive['sha256']
stages={}
for stage,pin_file in ((1,'code-manifest.json'),(2,'code-manifest.json'),
                       (3,'source-sha256.json'),(4,'source-sha256.json'),(5,'source-sha256.json')):
    pins=read(f'output/uniform-stage{stage}/{pin_file}')
    pins=pins.get('sha256',pins)
    mismatches=changed(pins)
    stages[str(stage)]={'pin_file':f'output/uniform-stage{stage}/{pin_file}',
        'matching':[p for p in pins if p not in mismatches],
        'superseded_or_changed':mismatches}
candidate=ROOT/'data/uniform_candidate_smoke/645a629ef7034ed8ae06d3a4743ba75d/candidates/ffe234076d3d428eb28c332838b9e5fd'
smoke=read('output/uniform-stage2/smoke-results.json')
assert sha(candidate/'candidate.pt')==smoke['verification']['candidate_sha256']
assert not (ROOT/'data/uniform_models/active.json').exists()
real_candidates=list((ROOT/'data/uniform_candidates').glob('*/candidate.pt'))
assert not real_candidates
preference=read('data/camera_preference.json')
views=read('data/camera_view_preference.json')
data={
    'repository':str(ROOT),'branch':subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip(),
    'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    'protected_files_verified':len(protected),'active_artifacts_verified':active,
    'source_changes_since_recovery':source_changes,'unrelated_tracked_diffs_unchanged':True,
    'prior_stage_source_pins':stages,
    'dataset':{'catalog_sha256':old['catalog_sha256'],'total':len(records),
        'status':dict(Counter(r['status'] for r in records)),
        'categories':dict(Counter(r['category'] for r in records)),
        'logo_audit_reused_at_identical_catalog_sha256':old,
        'readiness_reused_at_identical_catalog_sha256':read('output/uniform-stage1/final-audit.json')['readiness'],
        'preprocessing_version':'uniform-stage1-raster-v1'},
    'original_archive_unchanged':archive,
    'synthetic_candidate':{'path':str(candidate),'weights_sha256':sha(candidate/'candidate.pt'),
        'synthetic':True,'mapping':smoke['verification']['class_map'],
        'preprocessing':smoke['verification']['preprocessing'],
        'manifest_sha256':smoke['last_status']['manifest_sha256']},
    'real_candidate_count':0,'production_active_pointer_absent':True,
    'camera_saved_primary':{k:preference.get(k) for k in ('id','label','type','index')},
    'camera_saved_previews':views.get('sources',[]),
    'camera_saved_preview_destination':views.get('destination'),
    'physical_camera_measurements':False,'consented_labeled_walkthroughs':0,
    'owned_model_training_runs_this_task':0,'production_activation_performed':False}
(OUT/'preservation-and-state.json').write_text(json.dumps(data,indent=2))
print(json.dumps({'protected_files_verified':len(protected),'active_artifacts_verified':len(active),
    'catalog_unchanged':True,'archive_unchanged':True,'source_changes':source_changes,
    'prior_stage_matching_counts':{s:len(v['matching']) for s,v in stages.items()},
    'real_candidates':0,'pending_examples':len(records),'production_active_pointer_absent':True}))
