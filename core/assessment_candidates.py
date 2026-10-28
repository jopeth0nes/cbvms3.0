"""Bounded observation-only routing of immutable camera frames.

Candidate tickets remain pending until a worker completion/rejection callback.
The router owns no models, identities, persistence, or notification effects.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import os
import threading
import time
import uuid

import cv2

from core.diagnostics import event
from core.live_state import validate_torso
from core.performance import PERF


def _enabled(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


OBSERVATION_ONLY = _enabled("CBVMS_ASSESSMENT_OBSERVATION_ONLY")
CLEAR_FRAME_SELECTION = _enabled("CBVMS_CLEAR_FRAME_SELECTION")
PRIORITIZED_ASSESSMENT = _enabled("CBVMS_PRIORITIZED_ASSESSMENT")


@dataclass(frozen=True)
class Candidate:
    ticket_id: str
    task: object
    presence_id: str
    track_id: int
    face_box: tuple[int, int, int, int]
    body_box: tuple[int, int, int, int] | None
    torso_box: tuple[int, int, int, int] | None
    score: float
    reason: str
    first_seen_at: float
    usable_at: float
    queued_at: float
    quality_metrics: tuple[tuple[str, float], ...]
    cancelled: threading.Event
    passage_validity: threading.Event

    @property
    def frame_key(self):
        c = self.task.context
        return (self.task.source_id, c.generation, self.task.camera_generation, c.frame_id)

    @property
    def passage_key(self):
        c = self.task.context
        return (self.task.source_id, c.generation, self.task.camera_generation, self.presence_id)

    @property
    def priority(self):
        return {"new": 0, "near_exit": 1, "better": 1, "confirmation": 2}[self.reason]


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _sharpness(crop):
    if crop is None or crop.size == 0:
        return 0.0
    sample = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    return min(1.0, math.log1p(float(cv2.Laplacian(gray, cv2.CV_64F).var())) / 8.0)


def _exposure(crop):
    if crop is None or crop.size == 0:
        return 0.0
    mean = float(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean())
    return max(0.0, 1.0 - abs(mean - 128.0) / 128.0)


def _size_quality(box, frame_shape, target_fraction):
    h, w = frame_shape[:2]
    fraction = _area(box) / max(1, h * w)
    return min(1.0, max(0.0, math.log1p(fraction * 10000) /
                            math.log1p(target_fraction * 10000)))


class ClearFrameRouter:
    """One-at-a-time dispatcher with per-passage and global memory bounds."""

    def __init__(self, *, max_tracks=48, per_passage=2, max_candidates=24,
                 max_bytes=24 * 1024 * 1024, ttl=3.0, max_queue=48,
                 refresh_interval=.5):
        if min(max_tracks, per_passage, max_candidates, max_bytes, max_queue) <= 0 or ttl <= 0:
            raise ValueError("candidate limits must be positive")
        self.max_tracks, self.per_passage = max_tracks, per_passage
        self.max_candidates, self.max_bytes = max_candidates, max_bytes
        self.ttl, self.max_queue = float(ttl), max_queue
        self.refresh_interval = max(0., float(refresh_interval))
        self._lock = threading.RLock()
        self._entries: dict[tuple, deque[Candidate]] = {}
        self._first_seen: dict[tuple, float] = {}
        self._last_frame: dict[tuple, tuple] = {}
        self._last_capture: dict[tuple, float] = {}
        self._pending: dict[str, tuple[Candidate, int]] = {}
        self._active: Candidate | None = None
        self._sequence = 0
        self._completed_at: dict[tuple, float] = {}
        self._passage_validity: dict[tuple, threading.Event] = {}
        self._torso_validity: dict[tuple, bool] = {}
        self._first_usable: dict[tuple, float] = {}
        self._first_assessment_started: set[tuple] = set()
        self._first_assessment_completed: set[tuple] = set()

    def reset(self):
        with self._lock:
            for candidate in self._all_candidates():
                candidate.cancelled.set()
                candidate.passage_validity.set()
            for token in self._passage_validity.values():
                token.set()
            self._entries.clear()
            self._first_seen.clear()
            self._last_frame.clear()
            self._last_capture.clear()
            self._pending.clear()
            self._completed_at.clear()
            self._passage_validity.clear()
            self._torso_validity.clear()
            self._first_usable.clear()
            self._first_assessment_started.clear()
            self._first_assessment_completed.clear()
            self._active = None

    def _all_candidates(self):
        seen = set()
        for values in self._entries.values():
            for item in values:
                if item.ticket_id not in seen:
                    seen.add(item.ticket_id)
                    yield item
        if self._active is not None and self._active.ticket_id not in seen:
            yield self._active

    def _unique_bytes(self):
        frames = {}
        for candidate in self._all_candidates():
            frames[id(candidate.task.frame)] = candidate.task.frame.nbytes
        return sum(frames.values())

    def _candidate_count(self):
        return len({item.ticket_id for item in self._all_candidates()})

    def _remove_candidate(self, key, candidate, *, cancel=True):
        if cancel:
            candidate.cancelled.set()
        values = self._entries.get(key)
        if values is not None:
            self._entries[key] = deque(item for item in values if item.ticket_id != candidate.ticket_id)
            if not self._entries[key]:
                self._entries.pop(key, None)
        self._pending.pop(candidate.ticket_id, None)
        # In-flight ownership remains until the worker callback acknowledges it.

    def _drop(self, key):
        for candidate in tuple(self._entries.get(key, ())):
            self._remove_candidate(key, candidate)
        self._entries.pop(key, None)
        self._first_seen.pop(key, None)
        self._last_frame.pop(key, None)
        self._last_capture.pop(key, None)
        self._completed_at.pop(key, None)
        token = self._passage_validity.pop(key, None)
        if token is not None:
            token.set()
        self._first_usable.pop(key, None)
        self._torso_validity.pop(key, None)
        self._first_assessment_started.discard(key)
        self._first_assessment_completed.discard(key)

    def _prune(self, now, generation=None, camera_generation=None):
        if (self._active is not None and generation is not None
                and (self._active.passage_key[1] != generation
                     or self._active.passage_key[2] != camera_generation)):
            self._active.cancelled.set()
        passage_keys = set(self._entries) | set(self._first_seen) | set(self._passage_validity)
        for key in tuple(passage_keys):
            if (generation is not None and (key[1] != generation or key[2] != camera_generation)):
                self._drop(key)
                continue
            had_entries = key in self._entries
            for candidate in tuple(self._entries.get(key, ())):
                if now - candidate.task.context.captured_at > self.ttl:
                    self._remove_candidate(key, candidate)
            if had_entries and not self._entries.get(key):
                self._drop(key)

    def _quality(self, task, observation, observations):
        frame = task.frame
        h, w = frame.shape[:2]
        face = tuple(map(int, observation.box))
        if _area(face) <= 0:
            return None
        visible_face = (max(0, face[0]), max(0, face[1]), min(w, face[2]), min(h, face[3]))
        if _area(visible_face) / _area(face) < .90:
            return None
        fx1, fy1, fx2, fy2 = visible_face
        face_crop = frame[fy1:fy2, fx1:fx2]
        if face_crop.size == 0:
            return None
        body = tuple(map(int, observation.body_box)) if observation.body_box is not None else None
        torso = tuple(map(int, observation.torso_box)) if observation.torso_box is not None else None
        torso_crop = None
        # Face-only evidence is valid for identity. Any missing/uncertain ownership
        # clears torso provenance and cannot make a uniform verdict.
        if body is not None and torso is not None:
            valid, _reason = validate_torso(face, body, torso, frame.shape,
                other_faces=[o.box for o in observations if o.presence_id != observation.presence_id],
                other_bodies=[o.body_box for o in observations if o.presence_id != observation.presence_id
                              and o.body_box is not None])
            overlap_bad = False
            for other in observations:
                if other.presence_id == observation.presence_id or other.torso_box is None:
                    continue
                ob = tuple(map(int, other.torso_box))
                overlap = max(0, min(torso[2], ob[2])-max(torso[0], ob[0])) * max(
                    0, min(torso[3], ob[3])-max(torso[1], ob[1]))
                if overlap > .03 * max(1, _area(torso)):
                    overlap_bad = True
                    break
            visible_torso = (max(0, torso[0]), max(0, torso[1]), min(w, torso[2]), min(h, torso[3]))
            if (valid and not overlap_bad and _area(torso) > 0
                    and _area(visible_torso) / _area(torso) >= .90):
                tx1, ty1, tx2, ty2 = visible_torso
                torso_crop = frame[ty1:ty2, tx1:tx2]
            else:
                body = torso = None
        else:
            body = torso = None
        face_size = _size_quality(face, frame.shape, .012)
        face_sharpness, face_exposure = _sharpness(face_crop), _exposure(face_crop)
        metrics = [("face_size", face_size), ("face_sharpness", face_sharpness),
                   ("face_exposure", face_exposure)]
        if torso_crop is not None and torso_crop.size:
            torso_size = _size_quality(torso, frame.shape, .12)
            torso_sharpness, torso_exposure = _sharpness(torso_crop), _exposure(torso_crop)
            score = (.20 * face_size + .20 * torso_size + .30 * face_sharpness
                     + .10 * face_exposure + .15 * torso_sharpness + .05 * torso_exposure)
            metrics.extend((("torso_size", torso_size), ("torso_sharpness", torso_sharpness),
                            ("torso_exposure", torso_exposure)))
        else:
            score = .55 * face_size + .30 * face_sharpness + .15 * face_exposure
        return score, face, body, torso, tuple(metrics)

    def observe(self, result, *, prioritize=True, select=True):
        """Retain usable original-frame candidates; dispatch is a separate step."""
        task, now = result.task, time.monotonic()
        if not task.valid(now) or not getattr(task, "source_id", ""):
            return
        prefix = (task.source_id, task.context.generation, task.camera_generation)
        with self._lock:
            self._prune(now, task.context.generation, task.camera_generation)
            if result.observations is None:
                keys = set(self._entries) | set(self._first_seen) | set(self._passage_validity)
                for key in tuple(keys):
                    if key[:3] == prefix:
                        self._drop(key)
                return
            live_ids = {o.presence_id for o in result.observations}
            for key in tuple(self._first_seen):
                if key[:3] == prefix and key[3] not in live_ids:
                    self._drop(key)
            for key in tuple(self._entries):
                if key[:3] == prefix and key[3] not in live_ids:
                    self._drop(key)
            # Sanitize every tracked person's geometry before any candidate task
            # receives the frame. The worker assesses all people in that frame.
            quality_by_id = {o.presence_id: self._quality(task, o, result.observations)
                             for o in result.observations}
            from dataclasses import replace
            safe_observations = tuple(
                replace(o, body_index=None, body_box=None, torso_box=None,
                    reason="Torso ownership/visibility is uncertain")
                if quality_by_id[o.presence_id] is None
                   or quality_by_id[o.presence_id][2] is None
                   or quality_by_id[o.presence_id][3] is None
                else o for o in result.observations)
            for observation in result.observations:
                key = prefix + (observation.presence_id,)
                if key not in self._first_seen and len(self._first_seen) >= self.max_tracks:
                    oldest = min(self._first_seen, key=self._first_seen.get)
                    self._drop(oldest)
                first_seen = self._first_seen.setdefault(key, task.context.captured_at)
                frame_key = (task.source_id, task.context.generation,
                             task.camera_generation, task.context.frame_id)
                if self._last_frame.get(key) == frame_key:
                    continue
                self._last_frame[key] = frame_key
                if task.context.captured_at <= self._last_capture.get(key, -math.inf):
                    continue
                self._last_capture[key] = task.context.captured_at
                if len(self._entries) >= self.max_tracks and key not in self._entries:
                    oldest = min(self._entries, key=lambda item: self._first_seen.get(item, math.inf))
                    self._drop(oldest)
                quality = quality_by_id[observation.presence_id]
                torso_is_valid = bool(quality is not None and quality[2] is not None
                                      and quality[3] is not None)
                was_torso_valid = self._torso_validity.get(key, False)
                if was_torso_valid and not torso_is_valid:
                    # A completed display result can no longer claim current
                    # torso provenance after an ambiguous/occluded observation.
                    old_validity = self._passage_validity.get(key)
                    if old_validity is not None:
                        old_validity.set()
                    self._passage_validity[key] = threading.Event()
                self._torso_validity[key] = torso_is_valid
                if quality is None:
                    for old in tuple(self._entries.get(key, ())):
                        self._remove_candidate(key, old)
                    continue
                score, face, body, torso, metrics = quality
                self._first_usable.setdefault(key, task.context.captured_at)
                if body is None or torso is None:
                    # Missing/occluded geometry invalidates old torso evidence even
                    # when the new tracker observation already omits its torso box.
                    for old in tuple(self._entries.get(key, ())):
                        if old.body_box is not None or old.torso_box is not None:
                            self._remove_candidate(key, old, cancel=True)
                frame_observations = safe_observations
                values = self._entries.get(key, deque())
                if values:
                    best = max(values, key=lambda candidate: candidate.score)
                    if select and score < best.score - .08:
                        continue
                    if select and score <= best.score + .02:
                        reason = "confirmation"
                    else:
                        reason = "better"
                else:
                    reason = "confirmation" if key in self._completed_at else "new"
                # Use image dimensions from the task without retaining a second image.
                height, width = task.frame.shape[:2]
                near_exit = (face[0] < .08 * width or face[2] > .92 * width
                             or (body is not None and (body[1] < .04 * height or body[3] > .96 * height)))
                if reason != "new" and near_exit:
                    reason = "near_exit"
                candidate_task = task.with_observations(frame_observations)
                cancel = threading.Event()
                passage_validity = self._passage_validity.setdefault(key, threading.Event())
                candidate = Candidate(uuid.uuid4().hex, candidate_task, observation.presence_id,
                    observation.track_id, face, body, torso, float(score), reason,
                    first_seen, task.context.captured_at, now, metrics, cancel,
                    passage_validity)
                values = self._entries.setdefault(key, deque())
                if len(values) >= self.per_passage:
                    # Replace the least useful unstarted candidate. An active ticket
                    # is never removed from accounting until its worker callback.
                    victim = min(values, key=lambda item: (item.score, item.task.context.captured_at))
                    if (self._active is not None and self._active.ticket_id == victim.ticket_id
                            or candidate.score <= victim.score):
                        continue
                    self._remove_candidate(key, victim)
                    values = self._entries.setdefault(key, deque())
                self._sequence += 1
                values.append(candidate)
                self._pending[candidate.ticket_id] = (candidate, self._sequence)
                self._enforce_bounds()
                while len(self._first_seen) > self.max_tracks:
                    oldest = min(self._first_seen, key=self._first_seen.get)
                    if oldest in self._entries or (self._active is not None and
                                                   self._active.passage_key == oldest):
                        break
                    self._drop(oldest)

    def _enforce_bounds(self):
        while (self._candidate_count() > self.max_candidates
               or self._unique_bytes() > self.max_bytes
               or len(self._pending) > self.max_queue):
            choices = [(key, item) for key, values in self._entries.items()
                       for item in values if self._active is None
                       or item.ticket_id != self._active.ticket_id]
            if not choices:
                break
            now = time.monotonic()
            # Evict the least urgent ticket, retaining aged work and new entrants
            # ahead of redundant confirmation frames when capacity is exhausted.
            key, victim = max(choices, key=lambda pair: (
                max(0, pair[1].priority - int((now-pair[1].queued_at)/.75)),
                -pair[1].score, pair[1].queued_at))
            self._remove_candidate(key, victim)

    def next(self, *, now=None, prioritize=True):
        """Reserve, but do not consume, one ticket for the idle worker."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            if self._active is not None:
                return None
            eligible = [(ticket, data[0], data[1]) for ticket, data in self._pending.items()
                        if data[0].ticket_id == ticket and not data[0].cancelled.is_set()]
            if not eligible:
                return None
            def rank(entry):
                _ticket, candidate, seq = entry
                last = self._completed_at.get(candidate.passage_key)
                if last is not None and now - last < self.refresh_interval:
                    return (2, last + self.refresh_interval, seq)
                aged = max(0, candidate.priority - int((now-candidate.queued_at)/.75)) if prioritize else 0
                return (aged, candidate.queued_at, seq)
            ticket, candidate, _seq = min(eligible, key=rank)
            last = self._completed_at.get(candidate.passage_key)
            if last is not None and now - last < self.refresh_interval:
                return None
            selected_task = candidate.task.for_observation_candidate(
                candidate.task.observations, candidate.presence_id, now,
                candidate.ticket_id, candidate.cancelled, candidate.passage_validity)
            selected = Candidate(candidate.ticket_id, selected_task, candidate.presence_id,
                candidate.track_id, candidate.face_box, candidate.body_box, candidate.torso_box,
                candidate.score, candidate.reason, candidate.first_seen_at,
                candidate.usable_at, candidate.queued_at, candidate.quality_metrics,
                candidate.cancelled, candidate.passage_validity)
            self._active = selected
            passage_key = candidate.passage_key
            if passage_key not in self._first_assessment_started:
                self._first_assessment_started.add(passage_key)
                PERF.observe("passage_entry_to_assessment", max(0., now-candidate.first_seen_at))
                PERF.observe("usable_to_assessment", max(0., now-self._first_usable.get(
                    passage_key, candidate.usable_at)))
            PERF.observe("candidate_selection_wait", max(0., now-candidate.queued_at))
            event("assessment_candidate_dispatched", score=round(candidate.score, 4),
                  reason=candidate.reason, age_ms=round(1000*(now-candidate.task.context.captured_at), 2))
            return selected

    def complete(self, ticket_id, result=None, reason="completed"):
        """Release frame ownership and acknowledge worker success/drop exactly once."""
        with self._lock:
            candidate = self._active
            if candidate is None or candidate.ticket_id != ticket_id:
                return False
            key = candidate.passage_key
            succeeded = reason == "completed" and result is not None
            if succeeded:
                finished = time.monotonic()
                # One successful task assesses all same-frame observations. Do
                # not immediately prioritize a sibling as an unassessed entrant.
                for observation in candidate.task.observations or ():
                    sibling_key = key[:3] + (observation.presence_id,)
                    if sibling_key in self._first_seen:
                        self._completed_at[sibling_key] = finished
                self._completed_at[key] = finished
                PERF.observe("candidate_to_assessment", max(0., finished-(candidate.task.candidate_selected_at or finished)))
                if key not in self._first_assessment_completed:
                    self._first_assessment_completed.add(key)
                    PERF.observe("passage_entry_to_first_assessment", max(0., finished-candidate.first_seen_at))
                    PERF.observe("usable_to_first_assessment", max(0., finished-self._first_usable.get(
                        key, candidate.usable_at)))
            self._active = None
            for values_key, values in tuple(self._entries.items()):
                for item in tuple(values):
                    if item.ticket_id == ticket_id or (item.frame_key == candidate.frame_key and reason == "completed"):
                        self._remove_candidate(values_key, item, cancel=not succeeded)
            self._pending.pop(ticket_id, None)
            event("assessment_candidate_completed", reason=reason)
            return True

    def stats(self):
        with self._lock:
            return {"active_tracks": len(self._first_seen),
                    "candidate_count": self._candidate_count(),
                    "queued": len(self._pending), "in_flight": int(self._active is not None),
                    "retained_bytes": self._unique_bytes(), "max_tracks": self.max_tracks,
                    "per_passage": self.per_passage, "max_candidates": self.max_candidates,
                    "max_bytes": self.max_bytes, "observation_only": True}
