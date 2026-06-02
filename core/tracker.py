"""Lightweight multi-face tracker (SORT-style) for the live overlay.

The recognizer is split into a fast detection pass (~52ms, run often) and a slow
recognition pass (~520ms, run occasionally). This tracker glues them together:

  * update(boxes)        — associate each detection to a persistent track by IoU,
                           so a square stays locked to one physical face and two
                           separated people can never collapse onto a single box.
  * assign_identities()  — bind recognizer identities to the tracks they overlap,
                           so a name follows its face across frames instead of being
                           re-guessed every frame.

Pure numpy, no extra dependencies. All mutation happens on the UI thread (the
background workers post results via after(0)), so no locking is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _iou(a: list[float], b: list[float]) -> float:
    """Intersection-over-union of two [x1, y1, x2, y2] boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


@dataclass
class Track:
    """One tracked face: a stable box + the identity bound to it."""

    id: int
    box: list[float]                       # [x1, y1, x2, y2], smoothed
    hits: int = 1                          # number of detection updates matched
    time_since_update: int = 0            # detection cycles since last match

    # Identity (set by assign_identities; pending until then)
    identified: bool = False
    name: str = ""
    student_id: str = ""
    gender: str = "—"
    matched: bool = False

    # Violation overlay (set on the recognition path)
    violation: str | None = None
    uniform_label: str | None = None
    uniform_conf: float = 0.0
    torso_box: list[int] | None = None

    def box_int(self) -> list[int]:
        return [int(round(v)) for v in self.box]


class FaceTracker:
    """Associates fast detections into persistent identity-bound tracks via IoU."""

    def __init__(
        self,
        *,
        iou_threshold: float = 0.3,
        max_age: int = 15,        # detection cycles a track survives unmatched (~1s @15fps)
        min_hits: int = 2,        # detections before a track renders (anti-flicker)
        render_coast: int = 3,    # cycles a confirmed track keeps rendering while unmatched
        smooth: float = 0.6,      # box smoothing toward the new detection (0..1)
    ) -> None:
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.min_hits = min_hits
        self.render_coast = render_coast
        self.smooth = smooth
        self._tracks: list[Track] = []
        self._next_id = 1

    # ------------------------------------------------------------------
    # Detection association (fast path)
    # ------------------------------------------------------------------

    def update(self, det_boxes: list[list[float]]) -> list[Track]:
        """Match detections to tracks by IoU; spawn/age/drop tracks. Returns renderable tracks."""
        matches, unmatched_dets = self._associate(det_boxes, self._tracks)

        for ti, di in matches:
            tr = self._tracks[ti]
            self._smooth_box(tr, det_boxes[di])
            tr.hits += 1
            tr.time_since_update = 0

        matched_tracks = {ti for ti, _ in matches}
        for ti, tr in enumerate(self._tracks):
            if ti not in matched_tracks:
                tr.time_since_update += 1

        for di in unmatched_dets:
            self._tracks.append(Track(id=self._next_id, box=[float(v) for v in det_boxes[di]]))
            self._next_id += 1

        # Drop dead tracks
        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]

        return self.renderable()

    def _smooth_box(self, tr: Track, det_box: list[float]) -> None:
        a = self.smooth
        tr.box = [a * float(d) + (1.0 - a) * float(c) for d, c in zip(det_box, tr.box)]

    def _associate(self, det_boxes, tracks):
        """Greedy IoU association above the gate. Returns (matches, unmatched_det_indices)."""
        if not tracks or not det_boxes:
            return [], list(range(len(det_boxes)))

        iou = np.zeros((len(tracks), len(det_boxes)), dtype=np.float32)
        for ti, tr in enumerate(tracks):
            for di, db in enumerate(det_boxes):
                iou[ti, di] = _iou(tr.box, db)

        matches: list[tuple[int, int]] = []
        used_tracks: set[int] = set()
        used_dets: set[int] = set()
        # Repeatedly take the globally-best remaining pair above the gate.
        order = np.dstack(np.unravel_index(np.argsort(iou, axis=None)[::-1], iou.shape))[0]
        for ti, di in order:
            ti, di = int(ti), int(di)
            if iou[ti, di] < self.iou_threshold:
                break
            if ti in used_tracks or di in used_dets:
                continue
            matches.append((ti, di))
            used_tracks.add(ti)
            used_dets.add(di)

        unmatched_dets = [di for di in range(len(det_boxes)) if di not in used_dets]
        return matches, unmatched_dets

    # ------------------------------------------------------------------
    # Identity binding (slow path)
    # ------------------------------------------------------------------

    def assign_identities(self, recog_results: list[dict]) -> None:
        """IoU-match recognizer results to existing tracks and copy identity onto them.

        Each track receives at most one identity and each result is consumed once
        (greedy by IoU), reinforcing the recognizer's own one-identity-per-face rule.
        """
        if not self._tracks or not recog_results:
            return
        res_boxes = [[float(v) for v in r["box"]] for r in recog_results]
        # tracks as rows, recognition boxes as columns → matches are (track_idx, result_idx).
        matches, _ = self._associate(res_boxes, self._tracks)
        for ti, ri in matches:
            tr = self._tracks[ti]
            r = recog_results[ri]
            tr.identified = True
            tr.name = r.get("name", "Unknown")
            tr.student_id = r.get("student_id", "")
            tr.gender = r.get("gender", "—") or "—"
            tr.matched = bool(r.get("matched", False))
            tr.violation = r.get("violation")
            tr.uniform_label = r.get("uniform_label")
            tr.uniform_conf = float(r.get("uniform_conf", 0.0) or 0.0)
            tr.torso_box = r.get("torso_box")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def renderable(self) -> list[Track]:
        """Confirmed tracks that are currently visible (recently matched)."""
        return [
            t for t in self._tracks
            if t.hits >= self.min_hits and t.time_since_update <= self.render_coast
        ]

    def has_unidentified(self) -> bool:
        """True if any renderable track still needs an identity → trigger re-ID."""
        return any(not t.identified for t in self.renderable())

    def reset(self) -> None:
        self._tracks = []
