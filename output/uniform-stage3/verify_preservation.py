"""Read-only preservation checks; write evidence only under output/uniform-stage3."""
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
intentional=('ui/training_panel.py','ui/dashboard.py')
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
(ROOT/'output/uniform-stage3/preservation-results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
assert not results['changed_protected'] and not results['changed_active'] and not results['changed_scroll']
assert results['catalog_unchanged'] and results['production_pointer_absent'] and results['real_candidate_bundles']==0
