"""Disposable import staging and one consolidated, save-time checked review."""
from pathlib import Path
import hashlib
import tempfile
import time
from core.uniform_dataset import UniformDataset, _image_info, visually_similar

ACTIONS = {'exact': ('skip', 'add_source'), 'similar': ('keep_both', 'skip'),
           'conflict': ('use_existing', 'change_category', 'skip')}


class ImportPlan(UniformDataset):
    def __init__(self, dataset):
        self.__dict__.update({k: v for k, v in dataset.__dict__.items()
                              if k not in {'_import_bytes', 'import_paths', 'import_folder', 'import_zip'}})
        self.dataset = dataset
        self.temporary = tempfile.TemporaryDirectory(prefix='cbvms-import-review-')
        self.items, self.rows = [], []

    def close(self):
        self.temporary.cleanup()

    def _import_bytes(self, data, source_path, kind, category, capture_group,
                      legacy_label=None, archive_sha256=None):
        _, _, _, dhash, _ = _image_info(data)
        digest = hashlib.sha256(data).hexdigest()
        path = Path(self.temporary.name) / digest
        if not path.exists():
            path.write_bytes(data)
        self.items.append(dict(image_id=digest, dhash=dhash, path=str(path), category=category,
            source_path=source_path, kind=kind, capture_group=capture_group,
            legacy_label=legacy_label, archive_sha256=archive_sha256))
        return digest

    def prepare(self):
        known = {r['image_id']: dict(r, path=str(self.root / r['original_path']), within_batch=False)
                 for r in self.dataset.snapshot()['records']}
        grouped = {}
        for item in self.items:
            exact = known.get(item['image_id'])
            matches = [exact] if exact else [r for r in known.values() if visually_similar(item, r)]
            match_type = ('conflict' if exact['category'] != item['category'] else 'exact') if exact else ('similar' if matches else None)
            item['resolution'] = dict(exact_category=exact['category'] if exact else None,
                                      near_ids=[r['image_id'] for r in matches])
            if match_type:
                key = (match_type, item['image_id'])
                if key not in grouped:
                    row = dict(row_id=str(len(self.rows)), match_type=match_type,
                        incoming_category=item['category'], incoming_source=item['source_path'],
                        incoming_path=item['path'], incoming_sources=[], occurrences=0, default_action=ACTIONS[match_type][0],
                        matches=[dict(image_id=r['image_id'], category=r['category'], path=r['path'],
                            source=r.get('source_path') or '; '.join(s.get('path', '') for s in r.get('sources', [])),
                            within_batch=r.get('within_batch', False)) for r in matches])
                    grouped[key] = row
                    self.rows.append(row)
                row = grouped[key]
                row['occurrences'] += 1
                row['incoming_sources'].append(item['source_path'])
                item['row_id'] = row['row_id']
            if not exact:
                known[item['image_id']] = dict(item, within_batch=True)

    def commit(self, job):
        ids = set()
        present_ids = {r['image_id'] for r in self.dataset.snapshot()['records']}
        state = job.snapshot()
        processed = state.get('failed', 0) + state.get('skipped', 0)
        imported = duplicates = sources = 0
        changed_categories = {}
        last_counts = 0.0
        failed, skipped = state.get('failed', 0), state.get('skipped', 0)
        for item in self.items:
            if job._cancel.is_set():
                break
            action = job.duplicate_choices().get(item.get('row_id'), 'keep_both')
            duplicate = item['resolution']['exact_category'] is not None
            duplicates += int(duplicate)
            job._publish(phase='importing', current=item['source_path'], processed=processed)
            if action == 'skip':
                skipped += 1
                if duplicate and item['image_id'] in present_ids:
                    ids.add(item['image_id'])
            else:
                try:
                    resolution = dict(item['resolution'], action=action)
                    if item['image_id'] in changed_categories:
                        resolution['exact_category'] = changed_categories[item['image_id']]
                    result = self.dataset._import_bytes(Path(item['path']).read_bytes(), item['source_path'],
                        item['kind'], item['category'], item['capture_group'], item['legacy_label'],
                        item['archive_sha256'], resolution=resolution)
                    ids.add(result['image_id'])
                    if result.get('processing_error'):
                        job._publish(error='Photo saved; automatic processing could not start: ' + result['processing_error'])
                    if action == 'change_category':
                        changed_categories[item['image_id']] = item['category']
                    imported += int(result['imported'])
                    sources += int(result['source_added'] and not result['imported'])
                except Exception as exc:
                    failed += 1
                    job._publish(error=f'{type(exc).__name__}: {exc}')
            if ids and time.monotonic() - last_counts >= .2:
                from core.uniform_import import _processing_counts
                job._publish(**_processing_counts(self.dataset, ids))
                last_counts = time.monotonic()
            processed += 1
            job._publish(processed=processed, imported=imported, duplicates=duplicates,
                         source_added=sources, failed=failed, skipped=skipped)
        return ids
