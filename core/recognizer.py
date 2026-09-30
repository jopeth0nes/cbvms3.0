"""Face recognition using InsightFace buffalo_l (SCRFD detection + ArcFace embedding).

ArcFace (w600k_r50) embeddings are far more discriminative than the previous
facenet/VGGFace2 model: genuine pairs land at cosine distance ~0.3-0.5 while
different people (unknowns) land at ~0.85-1.0. That clean gap is what makes
unknown-rejection reliable — an unregistered face stays well outside the match
threshold instead of leaking onto the nearest enrolled identity.

Two failure modes are addressed here:
  1. Unknown-leakage  -> discriminative ArcFace embeddings + strict threshold.
  2. Competing labels -> best-match-only assignment with ambiguity rejection.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from core.diagnostics import event
import pickle
import threading
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from database.db_manager import CBVMSDatabase

# Cosine distance threshold: lower = stricter matching. This absolute gate is the
# unknown-rejection lever — buffalo_l genuine pairs sit <= ~0.5, impostors >= ~0.85.
MATCH_THRESHOLD = 0.50

# A merely acceptable second choice is not identity evidence. Require a gap both
# between a face's two closest students and between two faces claiming one student.
IDENTITY_MARGIN = 0.05

# Drop weak SCRFD detections (real faces score well above this).
DET_SCORE_MIN = 0.5

# Input size for the fast detection-only pass (detect_faces). Smaller than the
# 640x640 recognition pass → ~52ms vs ~196ms, enough headroom for ~15 FPS tracking.
DET_FAST_SIZE = (320, 320)


class FaceRecognizer:
    """Detects and identifies faces using InsightFace buffalo_l (SCRFD + ArcFace)."""

    def __init__(self, db: "CBVMSDatabase") -> None:
        self._db = db
        self._lock = threading.Lock()
        # InsightFace's SCRFD model is shared by detection, recognition and
        # enrollment. Serialize inference separately from model/gallery loading.
        self._model_lock = threading.Lock()
        self._detector_lock = threading.RLock()
        self._recognition_lock = threading.RLock()
        self._detector = None
        self._recognition = None
        self.detector_error = None
        self.model_dir = Path(os.environ.get("CBVMS_FACE_MODEL_DIR", "~/.insightface/models/buffalo_l")).expanduser().resolve()
        self._app = None             # lazy — avoid slow model load at startup
        self._frontal_cascade = None  # lightweight detectors for the has_face() UI hint
        self._profile_cascade = None
        # (student_row, embeddings) where embeddings is a unit-normalized (K, 512) array
        # holding that student's per-angle ArcFace embeddings.
        self._known: list[tuple[dict, np.ndarray]] = []
        self._models_loaded = False
        self.last_error = None
        self.threshold: float = MATCH_THRESHOLD  # runtime-adjustable match sensitivity
        self.identity_margin: float = IDENTITY_MARGIN
        self.load_known_faces()

    @property
    def sensitivity_label(self) -> str:
        # Bands tuned for ArcFace's cleaner genuine/impostor separation.
        if self.threshold <= 0.40:
            return "Very Strict"
        elif self.threshold <= 0.50:
            return "Strict"
        elif self.threshold <= 0.60:
            return "Balanced"
        elif self.threshold <= 0.70:
            return "Lenient"
        else:
            return "Very Lenient"

    # ------------------------------------------------------------------
    # Model loading (lazy)
    # ------------------------------------------------------------------

    def _local_model(self, filename, task):
        path = self.model_dir / filename
        event("model_asset", component=task, path=str(path), provider="CPUExecutionProvider")
        if not path.is_file() or path.stat().st_size < 1024:
            raise RuntimeError(f"Missing or incomplete {task} weights: {path}. Install the buffalo_l asset and retry.")
        # Load only the requested file. FaceAnalysis opens every ONNX before filtering.
        os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
        import onnxruntime
        options = onnxruntime.SessionOptions()
        # Face tracking, recognition and YOLO share the CPU. Default per-session
        # spinning pools can starve body inference until every result has expired.
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        options.log_severity_level = 3  # fixed output metadata warns at the 320 input size
        # InsightFace 0.7.3's public get_model silently drops session options;
        # its router forwards them to the actual ONNX session. Path is validated above.
        from insightface.model_zoo.model_zoo import ModelRouter
        model = ModelRouter(str(path)).get_model(providers=["CPUExecutionProvider"], sess_options=options)
        if model is None or model.taskname != task:
            raise RuntimeError(f"Incompatible {task} weights: {path}")
        if task == "detection":
            model.prepare(ctx_id=-1, input_size=(640, 640), det_thresh=DET_SCORE_MIN)
        else:
            model.prepare(ctx_id=-1)
        return model

    def _ensure_detector(self):
        with self._detector_lock:
            if self._detector is None:
                model = self._local_model("det_10g.onnx", "detection")
                model.detect(np.zeros((320, 320, 3), np.uint8), input_size=DET_FAST_SIZE)
                self._detector = model
                self._app = SimpleNamespace(det_model=model)
                for attr, filename in (("_frontal_cascade", "haarcascade_frontalface_default.xml"),
                                       ("_profile_cascade", "haarcascade_profileface.xml")):
                    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + filename)
                    setattr(self, attr, None if cascade.empty() else cascade)
            return self._detector

    def _ensure_recognition(self):
        with self._recognition_lock:
            if self._recognition is None:
                model = self._local_model("w600k_r50.onnx", "recognition")
                model.get_feat(np.zeros((112, 112, 3), np.uint8))
                self._recognition = model
            return self._recognition

    def _ensure_models(self) -> bool:
        if self._models_loaded:
            return True
        try:
            self._ensure_detector()
            self._ensure_recognition()
            self._models_loaded = True
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    # ------------------------------------------------------------------
    # Detection / embedding helpers
    # ------------------------------------------------------------------

    def _detect(self, frame_bgr: np.ndarray) -> list[tuple[list[int], np.ndarray, float, str | None]]:
        """Run InsightFace on a BGR frame.

        Returns a list of (box[x1,y1,x2,y2], normed_embedding(512,), det_score, sex)
        for every face scoring above DET_SCORE_MIN. Empty list if none.
        """
        if self._app is None or frame_bgr is None or frame_bgr.size == 0:
            return []
        try:
            from insightface.app.common import Face
            with self._detector_lock:
                boxes, keypoints = self._detector.detect(frame_bgr, input_size=(640, 640))
                # Close faces may be visible at preview scale but missed at 640.
                # Keep the same model and threshold before declaring no faces.
                if not len(boxes):
                    boxes, keypoints = self._detector.detect(frame_bgr, input_size=DET_FAST_SIZE)
            faces = []
            # ArcFace has a separate owner lock; it cannot hold up fast SCRFD tracking.
            with self._recognition_lock:
                for i, box in enumerate(boxes):
                    face = Face(bbox=box[:4], kps=keypoints[i], det_score=box[4])
                    self._recognition.get(frame_bgr, face)
                    faces.append(face)
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            print(f"[Recognizer] detect error: {exc}")
            return []
        out: list[tuple[list[int], np.ndarray, float, str | None]] = []
        for f in faces:
            score = float(getattr(f, "det_score", 0.0))
            if score < DET_SCORE_MIN:
                continue
            box = [int(v) for v in f.bbox[:4]]
            emb = np.array(f.normed_embedding, dtype=np.float32, copy=True)
            sex = getattr(f, "sex", None)
            out.append((box, emb, score, sex))
        return out

    def detect_faces(self, frame_bgr: np.ndarray) -> list[dict]:
        """Fast detection-only pass (no embedding) for the live tracker.

        Runs SCRFD at a smaller input size (~52 ms vs ~520 ms for full recognition),
        so the overlay can refresh box positions ~10-15x more often than identity.
        Returns [{"box": [x1,y1,x2,y2], "score": float}] for confident detections.
        """
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        try:
            detector = self._ensure_detector()
            with self._detector_lock:
                bboxes, _kpss = detector.detect(frame_bgr, input_size=DET_FAST_SIZE)
            self.detector_error = None
        except Exception as exc:
            self.detector_error = str(exc)
            raise RuntimeError(f"Face detection unavailable: {exc}") from exc
        out: list[dict] = []
        for index, row in enumerate(bboxes):
            score = float(row[4])
            if score < DET_SCORE_MIN:
                continue
            out.append({"box": [int(row[0]), int(row[1]), int(row[2]), int(row[3])],
                        "score": score,
                        "keypoints": tuple(tuple(float(v) for v in point) for point in _kpss[index])})
        return out

    def _embed_detections(self, frame_bgr, detections):
        """Embed the tracker's exact same-frame landmarks, without redetecting faces."""
        from insightface.app.common import Face
        result = []
        with self._recognition_lock:
            for row in detections:
                points = np.asarray(row['keypoints'], dtype=np.float32)
                if points.shape != (5, 2) or not np.isfinite(points).all():
                    raise ValueError('Invalid same-frame face landmarks')
                face = Face(bbox=np.asarray(row['box']), kps=points, det_score=row['score'])
                self._recognition.get(frame_bgr, face)
                result.append((list(row['box']), np.array(face.normed_embedding, copy=True), row['score'], None))
        return result

    def enrollment_faces(self, frame_bgr: np.ndarray):
        """Return boxes and their embeddings from ONE raw enrollment frame."""
        if not self._ensure_models():
            return []
        return self._detect(frame_bgr)

    @staticmethod
    def _min_distance_to_student(probe: np.ndarray, student_embs: np.ndarray) -> float:
        """Min cosine distance from a unit probe to any of a student's unit embeddings."""
        sims = student_embs @ probe  # both unit-normalized → dot == cosine similarity
        return float(1.0 - float(np.max(sims)))

    @staticmethod
    def _assign_identities(
        distance_matrix: np.ndarray,
        threshold: float,
        margin: float = IDENTITY_MARGIN,
    ) -> list[int]:
        """Accept only unambiguous strongest evidence; never use a second identity.

        Rows are faces, columns are students. Each face may propose ONLY its best
        student, provided the absolute threshold and runner-up margin both pass.
        For competing claims, accept the strongest only when it clearly wins;
        otherwise leave every claimant unknown. A losing face is never relabeled
        as its next available enrolled student just to make names unique.
        """
        F, S = distance_matrix.shape
        assignment = [-1] * F
        if F == 0 or S == 0:
            return assignment
        candidates: dict[int, list[tuple[float, int]]] = {}
        for f in range(F):
            row = np.asarray(distance_matrix[f], dtype=float)
            # Non-finite evidence cannot establish the required ranking/margin.
            if not np.all(np.isfinite(row)):
                continue
            order = np.argsort(row)
            s = int(order[0])
            best = float(row[s])
            if best >= threshold:
                continue
            if S > 1 and float(row[order[1]]) - best < margin:
                continue
            candidates.setdefault(s, []).append((best, f))
        for s, claims in candidates.items():
            claims.sort()
            if len(claims) > 1 and claims[1][0] - claims[0][0] < margin:
                continue
            _distance, f = claims[0]
            assignment[f] = s
        return assignment

    def _cascade_hit(self, gray) -> bool:
        for cascade, img in (
            (self._frontal_cascade, gray),
            (self._profile_cascade, gray),
            (self._profile_cascade, cv2.flip(gray, 1)),  # right-facing profiles
        ):
            if cascade is None:
                continue
            rects = cascade.detectMultiScale(
                img, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60)
            )
            if len(rects) > 0:
                return True
        return False

    def has_face(self, frame_bgr: np.ndarray) -> bool:
        """Fast yes/no face check for the enrollment UI (cascades only, non-blocking).

        Returns False until the models/cascades are loaded (the caller warms them up
        on a background thread), so it never blocks the UI thread with a model load.
        This is only a positioning hint — the actual capture uses InsightFace.
        """
        if self._detector is None or frame_bgr is None or frame_bgr.size == 0:
            return False
        try:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            return self._cascade_hit(gray)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_known_faces(self) -> None:
        """Reload all enrolled face embeddings from the database, grouped per student.

        Each student becomes one (row, embeddings) entry where embeddings is a unit
        (K, 512) array of their angle embeddings, so matching can take a per-student
        minimum distance instead of treating each angle as a separate identity.
        """
        known: list[tuple[dict, np.ndarray]] = []
        try:
            students = self._db.get_all_students()
            for row in students:
                student = dict(row)          # sqlite3.Row → plain dict
                blob = student.get("encoding")
                if not blob:
                    continue
                try:
                    data = pickle.loads(blob)
                except Exception:
                    continue

                if isinstance(data, np.ndarray):
                    raw = [data]
                elif isinstance(data, list):
                    raw = [e for e in data if e is not None]
                else:
                    try:
                        raw = [np.array(data)]
                    except Exception:
                        raw = []

                unit_embs: list[np.ndarray] = []
                for e in raw:
                    e = np.asarray(e, dtype=np.float32).reshape(-1)
                    if e.size == 0:
                        continue
                    n = float(np.linalg.norm(e))
                    if n > 0:
                        unit_embs.append(e / n)
                if unit_embs:
                    known.append((student, np.vstack(unit_embs).astype(np.float32)))
        except Exception as exc:
            print(f"[Recognizer] load_known_faces error: {exc}")
        with self._lock:
            self._known = known

    def encode_face(self, frame_bgr: np.ndarray) -> tuple[np.ndarray | None, list | None]:
        """Detect the highest-confidence face and return its embedding + bounding box.

        Returns (embedding_np, [x1, y1, x2, y2]) or (None, None) if no face found.
        """
        if not self._ensure_models():
            return None, None
        dets = self._detect(frame_bgr)
        if not dets:
            return None, None
        best = max(range(len(dets)), key=lambda i: dets[i][2])  # highest det_score
        box, emb, _score, _sex = dets[best]
        return emb.astype(np.float32), [int(v) for v in box]

    def encode_face_multi(
        self,
        frames: list[np.ndarray],
        *,
        min_valid: int = 3,
    ) -> tuple[np.ndarray | None, list[int] | None]:
        """Embed the best face in each frame and average the embeddings.

        Returns (averaged_unit_embedding, best_box) where best_box [x1,y1,x2,y2]
        comes from the frame with the highest detection confidence. Returns
        (None, None) if fewer than `min_valid` frames had a detectable face.
        """
        if not self._ensure_models():
            return None, None

        embeddings: list[np.ndarray] = []
        best_box: list[int] | None = None
        best_conf = -1.0

        for frame in frames:
            if frame is None or frame.size == 0:
                continue
            dets = self._detect(frame)
            if not dets:
                continue
            idx = max(range(len(dets)), key=lambda i: dets[i][2])
            box, emb, score, _sex = dets[idx]
            embeddings.append(emb.astype(np.float32))
            if score > best_conf:
                best_conf = score
                best_box = [max(0, int(v)) for v in box]

        if len(embeddings) < min_valid:
            return None, None

        averaged = np.mean(embeddings, axis=0).astype(np.float32)
        norm = float(np.linalg.norm(averaged))
        if norm > 0:
            averaged = averaged / norm
        return averaged, best_box

    def encode_face_multi_angle(
        self,
        angle_frames: dict[str, list[np.ndarray]],
        *,
        min_valid_per_angle: int = 2,
    ) -> tuple[list[np.ndarray] | None, list[int] | None]:
        """Encode faces from multiple angle captures (front/left/right).

        Each angle's frame list is averaged into one unit-embedding via
        encode_face_multi(). Returns (list_of_embeddings, best_box) — one embedding
        per angle that had enough valid detections — or (None, None) if fewer than 2
        angles produced an embedding (insufficient coverage for robust multi-view
        recognition).
        """
        embeddings: list[np.ndarray] = []
        best_box: list[int] | None = None

        for angle, frames in angle_frames.items():
            emb, box = self.encode_face_multi(frames, min_valid=min_valid_per_angle)
            if emb is not None:
                embeddings.append(emb)
                if best_box is None:
                    best_box = box  # first valid box → preview photo

        if len(embeddings) < 2:
            return None, None

        return embeddings, best_box

    def recognize_faces(self, frame_bgr: np.ndarray, detections=None) -> list[dict]:
        """Detect ALL faces in frame and identify each against enrolled students.

        Returns list of dicts:
          {"box": [x1,y1,x2,y2], "name", "student_id", "gender", "matched",
           "detector_type", "embedding", "match_distance", "identity_uncertain"}

        Embeddings are normalized immutable tuples for same-frame tracking. An
        unassigned close/competing match is explicitly uncertain, while a face
        outside the absolute threshold is an ordinary unknown person.
        """
        if not self._ensure_models():
            return []
        try:
            dets = self._detect(frame_bgr) if detections is None else self._embed_detections(frame_bgr, detections)
            self.last_error = None
            if not dets:
                return []

            with self._lock:
                known_snapshot = list(self._known)  # [(student_row, (K,512) unit array)]

            F = len(dets)
            S = len(known_snapshot)

            # Per-student minimum cosine distance for every detected face → D[F, S].
            assignment = [-1] * F
            embeddings = []
            for _box, emb, _score, _sex in dets:
                emb = np.asarray(emb, dtype=np.float32).reshape(-1)
                norm = float(np.linalg.norm(emb))
                embeddings.append(emb / norm if norm > 0 and np.isfinite(norm) else None)
            D = np.full((F, S), np.inf, dtype=np.float32)
            if S > 0:
                for fi, emb in enumerate(embeddings):
                    if emb is None:
                        continue
                    for si, (_student, embs) in enumerate(known_snapshot):
                        D[fi, si] = self._min_distance_to_student(emb, embs)
                assignment = self._assign_identities(D, self.threshold, self.identity_margin)

            results = []
            for fi, (box, _emb, _score, sex) in enumerate(dets):
                name, sid, gender, matched = "Unknown", "", "—", False
                si = assignment[fi]
                emb = embeddings[fi]
                distance = float(np.min(D[fi])) if S else None
                if distance is not None and not np.isfinite(distance):
                    distance = None
                if si >= 0:
                    student = known_snapshot[si][0]
                    name = student.get("name", "Unknown")
                    sid = student.get("student_id", "")
                    gender = student.get("gender", "—") or "—"
                    matched = True
                else:
                    # Unknown face: surface InsightFace's gender guess for the alert panel.
                    if sex == "M":
                        gender = "Male"
                    elif sex == "F":
                        gender = "Female"

                results.append({
                    "box": [int(v) for v in box],
                    "name": name,
                    "student_id": sid,
                    "gender": gender,
                    "matched": matched,
                    "detector_type": "arcface",
                    "embedding": tuple(float(v) for v in emb) if emb is not None else (),
                    "match_distance": distance,
                    "identity_uncertain": emb is None or (
                        si < 0 and distance is not None and distance < self.threshold
                    ),
                })

            return results

        except Exception as exc:
            self.last_error = str(exc)
            print(f"[Recognizer] recognize_faces error: {exc}")
            return []
