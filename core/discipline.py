"""Shared rules and date helpers for the CBVMS discipline workflow.

Database timestamps are stored as naive-looking UTC strings for compatibility with
SQLite's ``datetime('now')`` format.  Values become timezone-aware as soon as they
enter Python so review and appeal windows are always exactly 5 * 24 hours.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone


ADMIN_REVIEW_DAYS = 5
STUDENT_APPEAL_DAYS = 5
STRIKE_LIMIT = 3

PENDING_REVIEW = "pending_review"
CONFIRMED = "confirmed"
AUTO_CONFIRMED = "auto_confirmed"
DISMISSED = "dismissed"

CONFIRMED_STATUSES = frozenset({CONFIRMED, AUTO_CONFIRMED, "reviewed"})
VISIBLE_VIOLATION_STATUSES = CONFIRMED_STATUSES

DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

_NON_DISCIPLINARY_CODES = frozenset({
    "unknown_person",
    "face_detected",
    "unknown_violation",
})

_DISPLAY_NAMES = {
    "wrong_uniform": "Wrong Uniform",
    "earring": "Earring",
    "missing_id": "Missing ID",
    "improper_hair": "Improper Hair",
    "improper_footwear": "Improper Footwear",
    "unknown_person": "Unknown Person",
    "face_detected": "Face Detected",
    "unknown_violation": "Unknown Violation",
}


def utc_now() -> datetime:
    """Return the current UTC time as an aware datetime."""

    return datetime.now(timezone.utc)


def parse_db_datetime(value: datetime | str | None) -> datetime | None:
    """Parse a SQLite/ISO timestamp and normalize it to aware UTC."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        # Existing CBVMS/SQLite timestamps are UTC even though they have no suffix.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_db_datetime(value: datetime | str | None) -> str:
    """Format a timestamp in the repository's existing SQLite UTC representation."""

    parsed = parse_db_datetime(value)
    if parsed is None:
        raise ValueError(f"Invalid datetime value: {value!r}")
    return parsed.strftime(DB_DATETIME_FORMAT)


def add_calendar_days(value: datetime | str, days: int) -> datetime:
    """Add exact 24-hour periods to an authoritative timestamp."""

    parsed = parse_db_datetime(value)
    if parsed is None:
        raise ValueError(f"Invalid datetime value: {value!r}")
    return parsed + timedelta(days=days)


def remaining_time_text(deadline: datetime | str | None, *, now: datetime | str | None = None) -> str:
    """Return a compact human-readable countdown for a persisted deadline."""

    end = parse_db_datetime(deadline)
    current = parse_db_datetime(now) if now is not None else utc_now()
    if end is None or current is None:
        return "Deadline unavailable"
    seconds = (end - current).total_seconds()
    if seconds <= 0:
        return "Expired"
    if seconds >= 86400:
        days = max(1, math.ceil(seconds / 86400))
        return f"{days} day{'s' if days != 1 else ''} remaining"
    if seconds >= 3600:
        hours = max(1, math.ceil(seconds / 3600))
        return f"{hours} hour{'s' if hours != 1 else ''} remaining"
    minutes = max(1, math.ceil(seconds / 60))
    return f"{minutes} minute{'s' if minutes != 1 else ''} remaining"


def local_calendar_day_utc_bounds(day: str) -> tuple[str, str]:
    """Translate a local YYYY-MM-DD calendar day into half-open UTC DB bounds."""

    parsed_day = datetime.strptime(day, "%Y-%m-%d").date()
    # astimezone() on a naive datetime applies the host's configured local zone,
    # including its historical daylight-saving rule for the requested date.
    local_start = datetime.combine(parsed_day, datetime.min.time()).astimezone()
    local_end = datetime.combine(
        parsed_day + timedelta(days=1), datetime.min.time()
    ).astimezone()
    return (
        format_db_datetime(local_start.astimezone(timezone.utc)),
        format_db_datetime(local_end.astimezone(timezone.utc)),
    )


def normalize_violation_code(value: str | None) -> str:
    """Derive a stable category code from legacy or display-oriented text.

    New detectors should pass their machine label directly.  This fallback keeps old
    rows such as ``Wrong uniform (82%)`` safe and deliberately removes confidence
    percentages from identity.
    """

    raw = (value or "").strip().lower()
    if not raw:
        return "unknown_violation"
    first = raw.split(",", 1)[0].strip()
    without_confidence = re.sub(r"\(\s*\d+(?:\.\d+)?\s*%\s*\)", "", first).strip()
    words = without_confidence.replace("-", " ").replace("_", " ")

    if "unknown person" in words or "unidentified person" in words:
        return "unknown_person"
    if "wrong uniform" in words or "improper uniform" in words:
        return "wrong_uniform"
    if "earring" in words:
        return "earring"
    if "missing id" in words or "no id" in words or "without id" in words:
        return "missing_id"
    if "hair" in words and any(token in words for token in ("wrong", "improper", "color", "colour")):
        return "improper_hair"
    if "footwear" in words or "wrong shoes" in words or "improper shoes" in words:
        return "improper_footwear"
    if "face detected" in words:
        return "face_detected"

    code = re.sub(r"[^a-z0-9]+", "_", without_confidence).strip("_")
    for suffix in ("_detected", "_violation"):
        if code.endswith(suffix) and len(code) > len(suffix):
            code = code[: -len(suffix)]
    return code or "unknown_violation"


def violation_display_name(code: str | None, fallback: str | None = None) -> str:
    """Return a student-facing category label without exposing machine syntax."""

    stable = normalize_violation_code(code)
    if stable in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[stable]
    if fallback:
        cleaned = re.sub(r"\(\s*\d+(?:\.\d+)?\s*%\s*\)", "", fallback).strip()
        if cleaned:
            return cleaned.replace("_", " ").title()
    return stable.replace("_", " ").title()


def is_disciplinary_code(code: str | None) -> bool:
    """Whether a category can award a student strike."""

    return normalize_violation_code(code) not in _NON_DISCIPLINARY_CODES


def academic_term_code(name: str) -> str:
    code = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return code or "semester"


def default_academic_term(*, now: datetime | str | None = None) -> dict[str, str]:
    """Return a sensible initial term; administrators can change it in Settings."""

    current = parse_db_datetime(now) if now is not None else utc_now()
    if current is None:
        current = utc_now()
    year = current.year
    if current.month >= 6:
        name = "Semester 1"
        school_year = f"{year}-{year + 1}"
    else:
        name = "Semester 2"
        school_year = f"{year - 1}-{year}"
    return {
        "semester_code": academic_term_code(name),
        "semester_name": name,
        "school_year": school_year,
    }
