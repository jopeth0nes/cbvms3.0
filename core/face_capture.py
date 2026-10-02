"""Guide-based enrollment selection and immutable, frame-bound capture state.

All geometry is in raw camera coordinates. Render the guide and selected box on
that same frame before resizing/mirroring; display transforms never affect crops.
"""
from dataclasses import dataclass
import pickle
from core.diagnostics import event

import cv2
import numpy as np

POSITION_MESSAGE = "Position your face inside the guide."
AMBIGUOUS_MESSAGE = "Only one person should be inside the guide."
MAX_FRAME_AGE = 1.5
CAPTURE_TIMEOUT = 4.0
STALE_MESSAGE = "Camera frame is stale. Check the camera connection."


def guide_geometry(shape, step=0):
    h, w = shape[:2]
    shift = .09 if step == 1 else -.09 if step == 2 else 0
    return w * (.5 + shift), h * .5, w * .19, h * .33


def select_target(detections, shape, step=0):
    """Reject competing boxes intersecting the visible oval; require center inside.

    Scores/order/area do not rank candidates. Invalid or clipped boxes fail closed.
    """
    cx, cy, rx, ry = guide_geometry(shape, step)
    h, w = shape[:2]
    overlapping = []
    for detection in detections:
        box = np.asarray(detection[0], dtype=float)
        if box.shape != (4,) or not np.isfinite(box).all():
            continue
        x1, y1, x2, y2 = box
        if x2 <= x1 or y2 <= y1:
            continue
        nearest_x, nearest_y = np.clip(cx, x1, x2), np.clip(cy, y1, y2)
        if ((nearest_x-cx)/rx)**2 + ((nearest_y-cy)/ry)**2 <= 1:
            overlapping.append(detection)
    if len(overlapping) > 1:
        return None, AMBIGUOUS_MESSAGE
    if not overlapping:
        return None, POSITION_MESSAGE
    face = overlapping[0]
    x1, y1, x2, y2 = face[0]
    inside = (((x1+x2)/2-cx)/rx)**2 + (((y1+y2)/2-cy)/ry)**2 <= 1
    if not inside or x1 < 0 or y1 < 0 or x2 > w or y2 > h:
        return None, ("Move slightly farther away — keep your whole face in view."
                      if x1 < 0 or y1 < 0 or x2 > w or y2 > h else POSITION_MESSAGE)
    return face, "Face selected — hold still"


def same_target(previous, current):
    a, b = np.asarray(previous[0]), np.asarray(current[0])
    intersection = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2])).prod()
    union = (a[2:]-a[:2]).prod() + (b[2:]-b[:2]).prod() - intersection
    ea, eb = np.asarray(previous[1]), np.asarray(current[1])
    norms = np.linalg.norm(ea) * np.linalg.norm(eb)
    return union > 0 and intersection/union >= .3 and norms > 0 and float(ea @ eb / norms) >= .55


@dataclass(frozen=True)
class FaceCapture:
    student_key: tuple
    frame_id: tuple
    captured_at: float
    box: tuple
    frame: np.ndarray
    embedding: np.ndarray
    photo: bytes

    @classmethod
    def freeze(cls, student_key, sample, face):
        frame = sample.frame.copy()
        box = tuple(int(v) for v in face[0])
        x1, y1, x2, y2 = box
        crop = frame[y1:y2, x1:x2]
        embedding = np.array(face[1], dtype=np.float32, copy=True)
        norm = np.linalg.norm(embedding)
        if not crop.size or not np.isfinite(embedding).all() or norm <= 0:
            raise ValueError("Invalid face capture. Please retake.")
        embedding /= norm
        ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError("Could not encode face photo. Please retake.")
        frame.setflags(write=False)
        embedding.setflags(write=False)
        return cls(student_key, sample.frame_id, sample.captured_at, box, frame, embedding, encoded.tobytes())


@dataclass(frozen=True)
class CaptureValidation:
    owner: object
    sample: object
    faces: object
    error: str | None
    request_id: int
    duration: float

    def __iter__(self):
        # Keep the worker interface usable by existing capture consumers.
        return iter((self.owner, self.sample, self.faces, self.error))


class CaptureSession:
    def __init__(self, student_key):
        self.student_key = student_key
        self.previous = None
        self.last_sample = None
        self.ready = False
        self.pending = False
        self.frozen = None
        self.message = POSITION_MESSAGE
        self.phase = 'searching'
        self.anchor = None
        self.requested_at = None
        self.request_id = 0

    def invalidate(self, message=POSITION_MESSAGE):
        if self.pending:
            event('enrollment_capture_rejected',request_id=self.request_id,reason=message,
                  frame_id=self.last_sample.frame_id if self.last_sample else None)
            self.request_id += 1  # A cancelled native worker can never complete another request.
        self.previous = None
        self.ready = self.pending = False
        self.message = message
        self.phase = 'recovering'

    def expire(self, now):
        if self.pending and now - self.requested_at >= CAPTURE_TIMEOUT:
            self.invalidate('Capture timed out. Wait for Ready and try again; check camera/model availability.')
            return True
        return False

    def observe(self, sample, detections, step, now):
        if self.frozen is not None or self.expire(now):
            return None
        if sample is None or not 0 <= now-sample.captured_at <= MAX_FRAME_AGE:
            self.invalidate(STALE_MESSAGE)
            return None
        if self.last_sample and sample.frame_id[0] == self.last_sample.frame_id[0]:
            if (sample.frame_id == self.last_sample.frame_id or
                    sample.captured_at <= self.last_sample.captured_at):
                return None  # Duplicate/out-of-order results never count as observations.
        if self.pending and sample.captured_at <= self.requested_at:
            return None  # Validation must be from an exposure AFTER this request.
        continuity = (self.last_sample is not None and
                      sample.frame_id[0] == self.last_sample.frame_id[0] and
                      0 < sample.captured_at-self.last_sample.captured_at <= MAX_FRAME_AGE)
        self.last_sample = sample
        face, message = select_target(detections, sample.frame.shape, step)
        if face is None:
            self.invalidate(message)
            return None
        embedding = np.asarray(face[1], dtype=np.float32)
        norm = np.linalg.norm(embedding)
        if embedding.ndim != 1 or not np.isfinite(embedding).all() or norm <= 0:
            self.invalidate('Face embedding unavailable. Please try again.')
            return None
        if self.anchor is not None and (self.anchor.shape != embedding.shape or float(self.anchor @ (embedding/norm)) < .55):
            self.invalidate('Target changed. Return to the same person or restart capture.')
            return None
        stable = continuity and self.previous is not None and same_target(self.previous, face)
        if self.pending and not stable:
            self.invalidate('Target changed or validation gap too long. Hold still and capture again.')
            return None
        self.ready = stable
        self.previous = face
        self.phase = 'ready' if stable else 'validating_target'
        self.message = 'Ready — face validated. Capture this angle.' if stable else 'Hold still while the target is verified.'
        if stable and self.anchor is None:
            self.anchor = np.array(embedding/norm, copy=True)
        if self.pending:
            try:
                self.frozen = FaceCapture.freeze(self.student_key, sample, face)
            except (ValueError, cv2.error) as exc:
                self.invalidate(str(exc))
                return None
            self.pending = False
            self.phase = 'frozen_review'
            event('enrollment_capture_frozen', frame_id=sample.frame_id,
                  frame_age=round(now-sample.captured_at,3), request_id=self.request_id,
                  latency=round(now-self.requested_at,3))
        return face

    def request(self, now):
        if self.pending or self.frozen is not None:
            return False
        if not self.ready or self.last_sample is None or not 0 <= now-self.last_sample.captured_at <= MAX_FRAME_AGE:
            self.invalidate(STALE_MESSAGE)
            return False
        self.pending = True
        self.requested_at = now
        self.request_id += 1
        self.phase = 'validating_capture'
        self.message = 'Validating capture… Hold still.'
        event('enrollment_capture_requested',request_id=self.request_id,frame_id=self.last_sample.frame_id)
        return True


def capture_payload(captures, student_key):
    """Only frozen captures enter persistence. Never run inference at save time."""
    if not captures or any(c.student_key != student_key for c in captures):
        raise ValueError("Student changed. Retake the captures for the selected student.")
    if any(float(captures[0].embedding @ c.embedding) < .55 for c in captures[1:]):
        raise ValueError('Captures belong to different targets. Retake with the same person.')
    # Keep each confirmed angle's original embedding (no unrelated frame averaging).
    return pickle.dumps([c.embedding for c in captures]), captures[0].photo


class FacePreviewTracker:
    """Cheap optical-flow overlay between validations; never generates saved data.

    Track from the exact analyzed image into current preview pixels instead of
    painting an old box on a newer face. Optical flow is optional: sparse features do not invalidate face detections.
    Loss of an established track is a separate signal requiring capture revalidation.
    """
    def __init__(self):
        self.clear()

    def clear(self):
        self.sample = self.gray = self.points = self.box = None
        self.failure_reason = "no_target"

    def fail(self, reason):
        self.clear()
        self.failure_reason = reason

    @staticmethod
    def _gray(frame):
        return cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY)

    def reset(self, sample, box):
        self.clear()
        gray = self._gray(sample.frame)
        h, w = sample.frame.shape[:2]
        scale = np.array([320/w, 240/h, 320/w, 240/h])
        scaled = np.asarray(box, dtype=float) * scale
        x1, y1, x2, y2 = scaled.astype(int)
        mask = np.zeros(gray.shape, np.uint8)
        mask[max(0,y1):min(240,y2), max(0,x1):min(320,x2)] = 255
        points = cv2.goodFeaturesToTrack(gray, 40, .02, 5, mask=mask)
        if points is None or len(points) < 6:
            self.failure_reason = "insufficient_points"
            return
        self.sample, self.gray, self.points, self.box = sample, gray, points, scaled
        self.failure_reason = ""

    def advance(self, sample, step=0):
        if self.sample is None:
            return None
        if sample.frame_id != self.sample.frame_id:
            if (sample.frame_id[0] != self.sample.frame_id[0] or
                    not 0 < sample.captured_at-self.sample.captured_at <= MAX_FRAME_AGE or
                    sample.frame.shape != self.sample.frame.shape):
                self.fail("source_or_time_changed")
                return None
            gray = self._gray(sample.frame)
            next_pts, ok, _ = cv2.calcOpticalFlowPyrLK(self.gray, gray, self.points, None)
            if next_pts is None:
                self.fail("flow_lost")
                return None
            back, back_ok, _ = cv2.calcOpticalFlowPyrLK(gray, self.gray, next_pts, None)
            if back is None:
                self.fail("flow_lost")
                return None
            good = ((ok.ravel() == 1) & (back_ok.ravel() == 1) &
                    (np.linalg.norm(self.points-back, axis=2).ravel() < 1.5))
            if good.sum() < 6 or good.mean() < .6:
                self.fail("flow_lost")
                return None
            old, new = self.points[good], next_pts[good]
            movement = (new-old).reshape(-1, 2)
            delta = np.median(movement, axis=0)
            coherent = np.linalg.norm(movement-delta, axis=1) < 3
            if coherent.sum() < 6 or coherent.mean() < .7:
                self.fail("flow_lost")
                return None
            self.box += np.tile(delta, 2)
            self.sample, self.gray, self.points = sample, gray, new[coherent]
        h, w = sample.frame.shape[:2]
        box = self.box * np.array([w/320, h/240, w/320, h/240])
        if select_target([(box, None)], sample.frame.shape, step)[0] is None:
            self.fail("outside_guide")
            return None
        # Replenish corners on the current image before gradual attrition empties the track.
        if len(self.points) < 20:
            mask = np.zeros((240,320), np.uint8)
            x1,y1,x2,y2 = self.box.astype(int)
            mask[max(0,y1):min(240,y2),max(0,x1):min(320,x2)] = 255
            points = cv2.goodFeaturesToTrack(self.gray,40,.02,5,mask=mask)
            if points is not None and len(points) > len(self.points):
                self.points = points
        return box
