"""Read-only readiness helpers for independently reviewed printed logos."""
from __future__ import annotations

LOGO_VISIBILITIES = ("visible", "obscured", "too_small", "back_view", "uncertain")
_ALIASES = {
    "unknown": "uncertain",
    "not_visible": "uncertain",
    "occluded": "obscured",
}
_RELEVANT_FLAGS = {
    "label_conflict", "incorrect_association", "provenance_association_uncertain",
    "person_association_uncertain", "multiple_people", "obstruction",
    "low_resolution", "possible_blur", "image_quality_check_failed", "unable_to_assess", "ambiguous",
}


def normalize_visibility(value: str) -> str:
    """Map legacy labels without treating unknown or occlusion as logo absence."""
    if not isinstance(value, str):
        return "uncertain"
    normalized = value.strip().lower()
    normalized = _ALIASES.get(normalized, normalized)
    return normalized if normalized in LOGO_VISIBILITIES else "uncertain"


def _current_provenance(record: dict) -> dict:
    return {
        "sources": record.get("sources", []),
        "capture_group": record.get("capture_group"),
        "capture_groups": record.get("capture_groups", []),
        "provenance_group": record.get("provenance_group"),
        "has_unknown_provenance": record.get("has_unknown_provenance", True),
    }


def _annotation_matches(record: dict, annotation: dict) -> bool:
    if not isinstance(record, dict) or not isinstance(annotation, dict) or annotation.get("schema") != "printed-logo-v1":
        return False
    history = record.get("review_history", [])
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return False
    latest_at = history[-1].get("at")
    history_annotation = history[-1].get("logo_annotation")
    return (
        annotation.get("reviewed") is True
        and annotation.get("image_sha256") == record.get("sha256")
        and annotation.get("torso_crop") == record.get("crop")
        and annotation.get("category") == record.get("category")
        and annotation.get("preprocess_version") == record.get("preprocess_version")
        and annotation.get("provenance") == _current_provenance(record)
        and annotation.get("reviewed_at") == latest_at
        and history_annotation == annotation
        and annotation.get("box") == record.get("logo_box")
        and annotation.get("visibility") == normalize_visibility(record.get("logo_visibility"))
    )


def is_eligible_visible_annotation(record: dict) -> bool:
    """Whether a visible-logo annotation is current and explicitly reviewed."""
    if not isinstance(record, dict):
        return False
    annotation = record.get("logo_annotation") or {}
    if not isinstance(annotation, dict):
        return False
    if not _annotation_matches(record, annotation):
        return False
    if record.get("status") == "excluded" or record.get("category") not in {
        "campus_uniform", "organizational_shirt", "other_clothing",
    }:
        return False
    if annotation.get("visibility") != "visible":
        return False
    if annotation.get("source_kind") != "photographed_print" or annotation.get("association_confirmed") is not True:
        return False
    torso = record.get("crop")
    box = annotation.get("box")
    if (not isinstance(torso, list) or len(torso) != 4 or not isinstance(box, list) or len(box) != 4
            or any(type(value) is not int for value in torso + box)):
        return False
    x1, y1, x2, y2 = torso
    lx1, ly1, lx2, ly2 = box
    if lx1 < x1 or ly1 < y1 or lx2 > x2 or ly2 > y2 or lx1 >= lx2 or ly1 >= ly2:
        return False
    flags = record.get("flags", [])
    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
        return False
    return not any(flag in _RELEVANT_FLAGS or any(marker in flag for marker in (
        "ambiguous", "conflict", "association_uncertain", "obstruct", "unable_to_assess",
    ))
                   for flag in record.get("flags", []))


def logo_readiness(records) -> dict:
    """Summarize exact reviewed-logo counts; never implies training or accuracy."""
    rows = list(records)
    per_visibility = {value: 0 for value in LOGO_VISIBILITIES}
    by_category = {}
    reviewed_visible = 0
    known_groups = set()
    unknown_provenance_visible = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        annotation = row.get("logo_annotation") or {}
        if not isinstance(annotation, dict):
            continue
        if _annotation_matches(row, annotation):
            visibility = normalize_visibility(annotation.get("visibility"))
            per_visibility[visibility] += 1
            if visibility == "visible" and is_eligible_visible_annotation(row):
                reviewed_visible += 1
                by_category[row.get("category", "unknown")] = by_category.get(row.get("category", "unknown"), 0) + 1
            if visibility == "visible" and is_eligible_visible_annotation(row):
                provenance = annotation.get("provenance") or {}
                if not isinstance(provenance, dict):
                    capture_groups = []
                    unknown_provenance_visible += 1
                    continue
                capture_groups = provenance.get("capture_groups", [])
                if not isinstance(capture_groups, list):
                    capture_groups = []
                if provenance.get("has_unknown_provenance", True) or not capture_groups:
                    unknown_provenance_visible += 1
                else:
                    known_groups.update(group for group in capture_groups if isinstance(group, str) and group)
    return {
        "status": "Not trained",
        "enabled": False,
        "counts": {
            "total": len(rows),
            "reviewed": sum(per_visibility.values()),
            "reviewed_visible": reviewed_visible,
            "known_capture_groups": len(known_groups),
            "eligible_visible_unknown_provenance": unknown_provenance_visible,
            "per_visibility": per_visibility,
        },
        "by_category": by_category,
        "known_capture_groups": len(known_groups),
        "eligible_visible_unknown_provenance": unknown_provenance_visible,
        "limitations": [
            "Logo annotations are an independent human review record; no logo model has been trained.",
            "Only explicitly reviewed, current photographed-print annotations with confirmed association count as visible examples.",
            "Obscured, too-small, back-view, uncertain, and reference-artwork examples are not treated as logo absence.",
            "Counts do not establish accuracy, coverage sufficiency, or deployment readiness.",
        ],
    }
