"""Versioned promotion floors for automatic-training candidates.

These conservative engineering gates are not a claim of production accuracy.
The existing authenticated human review, independent-set verification, immutable
report binding, active-baseline check and rollback checks still apply.
"""
import math

POLICY = {'version': 'uniform-auto-acceptance-v1', 'minimum_examples_per_class': 10,
          'minimum_groups_per_class': 3, 'minimum_accuracy': .80, 'minimum_macro_f1': .75,
          'minimum_recall': .70, 'maximum_latency_ratio': 1.10}
CLASSES = ('campus_uniform', 'organizational_shirt', 'other_clothing')


def assess(report):
    rows = [r for r in report.get('predictions', []) if r.get('assessability') == 'assessable']
    reasons = []
    if report.get('independent') is not True or report.get('synthetic') or report.get('status') != 'completed':
        reasons.append('completed independent real-data evaluation required')
    f1s, recalls = [], []
    for category in CLASSES:
        actual = [r for r in rows if r['category'] == category]
        groups = {r.get('capture_group') for r in actual if r.get('capture_group')}
        if len(actual) < POLICY['minimum_examples_per_class'] or len(groups) < POLICY['minimum_groups_per_class']:
            reasons.append(f'{category}: requires 10 independent test examples from at least 3 groups')
        tp = sum(r.get('candidate', {}).get('label') == category for r in actual)
        predicted = sum(r.get('candidate', {}).get('label') == category for r in rows)
        recall = tp / len(actual) if actual else 0.
        precision = tp / predicted if predicted else 0.
        recalls.append(recall)
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.)
    accuracy = sum(r.get('candidate', {}).get('label') == r['category'] for r in rows) / max(1, len(rows))
    macro_f1 = sum(f1s) / 3
    if accuracy < POLICY['minimum_accuracy'] or macro_f1 < POLICY['minimum_macro_f1'] or min(recalls) < POLICY['minimum_recall']:
        reasons.append('candidate fails absolute classification accuracy, macro F1 or recall floors')
    if set(report.get('baseline', {}).get('labels', [])) == set(CLASSES):
        base_accuracy = sum(r.get('baseline', {}).get('label') == r['category'] for r in rows) / max(1, len(rows))
        if accuracy < base_accuracy: reasons.append('classification accuracy regresses against active baseline')
        base_f1s = []
        for index, category in enumerate(CLASSES):
            support = sum(r['category'] == category for r in rows)
            predicted = sum(r.get('baseline', {}).get('label') == category for r in rows)
            tp = sum(r['category'] == category and r.get('baseline', {}).get('label') == category for r in rows)
            recall = tp / support if support else 0.
            precision = tp / predicted if predicted else 0.
            base_f1s.append(2 * recall * precision / (recall + precision) if recall + precision else 0.)
            if recalls[index] < recall:
                reasons.append(f'{category} recall regresses against active baseline')
        if macro_f1 < sum(base_f1s) / 3:
            reasons.append('macro F1 regresses against active baseline')
    policy_rows = [r for r in rows if r.get('policy_label') in {'compliant', 'violation'}]
    if not policy_rows:
        reasons.append('paired policy ground truth is unavailable')
    else:
        correct = {m: sum(r.get(m, {}).get('policy_label') == r['policy_label'] for r in policy_rows) for m in ('baseline','candidate')}
        fp = {m: sum(r['policy_label'] == 'compliant' and r.get(m, {}).get('policy_label') == 'violation' for r in policy_rows) for m in ('baseline','candidate')}
        if correct['candidate'] < correct['baseline'] or fp['candidate'] > fp['baseline']:
            reasons.append('policy accuracy or false violations regress against active baseline')
    timing = report.get('paired_latency', {})
    if timing.get('same_hardware') is not True or timing.get('backend') != 'cpu':
        reasons.append('verified paired CPU inference benchmark required')
    for key in ('mean_ms', 'p95_ms'):
        a, b = timing.get('baseline', {}).get(key), timing.get('candidate', {}).get(key)
        if not all(isinstance(v, (float,int)) and math.isfinite(v) and v > 0 for v in (a,b)):
            reasons.append(f'paired {key} latency unavailable')
        elif b > a * POLICY['maximum_latency_ratio']:
            reasons.append(f'candidate {key} latency exceeds baseline by more than 10%')
    return {'policy': dict(POLICY), 'accepted': not reasons, 'reasons': reasons,
            'accuracy': accuracy, 'macro_f1': macro_f1, 'recalls': dict(zip(CLASSES, recalls))}
