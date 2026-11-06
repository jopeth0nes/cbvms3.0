Face model setup
================

If registration reports missing `det_10g.onnx` or `w600k_r50.onnx`, run:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_face_models.py
```

The installer downloads the official InsightFace buffalo_l archive (~275 MB),
verifies its published SHA-256, and installs the detector and recognition weights
under `models/buffalo_l/`. Model files are excluded from Git. Restart CBVMS after
installation and reopen registration.

The recognizer uses the default InsightFace cache when complete, otherwise it
uses this project-local pack. An explicit `CBVMS_FACE_MODEL_DIR` takes precedence.
Downloads never run in the camera or UI path.

Official model source and license terms:
https://github.com/deepinsight/insightface/releases/tag/model-zoo
https://github.com/deepinsight/insightface#license

The supplied pretrained models are for non-commercial research; other uses need
the appropriate InsightFace license.
