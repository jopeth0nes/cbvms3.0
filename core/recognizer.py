"""Face recognition using MTCNN (detection) + InceptionResnetV1 (embedding).

No dlib or CMake required — uses facenet-pytorch which builds on the existing
PyTorch installation already present via ultralytics.
"""

from __future__ import annotations

import pickle
import threading
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from database.db_manager import CBVMSDatabase

# Cosine distance threshold: lower = stricter matching.
MATCH_THRESHOLD = 0.6


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    a = a / (np.linalg.norm(a) + 1e-10)
    b = b / (np.linalg.norm(b) + 1e-10)
    return float(1.0 - np.dot(a, b))


class FaceRecognizer:
    """Detects and identifies faces using MTCNN + InceptionResnetV1 (VGGFace2)."""

    def __init__(self, db: "CBVMSDatabase") -> None:
        self._db = db
        self._lock = threading.Lock()
        self._mtcnn = None          # lazy — avoid slow import at startup
        self._resnet = None
        self._profile_cascade = None  # OpenCV fallback detectors (loaded with models)
        self._frontal_cascade = None
        self._known: list[tuple[dict, np.ndarray]] = []  # (student_row, embedding)
        self._models_loaded = False
        self.threshold: float = MATCH_THRESHOLD  # runtime-adjustable match sensitivity
        self.load_known_faces()

    @property
    def sensitivity_label(self) -> str:
        if self.threshold <= 0.45:
            return "Very Strict"
        elif self.threshold <= 0.55:
            return "Strict"
        elif self.threshold <= 0.65:
            return "Balanced"
        elif self.threshold <= 0.75:
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
        # lock both would build MTCNN/ResNet at once (wasteful, and risks a torch-hub
        # weight-download race). The fast path above stays lock-free after load.
        with self._lock:
            if self._models_loaded:
                return True
            return self._load_models()

    def _load_models(self) -> bool:
        try:
            import torch
            from facenet_pytorch import MTCNN, InceptionResnetV1

            self._mtcnn = MTCNN(
                keep_all=True,
                min_face_size=40,
                thresholds=[0.6, 0.7, 0.7],
                device="cpu",
            )
            self._resnet = InceptionResnetV1(pretrained="vggface2").eval()

            # OpenCV Haar cascades as a fallback detector for profile/side views that
            # MTCNN (frontal-biased) misses. Both ship with opencv-python — no download.
            try:
                self._profile_cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_profileface.xml"
                )
                self._frontal_cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                )
                if self._profile_cascade.empty():
                    self._profile_cascade = None
                if self._frontal_cascade.empty():
                    self._frontal_cascade = None
            except Exception as exc:
                print(f"[Recognizer] cascade load failed: {exc}")
                self._profile_cascade = self._frontal_cascade = None

            self._models_loaded = True
            return True
        except Exception as exc:
            print(f"[Recognizer] model load failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Dual detector (MTCNN + OpenCV cascade fallback for profile views)
    # ------------------------------------------------------------------

    def _cascade_faces(self, cascade, gray) -> list[list[int]]:
        if cascade is None:
            return []
        rects = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60)
        )
        return [[int(x), int(y), int(x + w), int(y + h)] for (x, y, w, h) in rects]

    def _align_cascade_boxes(self, boxes: list[list[int]], frame_bgr: np.ndarray):
        """Crop each box and build an MTCNN-style aligned tensor (N,3,160,160) in [-1,1].

        Returns (kept_boxes, tensor) so the boxes stay in lock-step with the tensor rows
        even if a box is dropped (empty crop) — pairing a box with the wrong embedding
        would otherwise mislabel that detection.
        """
        import torch

        tensors: list = []
        kept: list[list[int]] = []
        h, w = frame_bgr.shape[:2]
        for box in boxes:
            x1, y1, x2, y2 = box
            x1c, y1c = max(0, x1), max(0, y1)
            x2c, y2c = min(w, x2), min(h, y2)
            crop = frame_bgr[y1c:y2c, x1c:x2c]
            if crop.size == 0:
                continue
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            face_pil = Image.fromarray(rgb).resize((160, 160), Image.BILINEAR)
            arr = (np.asarray(face_pil).astype(np.float32) / 127.5) - 1.0  # [-1, 1]
            tensors.append(torch.from_numpy(np.transpose(arr, (2, 0, 1))))
            kept.append(box)
        if not tensors:
            return [], None
        return kept, torch.stack(tensors)

    def _detect_with_fallback(self, pil_img, frame_bgr):
        """Detect faces with MTCNN, falling back to OpenCV cascades for profile views.

        Returns (boxes, aligned_tensor, probs, detector_type):
          - boxes: list of [x1,y1,x2,y2]
          - aligned_tensor: (N,3,160,160) torch tensor (or None)
          - probs: list[float] aligned with boxes (real MTCNN probs, or 0.75 for cascade)
          - detector_type: "mtcnn" | "cascade" | "none"
        Each call returns from exactly one detector, so detector_type applies to all boxes.
        """
        # 1) MTCNN — frontal / near-frontal
        try:
            boxes, probs = self._mtcnn.detect(pil_img)
        except Exception:
            boxes, probs = None, None
        if boxes is not None and len(boxes) > 0:
            faces = self._mtcnn(pil_img)
            if faces is not None and len(faces) > 0:
                # detect() and __call__() are separate passes; keep boxes/probs in
                # lock-step with the aligned-tensor count so a face dropped during
                # alignment can't cause an index error that loses the whole frame.
                n = int(faces.shape[0])
                box_list = [[int(v) for v in b] for b in boxes[:n]]
                prob_list = [float(p) if p is not None else 0.0 for p in probs[:n]]
                return box_list, faces, prob_list, "mtcnn"

        # Fallback: OpenCV cascades
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]

        # 2) profile cascade (left-facing)
        boxes = self._cascade_faces(self._profile_cascade, gray)
        # 3) profile cascade on flipped frame (right-facing) → map x back
        if not boxes:
            flipped = self._cascade_faces(self._profile_cascade, cv2.flip(gray, 1))
            boxes = [[w - x2, y1, w - x1, y2] for (x1, y1, x2, y2) in flipped]
        # 4) frontal cascade (catch MTCNN-missed frontals)
        if not boxes:
            boxes = self._cascade_faces(self._frontal_cascade, gray)

        if not boxes:
            return [], None, [], "none"

        boxes, tensor = self._align_cascade_boxes(boxes, frame_bgr)
        if tensor is None:
            return [], None, [], "none"
        return boxes, tensor, [0.75] * len(boxes), "cascade"

    def has_face(self, frame_bgr: np.ndarray) -> bool:
        """Fast yes/no face check for the enrollment UI (cascades only, non-blocking).

        Returns False until the models/cascades are loaded (the caller warms them up
        on a background thread), so it never blocks the UI thread with a model load.
        """
        if not self._models_loaded or frame_bgr is None or frame_bgr.size == 0:
            return False
        try:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            if self._cascade_faces(self._frontal_cascade, gray):
                return True
            if self._cascade_faces(self._profile_cascade, gray):
                return True
            if self._cascade_faces(self._profile_cascade, cv2.flip(gray, 1)):
                return True
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_known_faces(self) -> None:
        """Reload all enrolled face embeddings from the database."""
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
                    if isinstance(data, np.ndarray):
                        # Legacy single-embedding format.
                        known.append((student, data.astype(np.float32)))
                    elif isinstance(data, list):
                        # Multi-angle: one gallery entry per angle embedding.
                        for emb in data:
                            if isinstance(emb, np.ndarray):
                                known.append((student, emb.astype(np.float32)))
                    else:
                        known.append((student, np.array(data, dtype=np.float32)))
                except Exception:
                    pass
        except Exception as exc:
            print(f"[Recognizer] load_known_faces error: {exc}")
        with self._lock:
            self._known = known

    def encode_face(self, frame_bgr: np.ndarray) -> tuple[np.ndarray | None, list | None]:
        """Detect the largest face in frame and return its embedding + bounding box.

        Returns (embedding_np, [x1, y1, x2, y2]) or (None, None) if no face found.
        """
        if not self._ensure_models():
            return None, None
        try:
            import torch

            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)

            boxes, faces, probs, _dtype = self._detect_with_fallback(pil, frame_bgr)
            if not boxes or faces is None:
                return None, None

            # Pick the face with highest detection confidence
            best = int(np.argmax(probs)) if probs else 0
            box = [int(v) for v in boxes[best]]  # [x1, y1, x2, y2]

            face_tensor = faces[best].unsqueeze(0)  # (1, 3, 160, 160)
            with torch.no_grad():
                embedding = self._resnet(face_tensor)  # (1, 512)
            emb_np = embedding.squeeze().numpy().astype(np.float32)
            return emb_np, box

        except Exception as exc:
            print(f"[Recognizer] encode_face error: {exc}")
            return None, None

    def encode_face_multi(
        self,
        frames: list[np.ndarray],
        *,
        min_valid: int = 3,
    ) -> tuple[np.ndarray | None, list[int] | None]:
        """Embed the best face in each frame and average the embeddings.

        Returns (averaged_unit_embedding, best_box) where best_box [x1,y1,x2,y2]
        comes from the frame with the highest MTCNN confidence. Returns
        (None, None) if fewer than `min_valid` frames had a detectable face.
        """
        if not self._ensure_models():
            return None, None

        import torch

        embeddings: list[np.ndarray] = []
        best_box: list[int] | None = None
        best_conf = -1.0

        for frame in frames:
            if frame is None or frame.size == 0:
                continue
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                boxes, faces, probs, _dtype = self._detect_with_fallback(pil, frame)
                if not boxes or faces is None:
                    continue
                idx = int(np.argmax(probs)) if probs else 0
                conf = float(probs[idx]) if probs else 0.0

                with torch.no_grad():
                    emb = self._resnet(faces[idx].unsqueeze(0))[0].numpy().astype(np.float32)
                embeddings.append(emb)

                if conf > best_conf:
                    best_conf = conf
                    best_box = [max(0, int(v)) for v in boxes[idx]]
            except Exception:
                continue

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
          {"box": [x1,y1,x2,y2], "name": str, "student_id": str, "matched": bool}
        """
        if not self._ensure_models():
            return []
        try:
            import torch

            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)

            boxes, faces, probs, detector_type = self._detect_with_fallback(pil, frame_bgr)
            if not boxes or faces is None:
                return []

            with self._lock:
                known_snapshot = list(self._known)

            results = []
            with torch.no_grad():
                embeddings = self._resnet(faces)  # (N, 512)

            for i, box in enumerate(boxes):
                if i >= embeddings.shape[0]:
                    break  # never index past the aligned-tensor batch (count safety)
                # The 0.85 confidence gate applies to MTCNN only; cascade fallback
                # detections have no real score (0.75) but are still usable.
                if detector_type == "mtcnn" and i < len(probs) and probs[i] < 0.85:
                    continue
                emb = embeddings[i].numpy().astype(np.float32)
                box_int = [int(v) for v in box]

                name, sid, gender, matched = "Unknown", "", "—", False
                if known_snapshot:
                    distances = [_cosine_distance(emb, k_emb) for _, k_emb in known_snapshot]
                    best_idx = int(np.argmin(distances))
                    if distances[best_idx] < self.threshold:
                        student = known_snapshot[best_idx][0]
                        name = student.get("name", "Unknown")
                        sid = student.get("student_id", "")
                        gender = student.get("gender", "—") or "—"
                        matched = True

                results.append({
                    "box": box_int,
                    "name": name,
                    "student_id": sid,
                    "gender": gender,
                    "matched": matched,
                    "detector_type": detector_type,
                })

            return results

        except Exception as exc:
            print(f"[Recognizer] recognize_faces error: {exc}")
            return []
