"""Read-only preservation checks; write evidence only under output/uniform-stage4."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[2]
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
protected=json.loads((ROOT/'output/uniform-stage1/protected-artifacts-sha256.json').read_text())
scroll=json.loads((ROOT/'output/scrolling/manifest.json').read_text())['sha256']
intentional=('ui/training_panel.py','ui/dashboard.py','ui/live_alerts.py')
for path in intentional:scroll.pop(path,None)
active=json.loads((ROOT/'output/uniform-stage1/final-audit.json').read_text())['model_artifacts_sha256']
expected_catalog=json.loads((ROOT/'output/uniform-stage2/recovered-catalog.json').read_text())['catalog_sha256']
changed=lambda values:[name for name,h in values.items() if not (ROOT/name).is_file() or sha(ROOT/name)!=h]
catalog=json.loads((ROOT/'data/uniform_stage1/catalog.json').read_text())
records=catalog['records'];records=list(records.values()) if isinstance(records,dict) else records
results={'protected_file_count':len(protected),'changed_protected':changed(protected),
    'active_artifact_count':len(active),'changed_active':changed(active),
    'scroll_files_without_intentional_integration':len(scroll),'changed_scroll':changed(scroll),
    'intentional_scroll_touchpoints':list(intentional),
    'catalog_sha256':sha(ROOT/'data/uniform_stage1/catalog.json'),
    'catalog_unchanged':sha(ROOT/'data/uniform_stage1/catalog.json')==expected_catalog,
    'catalog_status_counts':{s:sum(r['status']==s for r in records) for s in ('pending_review','approved','excluded')},
    'production_pointer_absent':not (ROOT/'data/uniform_models/active.json').exists(),
    'real_candidate_bundles':len(list((ROOT/'data/uniform_candidates').glob('*/candidate.pt')))}
prior_sources=json.loads((ROOT/'output/uniform-stage3/source-sha256.json').read_text())
intentional_stage4={'core/live_pipeline.py','core/live_state.py','core/uniform_model_registry.py','ui/dashboard.py','ui/live_alerts.py','scripts/replay_camera_assessment.py'}
unaffected={name:h for name,h in prior_sources.items() if name not in intentional_stage4}
results['unchanged_stage3_source_count']=len(unaffected)
results['changed_unowned_stage3_sources']=changed(unaffected)
smoke=ROOT/'data/uniform_candidate_smoke/645a629ef7034ed8ae06d3a4743ba75d/candidates/ffe234076d3d428eb28c332838b9e5fd/candidate.pt'
results['candidate_sha256']=sha(smoke)
assert results['candidate_sha256']=='4eda5fd372adbd80749553f849bc155a3fc4873fa2b660774d413a26bdaaf65a'
assert not results['changed_unowned_stage3_sources']
(ROOT/'output/uniform-stage4/preservation-results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
assert not results['changed_protected'] and not results['changed_active'] and not results['changed_scroll']
assert results['catalog_unchanged'] and results['production_pointer_absent'] and results['real_candidate_bundles']==0
