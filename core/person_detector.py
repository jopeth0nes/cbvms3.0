"""Full-body person detection (YOLOv8n) for deriving torso crops.

Used to obtain a reliable torso region for uniform classification — the MTCNN
face box only covers the face, so a real person box is needed. Lazy-loads the
model on first use. CPU only. No new dependencies (ultralytics already present).
"""

from __future__ import annotations

import numpy as np

# Vertical slice of the person box used as the torso (fractions of body height).
_TORSO_TOP_FRAC = 0.20   # skip head/neck
_TORSO_BOT_FRAC = 0.65   # down through the shirt/torso
_MIN_CROP_PX = 32


class PersonDetector:
    """Detects persons via YOLOv8n (COCO class 0); returns boxes by area desc."""

    _MODEL_PATH = "yolov8n.pt"   # auto-downloads if missing
    _CONF = 0.40
    _IOU = 0.50

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False

    # ------------------------------------------------------------------
    # Model (lazy)
    # ------------------------------------------------------------------

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None
        try:
            from ultralytics import YOLO
            self._model = YOLO(self._MODEL_PATH)  # ~6 MB, auto-downloads once
            return self._model
        except Exception as exc:
            print(f"[PersonDetector] model load failed: {exc}")
            self._load_failed = True
            return None

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_persons(self, frame_bgr: np.ndarray) -> list[list[int]]:
        """Return person bounding boxes [x1,y1,x2,y2], largest area first."""
        model = self._ensure_model()
        if model is None or frame_bgr is None or frame_bgr.size == 0:
            return []
        try:
            # Pass BGR directly — ultralytics expects BGR numpy frames.
            results = model.predict(
                frame_bgr, conf=self._CONF, iou=self._IOU, classes=[0], verbose=False
            )
            boxes: list[list[int]] = []
            if results:
                for b in results[0].boxes:
                    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                    boxes.append([x1, y1, x2, y2])
            boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
            return boxes
        except Exception as exc:
            print(f"[PersonDetector] detect error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Torso geometry
    # ------------------------------------------------------------------

    @staticmethod
    def get_torso_box(
        person_box: list[int],
        *,
        top_frac: float = _TORSO_TOP_FRAC,
        bot_frac: float = _TORSO_BOT_FRAC,
    ) -> list[int]:
        """Torso bounding box in full-frame coords (for drawing). Not clamped."""
        x1, y1, x2, y2 = person_box
        ph = y2 - y1
        ty1 = int(y1 + ph * top_frac)
        ty2 = int(y1 + ph * bot_frac)
        return [int(x1), ty1, int(x2), ty2]

    def get_torso_crop(
        self,
        frame_bgr: np.ndarray,
        person_box: list[int],
        *,
        top_frac: float = _TORSO_TOP_FRAC,
        bot_frac: float = _TORSO_BOT_FRAC,
    ) -> np.ndarray | None:
        """Crop the torso region. Returns None if smaller than 32x32 px."""
        if frame_bgr is None or frame_bgr.size == 0:
            return None
        h, w = frame_bgr.shape[:2]
        tx1, ty1, tx2, ty2 = self.get_torso_box(person_box, top_frac=top_frac, bot_frac=bot_frac)
        tx1, ty1 = max(0, tx1), max(0, ty1)
        tx2, ty2 = min(w, tx2), min(h, ty2)
        if (tx2 - tx1) < _MIN_CROP_PX or (ty2 - ty1) < _MIN_CROP_PX:
            return None
        return frame_bgr[ty1:ty2, tx1:tx2]

    # ------------------------------------------------------------------
    # Face-anchored uniform region (robust to framing)
    # ------------------------------------------------------------------

    @staticmethod
    def get_uniform_region(
        face_box: list[int],
        frame_shape: tuple[int, ...],
        person_box: list[int] | None = None,
        *,
        down: float = 2.6,
        width_pad: float = 0.6,
    ) -> list[int] | None:
        """Chest/shirt box anchored to the detected face — the torso is directly below it.

        Unlike a fixed fraction of the person box (which lands on the face in a close-up
        shot where the body is cropped), anchoring to the face works for both close-up
        and full-body framing. Width comes from the matched person box (shoulders) when
        available, else from the face width. Returns [x1,y1,x2,y2] or None if too small.
        """
        fx1, fy1, fx2, fy2 = [int(v) for v in face_box]
        fw, fh = fx2 - fx1, fy2 - fy1
        if fw <= 0 or fh <= 0:
            return None
        H, W = int(frame_shape[0]), int(frame_shape[1])
        cx = (fx1 + fx2) // 2

        top = fy2                              # start at the chin
        bottom = int(fy2 + down * fh)          # down through the shirt
        if person_box is not None:
            px1, py1, px2, py2 = [int(v) for v in person_box]
            left, right = px1, px2
            bottom = min(bottom, py2)           # never past the body
        else:
            half = int(fw * (1.0 + width_pad))
            left, right = cx - half, cx + half

        left, top = max(0, left), max(0, top)
        right, bottom = min(W, right), min(H, bottom)
        if (right - left) < _MIN_CROP_PX or (bottom - top) < _MIN_CROP_PX:
            return None
        return [left, top, right, bottom]
