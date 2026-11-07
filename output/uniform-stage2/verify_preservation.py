"""Read-only preservation audit against recovered Stage 1 and scrolling evidence."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]

def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

protected = json.loads((ROOT/'output/uniform-stage1/protected-artifacts-sha256.json').read_text())
scrolling = json.loads((ROOT/'output/scrolling/manifest.json').read_text())['sha256']
scrolling.pop('ui/training_panel.py')  # Intentional Stage 1/2 integration; diff reviewed separately.
expected_catalog = json.loads((ROOT/'output/uniform-stage2/recovered-catalog.json').read_text())['catalog_sha256']
changed = [name for name, expected in protected.items() if not (ROOT/name).is_file() or sha(ROOT/name) != expected]
scroll_changes = [name for name, expected in scrolling.items() if sha(ROOT/name) != expected]
catalog_hash = sha(ROOT/'data/uniform_stage1/catalog.json')
active = json.loads((ROOT/'output/uniform-stage1/final-audit.json').read_text())['model_artifacts_sha256']
actual_active = {name: sha(ROOT/name) for name in active}
result = dict(protected_files=len(protected), changed_protected_files=changed,
              scrolling_files=len(scrolling), changed_scrolling_files=scroll_changes,
              catalog_sha256=catalog_hash, catalog_unchanged=catalog_hash == expected_catalog,
              active_artifacts_sha256=actual_active, active_artifacts_unchanged=active == actual_active)
(ROOT/'output/uniform-stage2/preservation-results.json').write_text(json.dumps(result, indent=2))
print(json.dumps({k:v for k,v in result.items() if k != 'active_artifacts_sha256'}, indent=2))
assert not changed and not scroll_changes and result['catalog_unchanged'] and result['active_artifacts_unchanged']
