"""Face recognition using InsightFace buffalo_l (SCRFD detection + ArcFace embedding).

ArcFace (w600k_r50) embeddings are far more discriminative than the previous
facenet/VGGFace2 model: genuine pairs land at cosine distance ~0.3-0.5 while
different people (unknowns) land at ~0.85-1.0. That clean gap is what makes
unknown-rejection reliable — an unregistered face stays well outside the match
threshold instead of leaking onto the nearest enrolled identity.

Two failure modes are addressed here:
  1. Unknown-leakage  -> discriminative ArcFace embeddings + strict threshold.
  2. Duplicate labels -> unique greedy assignment (one identity per face/frame).
"""

from __future__ import annotations

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
        self._app = None             # lazy — avoid slow model load at startup
        self._frontal_cascade = None  # lightweight detectors for the has_face() UI hint
        self._profile_cascade = None
        # (student_row, embeddings) where embeddings is a unit-normalized (K, 512) array
        # holding that student's per-angle ArcFace embeddings.
        self._known: list[tuple[dict, np.ndarray]] = []
        self._models_loaded = False
        self.threshold: float = MATCH_THRESHOLD  # runtime-adjustable match sensitivity
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

    def _ensure_models(self) -> bool:
        if self._models_loaded:
            return True
        # Serialize the (slow) first load — the enrollment wizard warms models on a
        # background thread while the live face worker may also call this; without the
        # lock both would build the InsightFace app at once (wasteful, risks a model
        # download race). The fast path above stays lock-free after load.
        with self._lock:
            if self._models_loaded:
                return True
            return self._load_models()

    def _load_models(self) -> bool:
        try:
            from insightface.app import FaceAnalysis

            # Load only the models we actually use: SCRFD detection, ArcFace recognition,
            # and gender/age (for the unknown-face gender hint). Skipping the 106-point
            # landmark model speeds up both startup load and per-frame recognition.
            app = FaceAnalysis(
                name="buffalo_l",
                allowed_modules=["detection", "recognition", "genderage"],
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=-1, det_size=(640, 640))  # ctx_id=-1 => CPU
            self._app = app

            # OpenCV Haar cascades power only the lightweight has_face() preview hint,
            # which runs on the UI thread every 200ms during enrollment — far too often
            # to run full SCRFD there. Both ship with opencv-python (no download).
            try:
                self._frontal_cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                )
                self._profile_cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_profileface.xml"
                )
                if self._frontal_cascade.empty():
                    self._frontal_cascade = None
                if self._profile_cascade.empty():
                    self._profile_cascade = None
            except Exception as exc:
                print(f"[Recognizer] cascade load failed: {exc}")
                self._frontal_cascade = self._profile_cascade = None

            self._models_loaded = True
            return True
        except Exception as exc:
            print(f"[Recognizer] model load failed: {exc}")
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
            faces = self._app.get(frame_bgr)  # InsightFace expects BGR (OpenCV) frames
        except Exception as exc:
            print(f"[Recognizer] detect error: {exc}")
            return []
        out: list[tuple[list[int], np.ndarray, float, str | None]] = []
        for f in faces:
            score = float(getattr(f, "det_score", 0.0))
            if score < DET_SCORE_MIN:
                continue
            box = [int(v) for v in f.bbox[:4]]
            emb = np.asarray(f.normed_embedding, dtype=np.float32)  # already L2-normalized
            sex = getattr(f, "sex", None)
            out.append((box, emb, score, sex))
        return out

    def detect_faces(self, frame_bgr: np.ndarray) -> list[dict]:
        """Fast detection-only pass (no embedding) for the live tracker.

        Runs SCRFD at a smaller input size (~52 ms vs ~520 ms for full recognition),
        so the overlay can refresh box positions ~10-15x more often than identity.
        Returns [{"box": [x1,y1,x2,y2], "score": float}] for confident detections.
        """
        if not self._ensure_models():
            return []
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        try:
            bboxes, _kpss = self._app.det_model.detect(frame_bgr, input_size=DET_FAST_SIZE)
        except Exception as exc:
            print(f"[Recognizer] detect_faces error: {exc}")
            return []
        out: list[dict] = []
        for row in bboxes:
            score = float(row[4])
            if score < DET_SCORE_MIN:
                continue
            out.append({"box": [int(row[0]), int(row[1]), int(row[2]), int(row[3])],
                        "score": score})
        return out

    @staticmethod
    def _min_distance_to_student(probe: np.ndarray, student_embs: np.ndarray) -> float:
        """Min cosine distance from a unit probe to any of a student's unit embeddings."""
        sims = student_embs @ probe  # both unit-normalized → dot == cosine similarity
        return float(1.0 - float(np.max(sims)))

    @staticmethod
    def _assign_identities(distance_matrix: np.ndarray, threshold: float) -> list[int]:
        """Unique greedy assignment of faces → students.

        distance_matrix is (F, S); returns a list of length F where entry f is the
        assigned student index, or -1 for "Unknown". Each face and each student is
        used at most once: the globally-closest under-threshold (face, student) pair
        is committed first, then the next, etc. This guarantees one identity per face
        in a frame (fixing duplicate labels) while faces with no under-threshold
        student remain Unknown (fixing unknown-leakage in tandem with the threshold).
        """
        F, S = distance_matrix.shape
        assignment = [-1] * F
        if F == 0 or S == 0:
            return assignment
        candidates: list[tuple[float, int, int]] = []
        for f in range(F):
            for s in range(S):
                d = float(distance_matrix[f, s])
                if d < threshold:
                    candidates.append((d, f, s))
        candidates.sort(key=lambda t: t[0])
        used_faces: set[int] = set()
        used_students: set[int] = set()
        for d, f, s in candidates:
            if f in used_faces or s in used_students:
                continue
            assignment[f] = s
            used_faces.add(f)
            used_students.add(s)
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
        if not self._models_loaded or frame_bgr is None or frame_bgr.size == 0:
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

    def recognize_faces(self, frame_bgr: np.ndarray) -> list[dict]:
        """Detect ALL faces in frame and identify each against enrolled students.

        Returns list of dicts:
          {"box": [x1,y1,x2,y2], "name", "student_id", "gender", "matched", "detector_type"}
        """
        if not self._ensure_models():
            return []
        try:
            dets = self._detect(frame_bgr)
            if not dets:
                return []

            with self._lock:
                known_snapshot = list(self._known)  # [(student_row, (K,512) unit array)]

            F = len(dets)
            S = len(known_snapshot)

            # Per-student minimum cosine distance for every detected face → D[F, S].
            assignment = [-1] * F
            if S > 0:
                D = np.empty((F, S), dtype=np.float32)
                for fi, (_box, emb, _score, _sex) in enumerate(dets):
                    for si, (_student, embs) in enumerate(known_snapshot):
                        D[fi, si] = self._min_distance_to_student(emb, embs)
                # Unique assignment: one identity per face; unknowns stay -1.
                assignment = self._assign_identities(D, self.threshold)

            results = []
            for fi, (box, _emb, _score, sex) in enumerate(dets):
                name, sid, gender, matched = "Unknown", "", "—", False
                si = assignment[fi]
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
                })

            return results

        except Exception as exc:
            print(f"[Recognizer] recognize_faces error: {exc}")
            return []
