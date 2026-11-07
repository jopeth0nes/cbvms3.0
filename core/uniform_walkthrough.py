"""Human-labeled, all-passage scoring for paced camera replay.

Annotations are independent ground truth. Predictions are joined through the
tracked observation presence/track keys and never used to create passage labels.
"""
from __future__ import annotations

import math

from core.uniform_model_registry import CLASSES
from core.uniform_latency import distribution as latency_distribution

MATCH_IOU = 0.25
MAX_HISTORY = 10_000
_POLICY_LABELS = {"compliant", "violation"}


def box_iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = max(0.0, (a[2]-a[0])*(a[3]-a[1])) + max(0.0, (b[2]-b[0])*(b[3]-b[1])) - inter
    return inter / union if union > 0 else 0.0


def _time(value, name, *, optional=True):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid {name} timestamp")
    return float(value)


def _box(value, frame_size=None):
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError("Reviewed boxes must contain four coordinates")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError("Reviewed box coordinates must be finite numbers")
    x1, y1, x2, y2 = map(float, value)
    if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
        raise ValueError("Reviewed box has invalid bounds")
    if frame_size is not None:
        width, height = frame_size
        if x2 > width or y2 > height:
            raise ValueError("Reviewed box extends outside the source raster")
    return [x1, y1, x2, y2]


def _prediction_garment(assessment):
    garment = getattr(assessment, "garment", None)
    value = getattr(garment, "label", None) if garment is not None else None
    return value if value in CLASSES else None


def _prediction_policy(assessment):
    garment = getattr(assessment, "garment", None)
    value = getattr(garment, "policy_outcome", None) if garment is not None else None
    if value in _POLICY_LABELS:
        return value
    legacy = getattr(assessment, "uniform_assessment", None)
    legacy = getattr(legacy, "value", legacy)
    return {"correct_uniform": "compliant", "wrong_uniform": "violation"}.get(legacy)


class WalkthroughMetrics:
    """Aggregate independently annotated passages and per-frame associations."""

    def __init__(self, *, history_limit=MAX_HISTORY):
        if type(history_limit) is not int or not 1 <= history_limit <= MAX_HISTORY:
            raise ValueError("history_limit must be in 1..10000")
        self.history_limit = history_limit
        self.passages = {}
        self.frames = {}
        self.last_annotated_frame = -1
        self.dropped_frame_history = 0
        self.dropped_result_history = 0
        self.dropped_passages = 0
        self.history_used = 0
        self.logical_ids_used = 0
        self.scoring_truncated = False
        self.unmatched_results = 0
        self._matched_presence_by_frame = {}
        self._association_by_frame_presence = {}

    @staticmethod
    def _new_passage(alias):
        return {
            "passage_alias": alias, "truth": {}, "frame_rows": [], "frames_seen": 0,
            "detected": False, "missed": True, "first_detection_ms": None,
            "first_face_ms": None, "first_body_ms": None, "first_torso_ms": None,
            "association_correct": False, "association_mismatch": False,
            "association_unscorable": False, "wrong_person_association": False,
            "prediction_presence_ids": set(), "prediction_logical_ids": set(), "duplicate_logical_result_ids": 0,
            "result_rows": [],
            "attempts": 0, "unassessed_attempts": 0, "late_results": 0,
            "timeout": False, "first_decision_ms": None, "first_decision_label": None,
            "first_policy_ms": None, "first_policy_label": None,
            "correct_before_exit": False, "first_correct_recognition_ms": None,
            "any_wrong_before_exit": False, "first_associated_result_ms": None,
            "garment_assessed_before_exit": False, "policy_assessed_before_exit": False,
            "first_result_ms": None,
            "first_result_after_usable_ms": None,
        }

    def _store_history(self, state, row, kind):
        if self.history_used >= self.history_limit:
            self.scoring_truncated = True
            if kind == "frame":
                self.dropped_frame_history += 1
            else:
                self.dropped_result_history += 1
            return False
        self.history_used += 1
        if kind == "frame":
            state["frame_rows"].append(row)
        else:
            state["result_rows"].append(row)
        return True

    def observe_frame(self, frame_index, media_ms, people, *, frame_size=None):
        if type(frame_index) is not int or frame_index < 0:
            raise ValueError("frame_index must be a nonnegative integer")
        media_ms = _time(media_ms, "media", optional=False)
        if frame_index <= self.last_annotated_frame:
            raise ValueError("Annotation frame rows must be unique and ordered by frame_index")
        self.last_annotated_frame = frame_index
        if not isinstance(people, list):
            raise ValueError("people annotations must be a list")
        if len(people) > 100:
            raise ValueError("At most 100 person rows are allowed per frame")
        frame_people = {}
        for row in people:
            if not isinstance(row, dict):
                raise ValueError("Each person annotation must be an object")
            alias = row.get("passage_alias")
            if not isinstance(alias, str) or not alias.strip() or len(alias) > 160:
                raise ValueError("Each labeled passage requires a bounded passage_alias")
            alias = alias.strip()
            if alias in frame_people:
                raise ValueError("Duplicate passage_alias in one frame annotation row")
            if alias not in self.passages:
                if len(self.passages) >= self.history_limit:
                    self.dropped_passages += 1
                    self.scoring_truncated = True
                    continue
                self.passages[alias] = self._new_passage(alias)
            state = self.passages[alias]
            truth = state["truth"]
            updates = {
                "garment_label": row.get("garment_label"),
                "policy_label": row.get("policy_label"),
                "scenario": row.get("scenario"),
                "camera": row.get("camera", row.get("camera_label")),
                "camera_mode": row.get("camera_mode"),
                "zone_entry_ms": row.get("zone_entry_ms"),
                "zone_exit_ms": row.get("zone_exit_ms"),
                "first_usable_torso_ms": row.get("first_usable_torso_ms"),
                "first_usable_face_ms": row.get("first_usable_face_ms"),
                "usable_torso_status": row.get("usable_torso_status"),
                "usable_face_status": row.get("usable_face_status"),
            }
            if updates["garment_label"] is not None and updates["garment_label"] not in CLASSES:
                raise ValueError("Invalid garment ground truth")
            if updates["policy_label"] == "unknown":
                updates["policy_label"] = None
                truth["policy_label_explicit_unknown"] = True
            elif updates["policy_label"] is not None and updates["policy_label"] not in _POLICY_LABELS:
                raise ValueError("Invalid policy ground truth")
            for key in ("zone_entry_ms", "zone_exit_ms", "first_usable_torso_ms", "first_usable_face_ms"):
                updates[key] = _time(updates[key], key)
            for kind in ("torso", "face"):
                status_key = f"usable_{kind}_status"
                timestamp_key = f"first_usable_{kind}_ms"
                status = updates[status_key]
                if status is None:
                    status = "visible" if updates[timestamp_key] is not None else "unknown"
                if status not in {"visible", "never_usable", "unknown"}:
                    raise ValueError(f"{status_key} must be visible, never_usable, or unknown")
                if status == "visible" and updates[timestamp_key] is None:
                    raise ValueError(f"{status_key}=visible requires {timestamp_key}")
                if status != "visible" and updates[timestamp_key] is not None:
                    raise ValueError(f"{timestamp_key} requires {status_key}=visible")
                updates[status_key] = status
            for key in ("scenario", "camera", "camera_mode"):
                if updates[key] is not None and (not isinstance(updates[key], str) or len(updates[key]) > 160):
                    raise ValueError(f"Invalid {key} annotation")
            if updates["camera_mode"] is not None and updates["camera_mode"] not in {
                "color", "infrared", "unknown",
            }:
                raise ValueError("camera_mode must be color, infrared, or unknown")
            for key, value in updates.items():
                if value is None:
                    continue
                if key in truth and truth[key] != value:
                    raise ValueError(f"Conflicting passage annotation for {alias}: {key}")
                truth[key] = value
            if (truth.get("zone_entry_ms") is not None and truth.get("zone_exit_ms") is not None
                    and truth["zone_exit_ms"] < truth["zone_entry_ms"]):
                raise ValueError("zone_exit_ms precedes zone_entry_ms")
            for key in ("first_usable_torso_ms", "first_usable_face_ms"):
                if truth.get(key) is not None and truth.get("zone_entry_ms") is not None and truth[key] < truth["zone_entry_ms"]:
                    raise ValueError(f"{key} precedes zone entry")
                if truth.get(key) is not None and truth.get("zone_exit_ms") is not None and truth[key] > truth["zone_exit_ms"]:
                    raise ValueError(f"{key} follows zone exit")
            frame = {"passage_alias": alias, "face_box": _box(row.get("face_box"), frame_size),
                     "body_box": _box(row.get("body_box"), frame_size),
                     "torso_box": _box(row.get("torso_box"), frame_size), "media_ms": media_ms}
            state["frames_seen"] += 1
            if self._store_history(state, {"frame_index": frame_index, **frame}, "frame"):
                frame_people[alias] = frame
        if frame_people and len(self.frames) < self.history_limit:
            self.frames[frame_index] = frame_people
        else:
            if frame_people:
                self.dropped_frame_history += 1
                self.scoring_truncated = True

    @staticmethod
    def _face_matches(frame_people, observations):
        pairs = []
        aliases = list(frame_people)
        for li, alias in enumerate(aliases):
            face = frame_people[alias].get("face_box")
            if face is None:
                continue
            for oi, observation in enumerate(observations):
                detected_face = getattr(observation, "box", None)
                if detected_face is None:
                    continue
                try:
                    score = box_iou(face, detected_face)
                except (TypeError, ValueError, IndexError):
                    continue
                if score >= MATCH_IOU:
                    pairs.append((score, alias, oi))
        pairs.sort(reverse=True, key=lambda item: item[0])
        used_aliases, used_observations, matched = set(), set(), {}
        for _, alias, oi in pairs:
            if alias not in used_aliases and oi not in used_observations:
                used_aliases.add(alias)
                used_observations.add(oi)
                matched[alias] = oi
        return matched

    def record_detections(self, frame_index, media_ms, observations):
        frame_people = self.frames.get(frame_index)
        if frame_people is None:
            return {}
        media_ms = _time(media_ms, "detection", optional=False)
        observations = tuple(observations or ())
        matches = self._face_matches(frame_people, observations)
        presence_map = {}
        association_map = {}
        for alias, oi in matches.items():
            observation = observations[oi]
            presence_id = getattr(observation, "presence_id", None)
            if not isinstance(presence_id, str) or not presence_id:
                continue
            presence_map[presence_id] = alias
            state = self.passages[alias]
            state["detected"] = True
            state["missed"] = False
            if state["first_detection_ms"] is None:
                state["first_detection_ms"] = media_ms
            gt = frame_people[alias]
            if gt.get("face_box") is not None and state["first_face_ms"] is None:
                state["first_face_ms"] = media_ms
            body = getattr(observation, "body_box", None)
            torso = getattr(observation, "torso_box", None)
            if gt.get("body_box") is not None and body is not None:
                try:
                    if box_iou(gt["body_box"], body) >= MATCH_IOU and state["first_body_ms"] is None:
                        state["first_body_ms"] = media_ms
                except (TypeError, ValueError, IndexError):
                    pass
            gt_torso = gt.get("torso_box")
            own_iou = None
            other_ious = []
            if gt_torso is not None and torso is not None:
                try:
                    own_iou = box_iou(gt_torso, torso)
                    for other_alias, other in frame_people.items():
                        if other_alias != alias and other.get("torso_box") is not None:
                            other_ious.append((other_alias, box_iou(other["torso_box"], torso)))
                except (TypeError, ValueError, IndexError):
                    own_iou = None
            matched_others = [other for other, iou in other_ious if iou >= MATCH_IOU]
            if own_iou is None:
                state["association_unscorable"] = True
                association_map[presence_id] = "unscorable"
            elif own_iou >= MATCH_IOU and not matched_others:
                state["association_correct"] = True
                association_map[presence_id] = "correct"
                if state["first_torso_ms"] is None:
                    state["first_torso_ms"] = media_ms
            elif own_iou < MATCH_IOU and matched_others:
                state["wrong_person_association"] = True
                association_map[presence_id] = "wrong_person"
            elif own_iou < MATCH_IOU:
                state["association_mismatch"] = True
                association_map[presence_id] = "mismatch"
            else:
                # Both own and neighboring torsos overlap: identity cannot be
                # assigned from geometry alone, even when their garment labels match.
                state["association_unscorable"] = True
                association_map[presence_id] = "unscorable"
            self._store_history(state, {"frame_index": frame_index,
                    "detected_face_box": list(getattr(observation, "box", ())),
                    "detected_body_box": list(body) if body is not None else None,
                    "detected_torso_box": list(torso) if torso is not None else None,
                    "matched_ground_truth_alias": alias,
                    "own_torso_iou": own_iou,
                    "other_torso_matches": matched_others}, "frame")
        self._matched_presence_by_frame[frame_index] = presence_map
        self._association_by_frame_presence[frame_index] = association_map
        if len(self._matched_presence_by_frame) > self.history_limit:
            oldest = min(self._matched_presence_by_frame)
            self._matched_presence_by_frame.pop(oldest, None)
            self._association_by_frame_presence.pop(oldest, None)
        return presence_map

    def record_results(self, frame_index, completion_ms, observations, assessments):
        completion_ms = _time(completion_ms, "completion", optional=False)
        frame_presence = self._matched_presence_by_frame.get(frame_index, {})
        frame_associations = self._association_by_frame_presence.get(frame_index, {})
        observations_by_presence = {
            getattr(observation, "presence_id", None): observation
            for observation in (observations or ())
            if isinstance(getattr(observation, "presence_id", None), str)
        }
        seen_assessment_ids = set()
        for assessment in assessments or ():
            presence_id = getattr(assessment, "presence_id", None)
            if not isinstance(presence_id, str) or presence_id in seen_assessment_ids:
                continue
            seen_assessment_ids.add(presence_id)
            observation = observations_by_presence.get(presence_id)
            if observation is None:
                continue
            alias = frame_presence.get(presence_id)
            if alias is None or alias not in self.passages:
                self.unmatched_results += 1
                continue
            state = self.passages[alias]
            association_correct = frame_associations.get(presence_id) == "correct"
            garment_obj = getattr(assessment, "garment", None)
            logical_id = getattr(garment_obj, "passage_id", None) or presence_id
            if isinstance(logical_id, str) and logical_id not in state["prediction_logical_ids"]:
                if state["prediction_logical_ids"]:
                    state["duplicate_logical_result_ids"] += 1
                if self.logical_ids_used < self.history_limit:
                    self.logical_ids_used += 1
                    state["prediction_logical_ids"].add(logical_id)
                    state["prediction_presence_ids"].add(presence_id)
                else:
                    self.scoring_truncated = True
            garment = _prediction_garment(assessment)
            policy = _prediction_policy(assessment)
            state["attempts"] += 1
            truth = state["truth"]
            entry_ms = truth.get("zone_entry_ms")
            exit_ms = truth.get("zone_exit_ms")
            after_entry = entry_ms is not None and completion_ms >= entry_ms
            before_exit = None if exit_ms is None else completion_ms <= exit_ms
            if after_entry and state["first_result_ms"] is None:
                state["first_result_ms"] = completion_ms
            usable = truth.get("first_usable_torso_ms")
            if (usable is not None and completion_ms >= usable and
                    state["first_result_after_usable_ms"] is None):
                state["first_result_after_usable_ms"] = completion_ms
            reason = getattr(assessment, "reason", None)
            if reason is None and garment_obj is not None:
                reason = getattr(garment_obj, "reason", None)
            self._store_history(state, {"presence_id": presence_id,
                    "logical_result_id": logical_id,
                    "completion_media_ms": completion_ms, "garment_label": garment,
                    "policy_label": policy,
                    "reason": reason if isinstance(reason, str) else None,
                    "after_zone_entry": after_entry,
                    "association": frame_associations.get(presence_id, "unscorable"),
                    "before_zone_exit": before_exit}, "result")
            if (not after_entry or before_exit is not True or
                    truth.get("usable_torso_status") == "never_usable" or
                    truth.get("usable_face_status") == "never_usable"):
                if before_exit is False:
                    state["late_results"] += 1
                continue
            if association_correct and state["first_associated_result_ms"] is None:
                state["first_associated_result_ms"] = completion_ms
            if garment is None:
                state["unassessed_attempts"] += 1
            elif association_correct and state["first_decision_ms"] is None:
                state["first_decision_ms"] = completion_ms
                state["first_decision_label"] = garment
            if garment is not None and association_correct:
                state["garment_assessed_before_exit"] = True
            if policy is not None and association_correct and state["first_policy_ms"] is None:
                state["first_policy_ms"] = completion_ms
                state["first_policy_label"] = policy
            if (association_correct and garment is not None and truth.get("garment_label") is not None
                    and garment != truth["garment_label"]):
                state["any_wrong_before_exit"] = True
            if (garment is not None and garment == truth.get("garment_label")
                    and association_correct):
                state["correct_before_exit"] = True
                if state["first_correct_recognition_ms"] is None:
                    state["first_correct_recognition_ms"] = completion_ms
            if policy is not None and truth.get("policy_label") is not None and association_correct:
                state["policy_assessed_before_exit"] = True

    def report(self, *, interval_end_ms=None, input_complete=True):
        interval_end_ms = _time(interval_end_ms, "interval end")
        rows = list(self.passages.values())
        garment_labeled = [p for p in rows if p["truth"].get("garment_label") is not None]
        missing = {
            "passage_alias": self.dropped_passages,
            "garment_label": sum(p["truth"].get("garment_label") is None for p in rows),
            "policy_label": sum(p["truth"].get("policy_label") is None for p in rows),
            "zone_entry_ms": sum(p["truth"].get("zone_entry_ms") is None for p in rows),
            "zone_exit_ms": sum(p["truth"].get("zone_exit_ms") is None for p in rows),
            "first_usable_torso_ms": sum(p["truth"].get("first_usable_torso_ms") is None for p in rows),
            "first_usable_face_ms": sum(p["truth"].get("first_usable_face_ms") is None for p in rows),
            "usable_torso_status": sum(p["truth"].get("usable_torso_status") == "unknown" for p in rows),
            "usable_face_status": sum(p["truth"].get("usable_face_status") == "unknown" for p in rows),
            "scenario": sum(p["truth"].get("scenario") is None for p in rows),
            "camera": sum(p["truth"].get("camera") is None for p in rows),
            "camera_mode": sum(p["truth"].get("camera_mode") is None for p in rows),
            "face_box": sum(p["truth"].get("usable_face_status") == "visible" and
                             not any(r.get("face_box") is not None for r in p["frame_rows"]) for p in rows),
            "torso_box": sum(p["truth"].get("usable_torso_status") == "visible" and
                             not any(r.get("torso_box") is not None for r in p["frame_rows"]) for p in rows),
        }
        complete_labels = all(missing[key] == 0 for key in (
            "garment_label", "zone_entry_ms", "zone_exit_ms", "usable_torso_status", "usable_face_status",
            "face_box", "torso_box"))
        incomplete_at_cutoff = sum(not input_complete and (
            p["truth"].get("zone_exit_ms") is None or
            (interval_end_ms is not None and p["truth"].get("zone_exit_ms", 0) > interval_end_ms))
            for p in rows)
        score_complete = complete_labels and incomplete_at_cutoff == 0 and not self.scoring_truncated and bool(rows)
        scored_correct = sum(p["correct_before_exit"] for p in garment_labeled)
        denom = len(garment_labeled)
        class_metrics = {}
        for category in CLASSES:
            members = [p for p in garment_labeled if p["truth"].get("garment_label") == category]
            class_metrics[category] = {
                "support": len(members),
                "correct_associated_before_exit": sum(p["correct_before_exit"] for p in members),
                "recognized_errors": sum(p["any_wrong_before_exit"] or p["wrong_person_association"]
                                         for p in members),
                "unassessed": sum(not p["garment_assessed_before_exit"] for p in members),
            }
        policy_rows = [p for p in rows if p["truth"].get("policy_label") is not None]
        policy_scored = [p for p in policy_rows if p["policy_assessed_before_exit"]]
        policy_false = sum(p["truth"].get("policy_label") == "compliant" and p["first_policy_label"] == "violation"
                           for p in policy_scored)
        policy_missed = sum(p["truth"].get("policy_label") == "violation" and p["first_policy_label"] == "compliant"
                            for p in policy_scored)
        entry_to_result = []
        usable_to_result = []
        entry_to_correct = []
        usable_to_correct = []
        latency_face = []
        for p in rows:
            entry = p["truth"].get("zone_entry_ms")
            first_result = p["first_result_ms"]
            usable = p["truth"].get("first_usable_torso_ms")
            face = p["truth"].get("first_usable_face_ms")
            correct = p["first_correct_recognition_ms"]
            if entry is not None and first_result is not None and first_result >= entry:
                entry_to_result.append(first_result - entry)
            usable_result = p["first_result_after_usable_ms"]
            if usable is not None and usable_result is not None:
                usable_to_result.append(usable_result - usable)
            if entry is not None and correct is not None and correct >= entry:
                entry_to_correct.append(correct - entry)
            if usable is not None and correct is not None and correct >= usable:
                usable_to_correct.append(correct - usable)
            if face is not None and correct is not None and correct >= face:
                latency_face.append(correct - face)
        trial_population = [p for p in garment_labeled if p["truth"].get("usable_torso_status") in {"visible", "never_usable"}]
        successes_500 = sum(p["first_correct_recognition_ms"] is not None
            and p["first_correct_recognition_ms"] >= p["truth"]["first_usable_torso_ms"]
            and p["first_correct_recognition_ms"] - p["truth"]["first_usable_torso_ms"] <= 500
            for p in trial_population if p["truth"].get("usable_torso_status") == "visible")

        def distribution(values, valid=True):
            result = latency_distribution(values if valid else [])
            return {**result, "values_ms": values,
                    "status": ("unavailable_truncated" if not valid else
                               "measured" if values else "unavailable_no_results"),
                    "small_sample": len(values) < 30,
                    "caveat": "Descriptive sample percentiles, not a validated population p95."}
        scenarios = {}
        cameras = {}
        for p in rows:
            for key, bucket in (("scenario", scenarios), ("camera", cameras)):
                value = p["truth"].get(key)
                if value is not None:
                    bucket[value] = bucket.get(value, 0) + 1
        detailed = []
        for p in rows:
            truth = p["truth"]
            detail = {k: truth.get(k) for k in (
                "garment_label", "policy_label", "scenario", "camera", "camera_mode",
                "zone_entry_ms", "zone_exit_ms", "first_usable_torso_ms", "first_usable_face_ms",
                "usable_torso_status", "usable_face_status")}
            detail["passage_alias"] = p["passage_alias"]
            detail.update({k: p[k] for k in (
                "detected", "missed", "first_detection_ms", "first_face_ms", "first_body_ms",
                "first_torso_ms", "association_correct", "association_mismatch", "association_unscorable",
                "wrong_person_association", "duplicate_logical_result_ids", "attempts",
                "unassessed_attempts", "late_results", "first_decision_ms", "first_decision_label",
                "first_result_ms", "first_result_after_usable_ms",
                "first_policy_ms", "first_policy_label", "correct_before_exit",
                "first_correct_recognition_ms", "any_wrong_before_exit")})
            detail["frame_rows"] = p["frame_rows"][:self.history_limit]
            detail["result_rows"] = p["result_rows"][:self.history_limit]
            detailed.append(detail)
        return {
            "schema": "uniform-walkthrough-v1",
            "passage_count": len(rows),
            "labeled_garment_passages": denom,
            "garment_correct_associated_before_exit": scored_correct if score_complete else None,
            "garment_accuracy_all_labeled_passages": scored_correct / denom if denom and score_complete else None,
            "garment_accuracy_status": ("unavailable_no_passages" if not rows else
                "unavailable_history_truncated" if self.scoring_truncated else
                "scored" if score_complete else
                "incomplete_at_cutoff" if complete_labels and incomplete_at_cutoff else
                "unavailable_incomplete_ground_truth"),
            "garment_scored_denominator": denom,
            "association_scored_passages": sum(p["association_correct"] or p["wrong_person_association"] or p["association_mismatch"]
                                                for p in rows),
            "correct_association_passages": sum(p["association_correct"] for p in rows),
            "wrong_person_association_passages": sum(p["wrong_person_association"] for p in rows),
            "association_mismatch_passages": sum(p["association_mismatch"] for p in rows),
            "association_unscorable_passages": sum(p["association_unscorable"] for p in rows),
            "detected_passages": sum(p["detected"] for p in rows),
            "missed_passages": sum(p["missed"] for p in rows),
            "garment_per_class": class_metrics,
            "unassessed_passages": sum(not p["garment_assessed_before_exit"] for p in garment_labeled),
            "human_never_usable_view_passages": sum(p['truth'].get('usable_face_status') == 'never_usable' or
                p['truth'].get('usable_torso_status') == 'never_usable' for p in rows),
            "assessed_before_exit_passages": sum(p["garment_assessed_before_exit"] for p in garment_labeled),
            "policy": {"ground_truth_passages": len(policy_rows), "scored_passages": len(policy_scored),
                       "false_violations": policy_false if policy_rows else None,
                       "missed_violations": policy_missed if policy_rows else None,
                       "unassessed_passages": len(policy_rows) - len(policy_scored)},
            "timing": {"first_detection_media_ms": [p["first_detection_ms"] for p in rows if p["first_detection_ms"] is not None],
                       "entry_to_first_result": distribution(entry_to_result, not self.scoring_truncated),
                       "usable_torso_to_first_result": distribution(usable_to_result, not self.scoring_truncated),
                       "entry_to_first_correct_associated_recognition": distribution(entry_to_correct, not self.scoring_truncated),
                       "usable_torso_to_first_correct_associated_recognition": distribution(usable_to_correct, not self.scoring_truncated),
                       "first_correct_recognition_from_usable_torso_ms": usable_to_correct,
                       "first_correct_recognition_from_usable_face_ms": latency_face,
                       "fixed_500ms_torso_trial": {"successes": successes_500, "denominator": len(trial_population),
                           "eligible": len(trial_population), "target_ms": 500,
                           "success_rate": successes_500 / len(trial_population) if trial_population and score_complete else None,
                           "status": "measured" if score_complete else "unavailable_incomplete_or_truncated",
                           "failures_including_never_usable": len(trial_population) - successes_500,
                           "caveat": "Small-sample replay target; not a physical-camera guarantee."}},
            "late_results": sum(p["late_results"] for p in rows),
            "timed_out_passages": sum(p["truth"].get("zone_exit_ms") is not None and
                (interval_end_ms is None or p["truth"].get("zone_exit_ms") <= interval_end_ms) and
                not p["garment_assessed_before_exit"] for p in garment_labeled),
            "duplicate_logical_result_ids": sum(p["duplicate_logical_result_ids"] for p in rows),
            "scenario_coverage": scenarios,
            "camera_coverage": cameras,
            "camera_mode_coverage": {mode: sum(p["truth"].get("camera_mode") == mode for p in rows)
                                     for mode in ("color", "infrared", "unknown")},
            "missing_data": missing,
            "incomplete_at_cutoff_passages": incomplete_at_cutoff,
            "input_complete": bool(input_complete),
            "history": {"limit": self.history_limit, "used": self.history_used,
                        "logical_ids_used": self.logical_ids_used,
                        "stored_frame_rows": sum(len(p["frame_rows"]) for p in rows),
                        "dropped_frame_rows": self.dropped_frame_history,
                        "dropped_result_rows": self.dropped_result_history,
                        "dropped_passages": self.dropped_passages,
                        "scoring_truncated": self.scoring_truncated,
                        "unmatched_results": self.unmatched_results},
            "matching": {"face_iou_threshold": MATCH_IOU,
                          "association_rule": "Own reviewed torso overlap required; another person's torso match is wrong-person; own+other overlap is unscorable.",
                          "ground_truth_source": "human annotation only; no tracks/predictions infer labels or passage exits."},
            "passages": detailed,
            "limitations": [
                "All-passage garment rate is null when required independent passage, time, or geometry labels are incomplete.",
                "Identity remains the separate authorized-gallery metric; this scorer does not measure identity accuracy.",
                "Policy counts use only explicit human policy truth and are separate from garment classification.",
                "A result arriving after annotated zone exit is late and cannot replace a prior timely result.",
            ],
        }
