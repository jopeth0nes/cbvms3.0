"""Full-body person detection (YOLOv8n) for deriving torso crops.

Used to obtain a reliable torso region for uniform classification — the MTCNN
face box only covers the face, so a real person box is needed. Lazy-loads the
model on first use. CPU only. No new dependencies (ultralytics already present).
"""

from __future__ import annotations

import cv2
import numpy as np

# Vertical slice of the person box used as the torso (fractions of body height).
_TORSO_TOP_FRAC = 0.20   # skip head/neck
_TORSO_BOT_FRAC = 0.65   # down through the shirt/torso
_MIN_CROP_PX = 32
# A pose torso must be at least this big to be trusted (rejects tiny/garbage detections on
# full-body shots; a real webcam chest crop is far larger). Below this the caller falls back.
_POSE_MIN_PX = 60

# COCO-17 pose keypoint indices used to anchor the torso.
_KP_L_SHOULDER, _KP_R_SHOULDER = 5, 6
_KP_L_HIP, _KP_R_HIP = 11, 12
_KP_CONF_MIN = 0.30

# Bare-skin HSV band for the "can't judge" guard — flags a crop that has drifted onto the
# face/neck instead of the shirt. OpenCV HSV: H 0-179, S/V 0-255. The band is deliberately
# tight around skin hues so a coloured polo is not mistaken for skin.
_SKIN_HSV_LO = (0, 30, 60)
_SKIN_HSV_HI = (25, 170, 255)
_SKIN_FRAC_MAX = 0.55    # crop with > this fraction of skin pixels is "mostly skin" → abstain


def skin_fraction(crop_bgr: np.ndarray) -> float:
    """Fraction (0..1) of a BGR crop that is bare-skin coloured.

    Used by the uniform abstain guard: a torso crop that has slipped onto the face/neck reads
    as predominantly skin. The denominator is the usable body pixels (not black/blown) so a
    dark background can't dilute the ratio. Returns 0.0 when there is too little to judge.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    V = hsv[..., 2]
    body = (V >= 35) & (V <= 250)
    n_body = int(body.sum())
    if n_body < 50:
        return 0.0
    skin = (cv2.inRange(hsv, _SKIN_HSV_LO, _SKIN_HSV_HI) > 0) & body
    return float(int(skin.sum())) / float(n_body)


def clamp_top_below_chin(
    box: list[int] | None,
    chin_y: int | float,
    face_h: int | float,
    frame_shape: tuple[int, ...],
    *,
    margin: float = 0.10,
) -> list[int] | None:
    """Force a torso box's TOP edge to sit below the chin (the hard face floor).

    The top is pushed DOWN only — never up — to at least ``chin_y + margin*face_h`` (a small
    margin below the chin to skip the neck). Because identity face detection is reliable every
    frame, this makes it geometrically impossible for the crop to include the face at any
    distance or pose. Returns a frame-clamped ``[x1,y1,x2,y2]``, or None if the result is
    smaller than ``_MIN_CROP_PX`` or off-frame.
    """
    if box is None:
        return None
    x1, y1, x2, y2 = [int(v) for v in box]
    H, W = int(frame_shape[0]), int(frame_shape[1])
    floor_top = int(chin_y + margin * face_h)
    y1 = max(y1, floor_top)                      # push the top DOWN only
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)
    if (x2 - x1) < _MIN_CROP_PX or (y2 - y1) < _MIN_CROP_PX:
        return None
    return [x1, y1, x2, y2]


class PersonDetector:
    """Detects persons via YOLOv8n (COCO class 0); returns boxes by area desc.

    Also provides a pose-anchored torso crop (YOLOv8-pose shoulders+hips) — far more stable
    than a fixed fraction of the person box, which drifts with pose/arms/sitting.
    """

    _MODEL_PATH = "yolov8n.pt"        # auto-downloads if missing
    _POSE_MODEL_PATH = "yolov8n-pose.pt"  # auto-downloads once (~6 MB)
    # Lowered 0.40 -> 0.25 so a clearly-visible upright entrant isn't dropped frame-to-frame
    # (the live torso box must be present on every frame a person is visible). 0.25 is a
    # standard YOLO confidence floor; IoU/NMS unchanged so duplicate boxes are still merged.
    _CONF = 0.25
    _IOU = 0.50

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False
        self._pose_model = None
        self._pose_load_failed = False

    # ------------------------------------------------------------------
    # Models (lazy)
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

    def _ensure_pose_model(self):
        if self._pose_model is not None:
            return self._pose_model
        if self._pose_load_failed:
            return None
        try:
            from ultralytics import YOLO
            self._pose_model = YOLO(self._POSE_MODEL_PATH)  # ~6 MB, auto-downloads once
            return self._pose_model
        except Exception as exc:
            print(f"[PersonDetector] pose model load failed: {exc}")
            self._pose_load_failed = True
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
    # Pose-anchored torso (YOLOv8-pose shoulders + hips) — stable across pose
    # ------------------------------------------------------------------

    def pose_torso_box(self, frame_bgr: np.ndarray, region: list[int] | None = None) -> list[int] | None:
        """Tight torso/shirt box [x1,y1,x2,y2] in FRAME coords from shoulder+hip keypoints.

        `region` (a person box) restricts pose to that person — used live; pass None to run on
        the whole image (build). Returns None when pose is unreliable so the caller can fall back.
        """
        model = self._ensure_pose_model()
        if model is None or frame_bgr is None or frame_bgr.size == 0:
            return None
        H, W = frame_bgr.shape[:2]
        ox, oy = 0, 0
        img = frame_bgr
        if region is not None:
            rx1, ry1, rx2, ry2 = [int(v) for v in region]
            rx1, ry1 = max(0, rx1), max(0, ry1)
            rx2, ry2 = min(W, rx2), min(H, ry2)
            if (rx2 - rx1) < _MIN_CROP_PX or (ry2 - ry1) < _MIN_CROP_PX:
                return None
            img = frame_bgr[ry1:ry2, rx1:rx2]
            ox, oy = rx1, ry1

        try:
            results = model.predict(img, conf=0.25, verbose=False)
        except Exception as exc:
            print(f"[PersonDetector] pose error: {exc}")
            return None
        if not results:
            return None
        kpts = getattr(results[0], "keypoints", None)
        if kpts is None or kpts.data is None or len(kpts.data) == 0:
            return None
        data = kpts.data.cpu().numpy()  # (n_persons, 17, 3): x, y, conf

        # Pick the person with the strongest shoulder+hip keypoints.
        best, best_score = None, -1.0
        for person in data:
            score = float(person[_KP_L_SHOULDER, 2] + person[_KP_R_SHOULDER, 2]
                          + person[_KP_L_HIP, 2] + person[_KP_R_HIP, 2])
            if score > best_score:
                best_score, best = score, person
        if best is None:
            return None

        ls, rs = best[_KP_L_SHOULDER], best[_KP_R_SHOULDER]
        lh, rh = best[_KP_L_HIP], best[_KP_R_HIP]
        if ls[2] < _KP_CONF_MIN or rs[2] < _KP_CONF_MIN:
            return None  # need both shoulders to anchor the torso

        shoulder_w = abs(float(ls[0]) - float(rs[0])) or 1.0
        xs = [float(ls[0]), float(rs[0])]
        top = min(float(ls[1]), float(rs[1]))
        if lh[2] >= _KP_CONF_MIN and rh[2] >= _KP_CONF_MIN:
            xs += [float(lh[0]), float(rh[0])]
            bottom = max(float(lh[1]), float(rh[1]))
        else:
            bottom = top + 1.5 * shoulder_w  # hips out of frame (seated) → estimate torso height
        x1, x2 = min(xs), max(xs)

        padx = 0.08 * ((x2 - x1) if x2 > x1 else shoulder_w)
        x1 -= padx
        x2 += padx
        top -= 0.10 * shoulder_w  # nudge up for the collar

        x1 = max(0, int(round(x1 + ox)))
        y1 = max(0, int(round(top + oy)))
        x2 = min(W, int(round(x2 + ox)))
        y2 = min(H, int(round(bottom + oy)))
        if (x2 - x1) < _POSE_MIN_PX or (y2 - y1) < _POSE_MIN_PX:
            return None  # too small to be a reliable torso → caller falls back
        return [x1, y1, x2, y2]

    def pose_torso_crop(self, frame_bgr: np.ndarray, region: list[int] | None = None) -> np.ndarray | None:
        box = self.pose_torso_box(frame_bgr, region)
        if box is None:
            return None
        x1, y1, x2, y2 = box
        crop = frame_bgr[y1:y2, x1:x2]
        return crop if crop.size > 0 else None

    # ------------------------------------------------------------------
    # Face-anchored uniform region (robust to framing)
    # ------------------------------------------------------------------

    @staticmethod
    def get_uniform_region(
        face_box: list[int],
        frame_shape: tuple[int, ...],
        person_box: list[int] | None = None,
        *,
        down: float = 3.0,
        top_margin: float = 0.10,
        width_pad: float = 0.5,
    ) -> list[int] | None:
        """Chest/shirt box anchored to the detected face — the torso is directly below it.

        Face-anthropometry: torso width and chest position scale reliably with head size, so
        this tracks the chest at any distance. Unlike a fixed fraction of the person box (which
        lands on the face in a close-up shot where the body is cropped), anchoring to the face
        works for both close-up and full-body framing. Geometry:

          * top    = chin + ``top_margin``*face_h   (margin below the chin, skips the neck)
          * bottom = chin + ``down``*face_h          (~3x face-height down through the shirt)
          * width  = matched person box (shoulders) when available, else ``3x`` the face width
                     centred on the face — i.e. ``cx ± (1+width_pad)*face_w``.

        Returns [x1,y1,x2,y2] or None if too small. This is the guaranteed fallback floor used
        by :meth:`chest_region`.
        """
        fx1, fy1, fx2, fy2 = [int(v) for v in face_box]
        fw, fh = fx2 - fx1, fy2 - fy1
        if fw <= 0 or fh <= 0:
            return None
        H, W = int(frame_shape[0]), int(frame_shape[1])
        cx = (fx1 + fx2) // 2

        top = int(fy2 + top_margin * fh)       # margin below the chin (skip the neck)
        bottom = int(fy2 + down * fh)          # down through the shirt
        if person_box is not None:
            px1, py1, px2, py2 = [int(v) for v in person_box]
            left, right = px1, px2
            bottom = min(bottom, py2)           # never past the body
        else:
            half = int(fw * (1.0 + width_pad))  # total width = 3x face width centred on cx
            left, right = cx - half, cx + half

        left, top = max(0, left), max(0, top)
        right, bottom = min(W, right), min(H, bottom)
        if (right - left) < _MIN_CROP_PX or (bottom - top) < _MIN_CROP_PX:
            return None
        return [left, top, right, bottom]

    # ------------------------------------------------------------------
    # Final chest region with a HARD face floor (single source of truth)
    # ------------------------------------------------------------------

    def chest_region(
        self,
        frame_bgr: np.ndarray,
        face_box: list[int],
        person_box: list[int] | None = None,
    ) -> tuple[list[int] | None, str]:
        """Final torso/chest region for uniform classification, clamped below the chin.

        Identity face detection is reliable every frame, so the recognised face box anchors a
        hard geometric floor: every candidate region has its TOP pushed below the chin
        (:func:`clamp_top_below_chin`) before it can be used — the crop can never include the
        face. Order of preference, all clamped:

          (a)/(b) pose shoulders(+hips) box  [:meth:`pose_torso_box`, needs a person box;
                  prefers the shoulder+hip quad, falls internally to a shoulder-width extent
                  when the hips are off-frame] →
          (c) face-anthropometry chest box   [:meth:`get_uniform_region`, works with no
                  person box — the guaranteed floor].

        Returns ``(region|None, method)`` with method in ``{'pose', 'face', 'none'}``. Pose
        failing or clamping to nothing simply falls through to (c), which is almost always
        available, so the box no longer drops in/out frame-to-frame.
        """
        if frame_bgr is None or frame_bgr.size == 0 or face_box is None:
            return None, "none"
        fx1, fy1, fx2, fy2 = [int(v) for v in face_box]
        chin_y, face_h = fy2, (fy2 - fy1)
        if face_h <= 0:
            return None, "none"

        # (a)/(b) pose — only when we can restrict pose to this person's box.
        if person_box is not None:
            pose = self.pose_torso_box(frame_bgr, person_box)
            clamped = clamp_top_below_chin(pose, chin_y, face_h, frame_bgr.shape)
            if clamped is not None:
                return clamped, "pose"

        # (c) face-derived chest box — guaranteed floor; works with or without a person box.
        face_reg = self.get_uniform_region(face_box, frame_bgr.shape, person_box)
        clamped = clamp_top_below_chin(face_reg, chin_y, face_h, frame_bgr.shape)
        if clamped is not None:
            return clamped, "face"
        return None, "none"
