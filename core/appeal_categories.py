"""Versioned human-selected appeal decision categories; never policy or AI actions."""
from dataclasses import dataclass

VERSION = 1
SELECT_CATEGORY = 'Select decision category'
LEGACY_CATEGORY = 'Legacy — category not recorded'


@dataclass(frozen=True)
class DecisionCategory:
    code: str
    decision: str
    label: str

    @property
    def option(self):
        return ('Approval' if self.decision == 'approved' else 'Rejection') + ' — ' + self.label


CATEGORIES = (
    DecisionCategory('approval.incorrect_identity', 'approved', 'Incorrect student identification'),
    DecisionCategory('approval.detection_error', 'approved', 'Uniform compliant / detection error'),
    DecisionCategory('approval.valid_exemption', 'approved', 'Valid exemption or approved activity'),
    DecisionCategory('approval.exceptional_circumstance', 'approved', 'Documented exceptional circumstance'),
    DecisionCategory('approval.duplicate_record', 'approved', 'Duplicate violation record'),
    DecisionCategory('approval.invalid_original_evidence', 'approved', 'Incorrect or unusable original evidence'),
    DecisionCategory('approval.other', 'approved', 'Other approval reason'),
    DecisionCategory('rejection.violation_confirmed', 'rejected', 'Uniform violation confirmed'),
    DecisionCategory('rejection.insufficient_evidence', 'rejected', 'Supporting evidence insufficient'),
    DecisionCategory('rejection.unrelated_evidence', 'rejected', 'Supporting evidence unrelated to the recorded incident'),
    DecisionCategory('rejection.unverified_exemption', 'rejected', 'Claimed exemption not verified'),
    DecisionCategory('rejection.other', 'rejected', 'Other rejection reason'),
)
BY_CODE = {category.code: category for category in CATEGORIES}
BY_OPTION = {category.option: category for category in CATEGORIES}


def validate_category(code, decision):
    category = BY_CODE.get(code) if isinstance(code, str) else None
    if category is None:
        raise ValueError('Select a decision category before saving.')
    if category.decision != decision:
        raise ValueError('Decision category must match the Approve Appeal or Reject Appeal action.')
    return category


def category_display(row):
    row = dict(row)
    if not row.get('decision_category_code'):
        return LEGACY_CATEGORY
    # The saved label is an immutable decision-time snapshot, not a guessed reason.
    return row.get('decision_category_label') or f"Unrecognized category ({row['decision_category_code']})"


def decision_view(row):
    result = dict(row)
    result['decision_category_display'] = (category_display(result)
        if result.get('status', result.get('decision')) in ('approved', 'rejected') else '')
    return result
