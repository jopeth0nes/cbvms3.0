"""Validate uploaded appeal evidence before committing it with an appeal."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_PIXELS = 20_000_000
MAX_EVIDENCE_SIDE = 10_000


def validate_evidence(filename: str, file_type: str, data: bytes) -> None:
    if not isinstance(filename, str) or not filename.strip() or not isinstance(data, bytes):
        raise ValueError("Choose a valid picture file.")
    if not data or len(data) > MAX_EVIDENCE_BYTES:
        raise ValueError("Choose a non-empty evidence file no larger than 10 MB.")
    extension = Path(filename).suffix.lower()
    if file_type == "image":
        if extension not in {".jpg", ".jpeg", ".png", ".bmp"}:
            raise ValueError("Choose a JPG, PNG, or BMP picture.")
        try:
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format not in {"JPEG", "PNG", "BMP"}:
                    raise ValueError("Unsupported picture format.")
                width, height = picture.size
                if (width <= 0 or height <= 0 or width > MAX_EVIDENCE_SIDE
                        or height > MAX_EVIDENCE_SIDE or width * height > MAX_EVIDENCE_PIXELS):
                    raise ValueError("The picture dimensions are too large.")
                picture.verify()
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
        except Exception as exc:
            raise ValueError("The picture is unreadable or damaged. Choose another picture.") from exc
    else:
        raise ValueError("Picture evidence is required. PDF and text files are not accepted.")
