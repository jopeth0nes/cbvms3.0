"""Frame-bound Live Monitor ownership and evidence, independent of Tk and models.

One analysis worker owns ``LiveState``. It supplies ALL faces from one raw camera
frame, after face/body assignment and crop validation. No result is rebound to a
newer frame by overlap. Returned assessments contain only immutable values.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
from typing import Hashable, Mapping, Sequence

import numpy as np

Box = tuple[int, int, int, int]
IDENTIFYING = "Identifying"
LOCATING_TORSO = "Locating torso"
IDENTITY_UNCERTAIN = "Identity uncertain"
CHECKING_UNIFORM = "Checking uniform"
UNIFORM_COMPLIANT = "Uniform compliant"
SUSPECTED_VIOLATION = "Suspected uniform violation"
UNIFORM_NOT_ASSESSED = "Uniform not assessed"
UNKNOWN_PERSON = "Unknown person"


def _box(value) -> Box | None:
    try:
        coords = tuple(float(v) for v in value)
        if len(coords) != 4 or not all(math.isfinite(v) for v in coords):
            return None
        if coords[2] <= coords[0] or coords[3] <= coords[1]:
            return None
        result = tuple(int(round(v)) for v in coords)
        return result if result[2] > result[0] and result[3] > result[1] else None
    except (TypeError, ValueError, OverflowError):
        return None


def _area(box) -> float:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _intersection(a, b) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


@dataclass(frozen=True)
class FrameContext:
    generation: int
    frame_id: Hashable
    captured_at: float  # time.monotonic(), captured by the camera owner

    def is_fresh(self, generation: int, now: float, max_age: float = 3.0) -> bool:
        age = now - self.captured_at
        return self.generation == generation and math.isfinite(age) and 0 <= age <= max_age


@dataclass(frozen=True)
class BodyAssociation:
    index: int | None
    reason: str = ""

    @property
    def valid(self) -> bool:
        return self.index is not None


def associate_faces_to_bodies(face_boxes: Sequence, person_boxes: Sequence,
                              *, ambiguity_margin: float = 0.12) -> tuple[BodyAssociation, ...]:
    """Conservative one-to-one ownership; never use body order or largest area.

    Faces must lie within the upper portion of a plausible body. Close competing
    body scores or several faces claiming one body cause abstention for everyone
    involved; a second-best body is never invented to fill an assignment.
    """
    faces = [_box(b) for b in face_boxes]
    bodies = [_box(b) for b in person_boxes]
    proposed: list[BodyAssociation] = []
    contested_bodies: set[int] = set()
    for face in faces:
        scores = []
        if face is not None:
            fw, fh = face[2] - face[0], face[3] - face[1]
            fx, fy = _center(face)
            for index, body in enumerate(bodies):
                if body is None:
                    continue
                bw, bh = body[2] - body[0], body[3] - body[1]
                overlap = _intersection(face, body) / _area(face)
                upper = (fy - body[1]) / bh
                horizontal = abs(fx - _center(body)[0]) / (bw / 2)
                if overlap < .80 or not 0 <= upper <= .45 or horizontal > .80:
                    continue
                if bw < .85 * fw or bh < 1.5 * fh:
                    continue
                score = .50 * overlap + .30 * (1 - horizontal) + .20 * (1 - upper / .45)
                scores.append((score, index))
        scores.sort(reverse=True)
        if not scores:
            proposed.append(BodyAssociation(None, "No confidently associated body"))
        elif len(scores) > 1 and scores[0][0] - scores[1][0] < ambiguity_margin:
            contested_bodies.update(index for score, index in scores
                                     if scores[0][0] - score < ambiguity_margin)
            proposed.append(BodyAssociation(None, "Ambiguous face/body association"))
        else:
            proposed.append(BodyAssociation(scores[0][1]))
    claims: dict[int, int] = {}
    for association in proposed:
        if association.valid:
            claims[association.index] = claims.get(association.index, 0) + 1
    return tuple(BodyAssociation(None, "Multiple faces claim the same body")
                 if item.valid and (claims[item.index] > 1 or item.index in contested_bodies)
                 else item for item in proposed)


def validate_torso(face_box, body_box, torso_box, frame_shape,
                   *, other_faces: Sequence = (), other_bodies: Sequence = (),
                   min_crop_px: int = 32, max_contamination: float = .20) -> tuple[bool, str]:
    """Geometry/visibility gate before the existing skin and classifier checks.

    ``other_bodies`` excludes the associated body. Overlap is treated as possible
    occlusion, not proof of depth, so a contaminated torso is left unassessed.
    """
    face, body, torso = _box(face_box), _box(body_box), _box(torso_box)
    if face is None or body is None or torso is None:
        return False, "No valid face, body, and torso association"
    height, width = frame_shape[:2]
    visible = (max(0, torso[0]), max(0, torso[1]), min(width, torso[2]), min(height, torso[3]))
    if min(visible[2] - visible[0], visible[3] - visible[1]) < min_crop_px:
        return False, "Torso crop is too small"
    if _area(visible) / _area(torso) < .90:
        return False, "Torso is partly outside the frame"
    if torso[1] < face[3] or _intersection(torso, body) / _area(torso) < .90:
        return False, "Torso does not belong within the associated body"
    if torso[2] - torso[0] < .50 * (face[2] - face[0]):
        return False, "Too little visible shirt"
    for other in other_faces:
        other = _box(other)
        if other is not None and _intersection(torso, other) > .03 * _area(torso):
            return False, "Another face overlaps the torso"
    for other in other_bodies:
        other = _box(other)
        if other is not None and _intersection(torso, other) > max_contamination * _area(torso):
            return False, "Another person may obscure the torso"
    return True, ""


@dataclass(frozen=True)
class LiveConfig:
    identity_frames: int = 2
    uniform_confirm_frames: int = 3
    uniform_clear_frames: int = 3
    earring_confirm_frames: int = 2
    evidence_window: int = 5
    evidence_ttl: float = 8.0
    identity_ttl: float = 3.0
    track_ttl: float = 2.5
    max_result_age: float = 3.0
    appearance_distance: float = .50
    association_margin: float = .10
    uniform_min_confidence: float = .60
    violation_min_confidence: float = .75

    def __post_init__(self):
        counts = (self.identity_frames, self.uniform_confirm_frames,
                  self.uniform_clear_frames, self.earring_confirm_frames)
        if min(counts) < 1 or self.evidence_window < max(counts[1:]):
            raise ValueError("Evidence counts must be positive and fit within the window")
        if min(self.evidence_ttl, self.identity_ttl, self.track_ttl, self.max_result_age) <= 0:
            raise ValueError("Expiry intervals must be positive")


@dataclass(frozen=True)
class LiveAssessment:
    context: FrameContext
    track_id: int
    presence_id: str
    face_box: Box
    body_index: int | None
    body_box: Box | None
    torso_box: Box | None
    student_id: str
    name: str
    gender: str
    student_status: str
    suspension_tag: str
    discipline_eligible: bool
    reliable_identity: bool
    state: str
    reason: str
    uniform_label: str | None
    uniform_confidence: float
    accepted_categories: tuple[str, ...]
    association_valid: bool
    academic_snapshot: tuple = ()
    term_snapshot: tuple = ()

    @property
    def matched(self) -> bool:
        return self.reliable_identity

    @property
    def box(self) -> Box:
        return self.face_box

    @property
    def uniform_conf(self) -> float:
        return self.uniform_confidence


@dataclass
class _Track:
    id: int
    box: Box
    last_at: float
    embedding: np.ndarray | None
    owner_token: str | None = None
    velocity: tuple[float, float] = (0., 0.)
    candidate_id: str = ""
    identity_hits: int = 0
    identity_at: float = 0.
    previously_known: bool = False
    identity_changed: bool = False
    body_signature: tuple[float, ...] | None = None
    association_token: object = None
    uniform: deque = field(default_factory=deque)
    earrings: deque = field(default_factory=deque)
    stable_uniform: str | None = None

    def clear_uniform(self):
        self.uniform.clear()
        self.stable_uniform = None
        self.body_signature = None
        self.association_token = None

    def clear_identity(self):
        self.candidate_id = ""
        self.identity_hits = 0
        self.clear_uniform()
        self.earrings.clear()


def _embedding(row):
    try:
        vector = np.asarray(row.get("embedding"), dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        return vector / norm if vector.size and np.isfinite(vector).all() and norm > 0 else None
    except (TypeError, ValueError):
        return None


class LiveState:
    """Conservative tracking + identity-scoped evidence for sequential full frames.

    Each row has recognition fields (box, embedding, matched, student_id, name),
    standing metadata, ``association_valid``, ``body_index``, ``body_box``,
    ``torso_valid``, ``torso_box``, ``uniform_available``, ``uniform_label`` and
    ``uniform_conf`` (confidence of that label), plus optional ``earring_violation``.
    Missing validation flags fail closed. ``association_token``, if supplied, must
    identify OWNERSHIP, never a body's array index, which can change with sorting.
    """

    def __init__(self, config: LiveConfig | None = None):
        self.config = config or LiveConfig()
        self._generation: int | None = None
        self._tracks: list[_Track] = []
        self._next_id = 1
        self._seen_frames: deque = deque(maxlen=128)
        self._last_at = -math.inf

    def reset(self, generation: int | None = None):
        self._generation = generation
        self._tracks.clear()
        self._seen_frames.clear()
        self._last_at = -math.inf
        self._next_id = 1

    def _pair_cost(self, track, box, emb, captured_at):
        dt = max(0., captured_at - track.last_at)
        predicted = tuple(c + v * min(dt, 1.5) for c, v in zip(_center(track.box), track.velocity))
        scale = max(20., (box[2] - box[0] + track.box[2] - track.box[0]) / 2)
        distance = math.dist(predicted, _center(box)) / scale
        size_ratio = max(_area(box), _area(track.box)) / min(_area(box), _area(track.box))
        if size_ratio > 4 or distance > 5:
            return None
        if emb is not None and track.embedding is not None and emb.shape == track.embedding.shape:
            appearance = max(0., 1. - float(emb @ track.embedding))
            if appearance > self.config.appearance_distance:
                return None
            return .85 * appearance + .15 * min(distance / 3., 1.)
        # Missing embeddings are allowed only for close, unambiguous motion.
        if distance > .75:
            return None
        return .40 + .30 * distance

    def _associate(self, boxes, embeddings, captured_at, tokens=None):
        candidates: dict[tuple[int, int], float] = {}
        for ti, track in enumerate(self._tracks):
            for di, (box, emb) in enumerate(zip(boxes, embeddings)):
                if tokens is not None and tokens[di] != track.owner_token:
                    continue
                cost = self._pair_cost(track, box, emb, captured_at)
                if cost is not None:
                    candidates[ti, di] = cost
        matches, ambiguous_dets, retire_tracks = {}, set(), set()
        # Mutual best with a margin on BOTH sides. Never greedily remap a loser to
        # a second-best person, especially when two people cross or are occluded.
        for (ti, di), cost in sorted(candidates.items(), key=lambda item: item[1]):
            row = sorted((v, d) for (t, d), v in candidates.items() if t == ti)
            col = sorted((v, t) for (t, d), v in candidates.items() if d == di)
            if row[0][1] != di or col[0][1] != ti:
                continue
            row_ambiguous = len(row) > 1 and row[1][0] - cost < self.config.association_margin
            col_ambiguous = len(col) > 1 and col[1][0] - cost < self.config.association_margin
            if row_ambiguous or col_ambiguous:
                contested_dets = {d for v, d in row if v - cost < self.config.association_margin}
                contested_tracks = {t for v, t in col if v - cost < self.config.association_margin}
                ambiguous_dets.update(contested_dets)
                retire_tracks.update(contested_tracks)
            else:
                matches[di] = ti
        for di in tuple(matches):
            if di in ambiguous_dets or matches[di] in retire_tracks:
                ambiguous_dets.add(di)
                retire_tracks.add(matches.pop(di))
        return matches, ambiguous_dets, retire_tracks

    def update(self, context: FrameContext, rows: Sequence[Mapping], *, now: float | None = None
               ) -> tuple[LiveAssessment, ...]:
        now = time.monotonic() if now is None else now
        if self._generation is not None and context.generation < self._generation:
            return ()
        if not context.is_fresh(context.generation, now, self.config.max_result_age):
            return ()
        if context.generation != self._generation:
            self.reset(context.generation)
        if context.frame_id in self._seen_frames or context.captured_at <= self._last_at:
            return ()
        self._seen_frames.append(context.frame_id)
        self._last_at = context.captured_at
        self._tracks = [t for t in self._tracks if context.captured_at - t.last_at <= self.config.track_ttl]
        rows = [dict(row) for row in rows if _box(row.get("box")) is not None]
        boxes = [_box(row["box"]) for row in rows]
        embeddings = [_embedding(row) for row in rows]
        tokens = [row.get('owner_token') for row in rows] if any('owner_token' in r for r in rows) else None
        matches, ambiguous, retire = self._associate(boxes, embeddings, context.captured_at, tokens)
        matched_tracks = set(matches.values())
        for index, track in enumerate(self._tracks):
            if index not in matched_tracks:
                # Retain appearance/motion briefly to recover the temporary ID,
                # but an occlusion is a break in validated ownership. Returning
                # faces must reestablish identity and uniform evidence.
                track.clear_identity()
        claims: dict[str, int] = {}
        for row in rows:
            if row.get("matched") and row.get("student_id"):
                sid = str(row["student_id"])
                claims[sid] = claims.get(sid, 0) + 1
        assessments = []
        new_tracks = []
        for di, (row, box, emb) in enumerate(zip(rows, boxes, embeddings)):
            uncertain = di in ambiguous or bool(row.get("identity_uncertain"))
            sid = str(row.get("student_id") or "") if row.get("matched") else ""
            uncertain = uncertain or bool(sid and claims.get(sid, 0) > 1)
            if di in matches:
                track = self._tracks[matches[di]]
                dt = context.captured_at - track.last_at
                if dt > 0:
                    # Smooth detector jitter; a single small fast-step error must not
                    # extrapolate a stationary face far away after a brief missed frame.
                    track.velocity = tuple(.7*v + .3*(b-a)/dt for v,a,b in
                                           zip(track.velocity, _center(track.box), _center(box)))
                track.box, track.last_at = box, context.captured_at
                if emb is not None:
                    track.embedding = emb.copy()
            else:
                track = _Track(self._next_id, box, context.captured_at, emb,
                               owner_token=row.get('owner_token'))
                self._next_id += 1
                new_tracks.append(track)
                from core.diagnostics import event
                event('track_created', track_id=track.id, frame_id=context.frame_id,
                      owner_token=track.owner_token, ambiguous=uncertain)
            assessments.append(self._assess(track, context, row, uncertain))
        self._tracks = [t for i, t in enumerate(self._tracks) if i not in retire] + new_tracks
        return tuple(assessments)

    def _assess(self, track, context, row, uncertain):
        cfg = self.config
        sid = str(row.get("student_id") or "") if row.get("matched") else ""
        if sid == "unknown":
            sid = ""
        if uncertain or not sid:
            track.clear_identity()
        elif sid != track.candidate_id or context.captured_at - track.identity_at > cfg.identity_ttl:
            changed = bool(track.candidate_id or track.previously_known)
            track.clear_identity()
            track.identity_changed = changed
            track.candidate_id = sid
            track.identity_hits = 1
        else:
            track.identity_hits += 1
        track.identity_at = context.captured_at
        reliable = bool(sid and not uncertain and track.identity_hits >= cfg.identity_frames)
        eligible = reliable and bool(row.get("discipline_eligible", True))
        if reliable:
            track.previously_known = True
            track.identity_changed = False
        body = _box(row.get("body_box"))
        torso = _box(row.get("torso_box"))
        association_valid = bool(row.get("association_valid") and body is not None)
        torso_valid = bool(association_valid and row.get("torso_valid") and torso is not None)
        accepted = []
        label, confidence = None, 0.
        reason = str(row.get("uniform_reason") or row.get("reason") or "")
        if uncertain or (not reliable and (track.identity_changed or (not sid and track.previously_known))):
            state = IDENTITY_UNCERTAIN
            reason = "Identity needs fresh, unambiguous confirmation"
        elif not sid:
            state = UNKNOWN_PERSON
            reason = "No enrolled student match"
        elif not reliable:
            state = IDENTIFYING
            reason = "Confirming identity across fresh frames"
        elif not eligible:
            state = UNIFORM_NOT_ASSESSED
            reason = "Student is not eligible for disciplinary checks"
        elif not torso_valid or not row.get("uniform_available", False):
            state = UNIFORM_NOT_ASSESSED
            reason = reason or "A visible, confidently associated torso is required"
        else:
            state = CHECKING_UNIFORM

        if not eligible or not torso_valid or not row.get("uniform_available", False):
            track.clear_uniform()
        else:
            fw, fh = track.box[2] - track.box[0], track.box[3] - track.box[1]
            signature = ((body[0] - track.box[0]) / fw, (body[1] - track.box[1]) / fh,
                         (body[2] - track.box[2]) / fw, (body[3] - track.box[3]) / fh)
            token = row.get("association_token")
            if ((track.body_signature is not None
                 and max(abs(a-b) for a, b in zip(signature, track.body_signature)) > 1.25)
                    or (track.association_token is not None and token != track.association_token)):
                track.clear_uniform()
            track.body_signature, track.association_token = signature, token
            fresh = context.captured_at - cfg.evidence_ttl
            track.uniform = deque((entry for entry in track.uniform if entry[0] >= fresh), maxlen=cfg.evidence_window)
            raw_label = row.get("uniform_label")
            try:
                raw_conf = float(row.get("uniform_confidence", row.get("uniform_conf", 0.)) or 0.)
            except (TypeError, ValueError):
                raw_conf = 0.
            threshold = cfg.violation_min_confidence if raw_label == "wrong_uniform" else cfg.uniform_min_confidence
            if raw_label in ("correct_uniform", "wrong_uniform") and math.isfinite(raw_conf) and threshold <= raw_conf <= 1.:
                track.uniform.append((context.captured_at, context.frame_id, raw_label, raw_conf))
            else:
                # A classifier abstention has no verdict and must never read as OK.
                state = UNIFORM_NOT_ASSESSED
                reason = "Uniform result is unavailable or inconclusive"
            wrong = [e for e in track.uniform if e[2] == "wrong_uniform"]
            correct = [e for e in track.uniform if e[2] == "correct_uniform"]
            if len(wrong) >= cfg.uniform_confirm_frames and raw_label == "wrong_uniform":
                track.stable_uniform = "wrong_uniform"
            elif len(correct) >= cfg.uniform_clear_frames and raw_label == "correct_uniform":
                track.stable_uniform = "correct_uniform"
            elif ((track.stable_uniform == "wrong_uniform" and len(wrong) < cfg.uniform_confirm_frames)
                  or (track.stable_uniform == "correct_uniform" and len(correct) < cfg.uniform_clear_frames)):
                track.stable_uniform = None
            if state == CHECKING_UNIFORM and track.stable_uniform is not None and raw_label != track.stable_uniform:
                reason = "Confirming changed uniform result"
            if state == CHECKING_UNIFORM and track.stable_uniform is not None and raw_label == track.stable_uniform:
                label = track.stable_uniform
                agreeing = wrong if label == "wrong_uniform" else correct
                confidence = sum(e[3] for e in agreeing) / len(agreeing)
                state = SUSPECTED_VIOLATION if label == "wrong_uniform" else UNIFORM_COMPLIANT
                reason = ""
                # Retry writes on a subsequent fresh supporting frame. A held state
                # never logs a new violation against a currently compliant snapshot.
                if label == "wrong_uniform" and raw_label == label and raw_conf >= cfg.violation_min_confidence:
                    accepted.append("wrong_uniform")

        # Earring evidence has its own eligibility, crop, and category; torso
        # unavailability must not disable the existing male-only face-crop check.
        earring = row.get("earring_violation")
        earring_eligible = eligible and str(row.get("gender") or "").lower() == "male"
        if not earring_eligible or earring is None:
            track.earrings.clear()
        else:
            track.earrings = deque((e for e in track.earrings if e[0] >= context.captured_at - cfg.evidence_ttl), maxlen=cfg.evidence_window)
            track.earrings.append((context.captured_at, context.frame_id, bool(earring)))
            if earring and sum(e[2] for e in track.earrings) >= cfg.earring_confirm_frames:
                accepted.append("earring")
        return LiveAssessment(
            context=context, track_id=track.id,
            presence_id=f"person:{context.generation}:{track.id}",
            face_box=track.box, body_index=row.get("body_index") if association_valid else None,
            body_box=body if association_valid else None, torso_box=torso if torso_valid else None,
            student_id=sid if reliable else "", name=str(row.get("name") or sid) if reliable else ("Unknown" if state == UNKNOWN_PERSON else state),
            gender=str(row.get("gender") or "—"), student_status=str(row.get("student_status") or "Enrolled") if reliable else "",
            suspension_tag=str(row.get("suspension_tag") or "") if reliable else "",
            discipline_eligible=eligible, reliable_identity=reliable,
            state=state, reason=reason, uniform_label=label, uniform_confidence=confidence,
            accepted_categories=tuple(accepted), association_valid=association_valid,
            academic_snapshot=tuple(row.get("academic_snapshot", ())),
            term_snapshot=tuple(row.get("term_snapshot", ())),
        )
