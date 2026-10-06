"""Conservative, descriptive checks for appeal evidence; never assess truth."""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import warnings
from pathlib import Path

from PIL import Image

ANALYZER_VERSION = "evidence-forensics-1"
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_SIDE = 10_000
ARTIFACT_MAX = 512 * 1024
_SAFE = re.compile(r"[^\w .:+/-]", re.UNICODE)

def _clean(value, limit=120):
    if value is None:
        return None
    text = _SAFE.sub("", str(value)).strip()
    return text[:limit] or None

def _metadata(image):
    info = image.info or {}
    out = {}
    exif = None
    try:
        exif = image.getexif()
    except Exception:
        pass
    if exif:
        names = {271: "camera_make", 272: "camera_model", 274: "orientation",
                 306: "datetime", 36867: "datetime_original", 305: "software"}
        for key, name in names.items():
            if key in exif:
                val = _clean(exif.get(key))
                if val is not None:
                    out[name] = val
        gps = exif.get(34853)
        out["gps_present"] = bool(gps)
    for key, name in (("Software", "software"), ("Creation Time", "creation_time"),
                      ("DateTime", "datetime")):
        if name not in out and key in info:
            val = _clean(info.get(key))
            if val:
                out[name] = val
    return out

def analyze_evidence(data: bytes, filename: str) -> dict:
    started = time.monotonic()
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("evidence_size_invalid")
    ext = Path(str(filename).replace("\\", "/")).suffix.lower()
    signals = []
    metadata = {}
    fmt = None
    facts = {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                fmt = im.format
                width, height = im.size
                if (fmt not in {"JPEG", "PNG", "BMP"} or width <= 0 or height <= 0
                        or width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS):
                    raise ValueError("image_dimensions_or_format_invalid")
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                metadata = _metadata(im)
                im.load()
                facts = {"format": fmt, "width": width, "height": height, "mode": im.mode}
    except Exception as exc:
        raise ValueError("image_decode_invalid") from exc
    if ext not in {".jpg", ".jpeg", ".png", ".bmp"}:
        raise ValueError("unsupported_extension")
    from core.forensics_classical import describe_pixels
    facts["pixel_measurements"] = describe_pixels(data)
    if (fmt == "JPEG" and ext not in {".jpg", ".jpeg"}) or (fmt == "PNG" and ext != ".png") or (fmt == "BMP" and ext != ".bmp"):
        signals.append({"code": "extension_format_mismatch", "severity": "observation",
                        "summary": "File extension does not match the decoded image format."})
    # JPEG quantization tables are descriptive encoding facts, not manipulation indicators.
    if fmt == "JPEG":
        with Image.open(io.BytesIO(data)) as im:
            qtables = getattr(im, "quantization", {}) or {}
            if qtables:
                facts["jpeg_quantization_table_count"] = min(len(qtables), 4)
                facts["jpeg_quantization_table_lengths"] = [len(v) for _, v in sorted(qtables.items())[:4]]
    provenance = {"c2pa": {"status": "unavailable"}, "model": {"status": "not_configured"}}
    artifacts = {"localization_png": None, "reliability_png": None}
    for module_name, method, target in (("core.forensics_c2pa", "inspect_c2pa", "c2pa"),
                                        ("core.forensics_model", "inspect_model", "model")):
        try:
            module = __import__(module_name, fromlist=[method])
            result = getattr(module, method)(data, fmt)
            if isinstance(result, dict):
                result = dict(result)
                if target == "model":
                    for key in artifacts:
                        value = result.pop(key, None)
                        if isinstance(value, bytes) and len(value) <= ARTIFACT_MAX:
                            artifacts[key] = value
                provenance[target] = result
        except (ImportError, AttributeError):
            pass
        except Exception:
            provenance[target] = {"status": "error"}
    c2pa_status = str(provenance["c2pa"].get("status", "unavailable"))
    # A credential/manifest anomaly warrants human review but says nothing by itself
    # about who altered an image or when. Experimental model output stays inconclusive.
    if c2pa_status == "invalid":
        signals.append({"code": "c2pa_credential_verification_anomaly", "severity": "review",
                        "summary": "C2PA credential verification reported an anomaly; this does not establish image manipulation."})
    classification = "review_recommended" if c2pa_status == "invalid" else "inconclusive"
    return {"sha256": hashlib.sha256(data).hexdigest(), "analyzer_version": ANALYZER_VERSION,
            "classification": classification, "signals": signals, "metadata": metadata,
            "provenance": provenance, "model": provenance["model"], "reliability": provenance["model"].get("reliability"),
            "model_raw_score": provenance["model"].get("raw_score"), "model_calibrated": False,
            "risk": None, "facts": facts, "duration_ms": int((time.monotonic()-started)*1000),
            **artifacts}
