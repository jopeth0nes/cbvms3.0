# CBVMS Live Detection Pipeline — Uniform Violation Architecture

This document explains the architectural decision behind torso detection for
uniform-violation checking in the CBVMS live monitoring pipeline. It is written
for a technical panel review.

---

## 1. What does the current face bounding box represent?

The live pipeline identifies people with `FaceRecognizer.recognize_faces()`
(`core/recognizer.py`), which runs **MTCNN** face detection on the **full camera
frame**:

- **Coordinate space:** full-frame pixel coordinates `[x1, y1, x2, y2]`. No
  padding or pre-cropping is applied — MTCNN receives the whole frame, so the box
  maps directly onto the original image.
- **What it covers:** the **face only** — roughly forehead/eyebrows down to the
  chin, and ear to ear. It is **not** head-and-shoulders and **not** a full-body
  box.
- **Can a torso be derived from it?** Only crudely. Shifting the face box
  downward by a multiple of its height assumes the subject is upright,
  front-facing, at a consistent face-to-torso ratio, and fully in frame. Head
  tilt, camera angle, distance, and partial bodies all break that assumption.
  **A dedicated person detector is required for a reliable torso crop.**

---

## 2. Do we need a second detection pass for the torso?

| Option | Accuracy | Added latency (CPU) | Dependencies | Feasibility |
|--------|----------|---------------------|--------------|-------------|
| (a) Heuristic shift from face box | Low — fails on pose/angle/partial body | ~0 ms | none | trivial (current) |
| (b) **YOLOv8n person detection (COCO class 0)** | **Good** for upright people; real full-body box | **~50–80 ms/frame** | **none new** (`ultralytics` installed; `yolov8n.pt` auto-downloads ~6 MB once) | **high** |
| (c) YOLOv8n-seg person segmentation | Highest (pixel mask) | ~2–3× slower + extra model | none new but heavier | medium; overkill |

**Recommendation: Option (b) — YOLOv8n person detection.**

It produces a true full-body bounding box from which a stable torso slice can be
taken, reuses the already-installed `ultralytics` stack (no new dependency), and
keeps per-frame cost in an acceptable range on CPU. Segmentation (c) gives a
pixel-accurate mask we do not need — the uniform classifier consumes a
rectangular crop, so a bounding box is sufficient and markedly cheaper.

---

## 3. Where in the pipeline should uniform classification run?

**Inside the existing single background worker thread, inline** — not in a new
thread or queue.

- The camera UI thread enqueues **every 5th frame** into a `queue.Queue(maxsize=1)`
  using `put_nowait` (frames are **dropped** if the worker is still busy).
- Person detection (~50–80 ms) + N uniform predictions (~50–150 ms each) make a
  worker cycle ~130–300 ms. Because the queue is size-1 and drops on busy, a
  slower cycle simply **drops more frames** — the effective detection cadence
  self-throttles from ~6 fps to ~3–4 fps. This is more than enough for an
  entrance-monitoring use case.
- The **UI thread renders the camera feed independently** at the feed FPS, so the
  heavier worker **does not stall the live preview**.
- A second thread/queue would add shared-state hazards and complexity for
  marginal benefit. Keeping one daemon worker is simpler and safe.

---

## 4. What exact crop is sent to the uniform classifier?

Given a person box `[px1, py1, px2, py2]` with height `ph = py2 − py1`:

```
tx1 = px1
tx2 = px2
ty1 = py1 + 0.20 * ph     # skip head/neck
ty2 = py1 + 0.65 * ph     # down through the shirt/torso
```

The region is clamped to the frame and rejected if smaller than **32×32 px**.

**Does the crop need the 224×224 letterbox preprocessing the training panel uses?
No.** `ViolationTrainer.predict()` (`core/trainer.py`) already letterboxes the
input to 224×224 internally and passes the image as **BGR** (ultralytics converts
BGR→RGB itself). Letterboxing or RGB-converting in the caller would
double-process the image (and swap the R/B channels), degrading accuracy. The
caller passes the **raw BGR torso crop** directly.

---

## 5. How are violations surfaced versus presence alerts?

Previously, **every** recognized face wrote a `face_detected` row to the database,
flooding the Violation Log with non-violations. The corrected split:

**Database (the audit trail) — written only when there is something to record:**
- A real violation (`det["violation"]` set, e.g. `Wrong uniform (78%)`) → logged
  with the violation string.
- An unknown/unenrolled person → logged as `unknown_person`.
- A recognized, **compliant** student → **no database row**.
- Per-person cooldown of **300 s** prevents duplicate spam.

**Live Alerts sidebar (ephemeral) — shows every appearance:**
- 🟢 green dot + **"✓ OK"** — recognized, no violation.
- 🔴 red dot + **violation pill(s)** — recognized, violation detected.
- 🟡 yellow dot + **"Not enrolled"** — unknown person.

**Annotation box colors on the live feed:**
- Face box: **green** (matched + compliant), **red** (matched + violation),
  **blue** (unknown).
- Torso box: **orange** (BGR `0,165,255`), labelled with the uniform prediction
  and confidence (`✓ Uniform 91%` / `✗ Wrong uniform 78%`) so it is visually
  distinct from the face box.

---

## Summary of the data flow

```
camera frame ──(every 5th, drop-on-busy)──► worker thread
    │
    ├─ recognize_faces()                → face boxes + identities
    ├─ PersonDetector.detect_persons()  → full-body boxes  (once per frame)
    ├─ match face → person box          → torso crop (0.20–0.65 of body height)
    ├─ trainer.predict("uniform", torso)→ uniform label + confidence
    ├─ trainer.predict("earring", face) → earring label (male, optional)
    │
    └─ after(0, ...) ──► UI thread: annotate feed + Live Alerts card
                          worker: log to DB only if violation / unknown (300 s cooldown)
```

All inference is CPU-only and runs under the single `ultralytics` framework
(YOLOv8 detection + YOLOv8-cls classification) plus MTCNN/FaceNet for identity —
a unified, defensible computer-vision stack.

## Profile View Fix

### 1. Why MTCNN fails on profile / side views

MTCNN is a cascade of three CNNs (P-Net → R-Net → O-Net) trained on the WIDER FACE /
CelebA datasets, which are **overwhelmingly frontal**. Each stage proposes and refines
candidate windows using features (two eyes, a nose bridge, a mouth — the five-point
landmark layout) that only exist in a roughly **frontal pose**. Once the head yaws past
about **±30°**, one eye and half the landmark geometry disappear, the O-Net confidence
collapses below threshold, and the face is rejected. MTCNN is therefore a *frontal-biased*
detector by construction — it is excellent head-on and blind in true profile.

### 2. Why a single averaged embedding fails even when detection succeeds

FaceNet/InceptionResnetV1 maps a face to a 512-D vector and we compare identities by
**cosine distance**. A frontal embedding and a profile embedding of the *same person* are
**not in the same region of the embedding space** — the visible geometry is different, so
the network produces vectors that are far apart (cosine distance often > 0.6, our match
threshold). Enrolling a **single averaged frontal embedding** means a live profile capture
is compared only against a frontal anchor and is wrongly rejected as "Unknown." Averaging
multiple *frontal* frames does not help; it just produces a cleaner frontal anchor.

### 3. The two-part fix

- **Dual-detector pipeline.** At inference we first run MTCNN; if it returns nothing we fall
  back to OpenCV's `haarcascade_profileface.xml` (left-facing), then the same cascade on a
  horizontally-flipped frame (right-facing), then `haarcascade_frontalface_default.xml`.
  Cascade crops are aligned to 160×160 and normalized to `[-1, 1]` to match MTCNN's tensor
  format, so the same FaceNet embedder runs unchanged.
- **Multi-angle, multi-embedding enrollment.** A guided wizard captures **front + left +
  right** poses and stores **one averaged embedding per angle** (a pickled `list`), giving
  the gallery a profile anchor to match against.

### 4. Why multiple angle embeddings per student is standard practice

Production face-recognition systems use **gallery augmentation**: they store several
templates per identity spanning pose, expression, and lighting, because a single template
cannot cover the appearance manifold of a face. Storing front/left/right embeddings is the
small-scale version of this — each stored vector anchors a different pose region so a live
capture at any angle has a nearby gallery template.

### 5. Matching strategy

Recognition computes the **minimum cosine distance across *all* stored embeddings of *all*
students** and matches if that minimum is below the sensitivity threshold. Because each
student contributes several angle embeddings to the gallery, the closest-pose template wins
— a live profile matches the enrolled profile embedding rather than being forced to compare
against a frontal-only anchor.
