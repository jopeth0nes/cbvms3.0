"""Compare paired passage exports without running, training or activating models.

Reuses Stage 3 classification/policy accounting and Stage 4 latency statistics.
This report is descriptive; it does not establish training/test independence or
replace the model registry's authorization and acceptance workflow.
"""
import math
import statistics
from collections import Counter

from core.uniform_evaluation import EvaluationService
from core.uniform_latency import distribution
from core.uniform_model_registry import CLASSES, json_hash
from core.uniform_logo_annotations import LOGO_VISIBILITIES

CRITERIA = ('classification_errors', 'false_violations', 'unassessed_rate', 'added_latency_ms')
COMMON = ('footage_sha256', 'frame_sequence_sha256', 'annotation_manifest_sha256',
          'torso_model_sha256', 'torso_preprocessing', 'policy_version',
          'configuration_sha256', 'test_conditions')


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _signed_distribution(values):
    if not values:
        return {'count': 0, 'median_ms': None, 'p95_ms': None}
    values = sorted(values)
    rank = (len(values)-1)*.95
    lo = int(rank)
    hi = min(lo+1, len(values)-1)
    return {'count': len(values), 'median_ms': statistics.median(values),
            'p95_ms': values[lo]+(values[hi]-values[lo])*(rank-lo)}


def compare_logo_runs(manifest, torso_only, torso_plus_logo, *, criteria):
    """One final result per annotated passage; missing results remain missing.

    Criteria are supplied by the responsible reviewer before final testing. No
    thresholds or pass/fail decisions are invented here. Signed added latency
    is paired by passage, never a difference of unrelated aggregate percentiles.
    """
    if (not isinstance(criteria, dict) or set(criteria) != set(CRITERIA)
            or any(not isinstance(v, str) or not v.strip() for v in criteria.values())):
        raise ValueError('Explicit predeclared classification, policy, unassessed and latency criteria are required')
    if (manifest.get('schema') != 1 or manifest.get('purpose') != 'logo-held-out-comparison'
            or manifest.get('reviewed') is not True or not _sha(manifest.get('footage_sha256'))):
        raise ValueError('A reviewed held-out footage manifest is required')
    people = manifest.get('passages')
    if not isinstance(people, list) or not 1 <= len(people) <= 10000:
        raise ValueError('Supply 1–10000 annotated passages')
    truth = {}
    for person in people:
        alias = person.get('passage_alias')
        if not isinstance(alias, str) or not alias or alias in truth:
            raise ValueError('Passage aliases must be unique and nonempty')
        if person.get('garment_label') not in (*CLASSES, None):
            raise ValueError('Invalid garment ground truth')
        if person.get('policy_label') not in ('compliant', 'violation', None):
            raise ValueError('Invalid policy ground truth')
        if person.get('logo_visibility', 'uncertain') not in LOGO_VISIBILITIES:
            raise ValueError('Use canonical logo visibility, never absent for unreadable logos')
        truth[alias] = person
    runs = (torso_only, torso_plus_logo)
    if tuple(r.get('mode') for r in runs) != ('torso_only', 'torso_plus_logo'):
        raise ValueError('Compare torso_only against torso_plus_logo')
    for key in COMMON:
        left, right = (r.get(key) for r in runs)
        if left != right or left is None or left == '' or left == {}:
            raise ValueError('Paired runs must match '+key)
        if key.endswith('sha256') and not _sha(left):
            raise ValueError('Invalid hash: '+key)
    if (torso_only['footage_sha256'] != manifest['footage_sha256']
            or torso_only['annotation_manifest_sha256'] != json_hash(manifest)):
        raise ValueError('Runs do not refer to this exact footage/annotation manifest')
    if torso_only.get('logo_model_sha256') is not None or not _sha(torso_plus_logo.get('logo_model_sha256')):
        raise ValueError('Pin the logo model only for the augmented run')
    if not torso_plus_logo.get('logo_preprocessing'):
        raise ValueError('Pin the logo preprocessing version')
    if any(r.get('criteria_sha256') != json_hash(criteria) for r in runs):
        raise ValueError('Both runs must pin the predeclared criteria')
    indexed = []
    timing_keys = ('first_usable_to_decision_ms', 'zone_entry_to_decision_ms', 'logo_ms')
    for run in runs:
        rows = run.get('results')
        if not isinstance(rows, list) or len(rows) > len(truth):
            raise ValueError('Too many or invalid passage results')
        seen = {}
        for row in rows:
            alias = row.get('passage_alias')
            if alias not in truth or alias in seen:
                raise ValueError('Results must name unique annotated passages')
            if row.get('label') not in (*CLASSES, None):
                raise ValueError('Invalid garment prediction')
            if row.get('policy_outcome') not in ('compliant', 'violation', None):
                raise ValueError('Invalid policy prediction')
            if row.get('label') is None and row.get('policy_outcome') is not None:
                raise ValueError('Unable to assess must not become a policy outcome')
            for key in timing_keys:
                value = row.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError('Invalid measured '+key)
            seen[alias] = row
        indexed.append(seen)
    combined = []
    for alias, person in truth.items():
        row = {'category': person.get('garment_label'), 'policy_label': person.get('policy_label')}
        for key, results in zip(('baseline', 'candidate'), indexed):
            prediction = results.get(alias, {})
            row[key] = {'label': prediction.get('label'), 'policy_label': prediction.get('policy_outcome')}
        combined.append(row)
    labelled = [r for r in combined if r['category'] is not None]
    summary = {}
    for mode, key, results in zip(('torso_only', 'torso_plus_logo'), ('baseline', 'candidate'), indexed):
        policy = EvaluationService._policy_metrics(combined, key)
        if not policy['assessed_count']:
            policy['false_violation_count'] = policy['missed_violation_count'] = None
        unassessed = sum(r.get('label') is None for r in results.values())
        summary[mode] = {'passages': len(truth), 'returned_results': len(results),
            'missed': len(truth)-len(results), 'unassessed': unassessed,
            'unassessed_rate_on_returned': unassessed/len(results) if results else None,
            'unresolved_rate_all_passages': (len(truth)-len(results)+unassessed)/len(truth),
            'labelled_passages': len(labelled),
            'classification_errors': (sum(r[key]['label'] is not None and r[key]['label'] != r['category']
                                          for r in labelled) if labelled else None),
            'classification_unresolved': sum(r[key]['label'] is None for r in labelled),
            'per_class': EvaluationService._class_metrics(labelled, key),
            'confusion': EvaluationService._confusion(labelled, key, list(CLASSES)),
            'policy': policy,
            'latency': {k: distribution([r.get(k) for r in results.values()]) for k in timing_keys}}
    added = []
    for alias in truth:
        a, b = (rows.get(alias, {}).get('first_usable_to_decision_ms') for rows in indexed)
        if a is not None and b is not None:
            added.append(b-a)
    return {'schema': 1, 'status': 'comparison_only', 'activation_allowed': False,
        'manifest_sha256': json_hash(manifest), 'criteria': criteria, 'criteria_sha256': json_hash(criteria),
        'matched_conditions': {k: torso_only[k] for k in COMMON},
        'logo_model_sha256': torso_plus_logo['logo_model_sha256'],
        'logo_preprocessing': torso_plus_logo['logo_preprocessing'],
        'per_mode': summary, 'paired_added_latency_ms': _signed_distribution(added),
        'coverage': {key: dict(Counter(str(p.get(key) or 'unknown') for p in people))
                     for key in ('camera', 'lighting', 'capture_group', 'logo_visibility')},
        'limitations': ['Training/test independence and label correctness require a separate provenance review.',
            'Missing classification/policy labels are not inferred; no automatic acceptance decision is made.',
            'Only matching input hashes/configuration are checked; real replay conditions require audit.',
            'A shared crest alone does not establish garment category or policy permission.']}
