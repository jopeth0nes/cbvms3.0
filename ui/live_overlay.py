"""Source-coordinate annotations using the same assessment rows as Live Alerts."""

from __future__ import annotations

import math
import unicodedata
from typing import Iterable, Mapping

import cv2
import numpy as np

from ui.live_alerts import assessment_style


def clipped_box(box, width: int, height: int, mirror: bool = False):
    """Clip source coordinates, applying the same optional mirror as the pixels."""
    try:
        values = tuple(float(value) for value in box)
        if len(values) != 4 or width < 1 or height < 1 or not all(map(math.isfinite, values)):
            return None
        x1, y1, x2, y2 = (int(round(value)) for value in values)
    except (TypeError, ValueError, OverflowError):
        return None
    if mirror:
        x1, x2 = width - x2, width - x1
    x1, x2 = max(0, min(width - 1, x1)), max(0, min(width - 1, x2))
    y1, y2 = max(0, min(height - 1, y1)), max(0, min(height - 1, y2))
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def _intersection(a, b) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def label_rect(anchor, label_size, frame_size, occupied=(), face_boxes=()):
    """Choose an in-frame label location with minimum overlap and then distance.

    Crowded scenes cannot always avoid every overlap. The bounded candidate search
    keeps the nearest available rows before falling back to minimum overlap.
    """
    width, height = frame_size
    label_width, label_height = min(width, label_size[0]), min(height, label_size[1])
    x1, y1, x2, y2 = anchor
    candidates = []
    for x in (x1, x2 - label_width, (x1 + x2 - label_width) // 2):
        for y in (y1 - label_height - 3, y2 + 3, y1, y2 - label_height):
            candidates.append((x, y))
    for step in range(1, 5):
        candidates.extend(((x1, y1 - step * (label_height + 3)),
                           (x1, y2 + step * (label_height + 3))))
    rects = [(max(0, min(width - label_width, x)), max(0, min(height - label_height, y)))
             for x, y in candidates]
    rects = [(x, y, x + label_width, y + label_height) for x, y in rects]

    def cost(rect):
        overlap = sum(_intersection(rect, other) for other in occupied)
        faces = sum(_intersection(rect, other) for other in face_boxes)
        distance = abs(rect[0] - x1) + abs(rect[3] - y1)
        return overlap * 4 + faces, distance

    return min(rects, key=cost)


def _ascii(text: str) -> str:
    # OpenCV's Hershey font does not support Unicode. Cards retain the full name.
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").strip()


def _lines(texts, max_width: int, scale: float) -> list[str]:
    result = []
    for text in texts:
        current = ""
        for word in text.split():
            while word and cv2.getTextSize(word, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] > max_width:
                word = word[:-1]
            candidate = f"{current} {word}".strip()
            if current and cv2.getTextSize(candidate, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] > max_width:
                result.append(current)
                current = word
            else:
                current = candidate
        if current:
            result.append(current)
    return result or [""]


def draw_assessments(frame: np.ndarray, rows: Iterable[Mapping], mirror: bool = False) -> np.ndarray:
    """Draw immutable snapshots. Never modifies source pixels or inference boxes."""
    out = cv2.flip(frame, 1) if mirror else frame.copy()
    height, width = out.shape[:2]
    if min(width, height) < 16:
        return out
    prepared = []
    for row in rows:
        face = clipped_box(row.get("face_box", row.get("bbox")), width, height, mirror)
        if face is None:
            continue
        torso = clipped_box(row.get("torso_box"), width, height, mirror)
        state, color_hex = assessment_style(row)
        color = tuple(int(color_hex[index:index + 2], 16) for index in (5, 3, 1))
        track = str(row.get("track_id", row.get("presence_id", "?")))
        tag = f"T{track}"
        name = _ascii(row.get("name") or "Unidentified person")
        if not name:
            name = f"Student {row.get('student_id') or tag}"
        categories = row.get("accepted_categories") or ()
        if isinstance(categories, str):
            categories = (categories,)
        texts = [f"[{tag}] {name}", state]
        if categories:
            texts.append(", ".join(_ascii(category).replace("_", " ") for category in categories))
        if row.get("suspension_tag"):
            texts.append("Active suspension")
        prepared.append((face, torso, color, tag, texts, state))
        cv2.rectangle(out, face[:2], face[2:], color, 2)
        if torso is not None:
            cv2.rectangle(out, torso[:2], torso[2:], color, 2)
            cv2.line(out, ((face[0] + face[2]) // 2, face[3]),
                     ((torso[0] + torso[2]) // 2, torso[1]), color, 1, cv2.LINE_AA)

    occupied = []
    face_boxes = [item[0] for item in prepared]
    scale, line_height, padding = .45, 18, 5

    def draw_label(anchor, texts, color):
        lines = _lines(texts, max(1, min(330, width - padding * 2)), scale)
        max_lines = max(1, (height - 2 * padding) // line_height)
        lines = lines[:max_lines]
        label_width = min(width, max(cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]
                                    for text in lines) + 2 * padding)
        label_height = min(height, len(lines) * line_height + padding)
        rect = label_rect(anchor, (label_width, label_height), (width, height), occupied, face_boxes)
        occupied.append(rect)
        cv2.rectangle(out, rect[:2], (rect[2] - 1, rect[3] - 1), color, -1)
        # A leader connects displaced labels to their owner, even in a crowded scene.
        cv2.line(out, ((rect[0] + rect[2]) // 2, rect[3] - 1),
                 ((anchor[0] + anchor[2]) // 2, anchor[1]), color, 1, cv2.LINE_AA)
        for index, text in enumerate(lines):
            baseline = min(height - 2, rect[1] + (index + 1) * line_height)
            cv2.putText(out, text, (rect[0] + padding, baseline), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, (255, 255, 255), 1, cv2.LINE_AA)

    # Face identity/state has priority; torso tags share the same track ID and color.
    for face, torso, color, tag, texts, state in prepared:
        draw_label(face, texts, color)
    for face, torso, color, tag, texts, state in prepared:
        if torso is not None:
            draw_label(torso, [f"[{tag}] {state}"], color)
    return out
