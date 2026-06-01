"""AI-assisted appeal analysis using the Anthropic Claude API.

Runs in a background thread so it never blocks the UI.
Reads ANTHROPIC_API_KEY from the environment (or .env file if python-dotenv
is installed). Gracefully degrades to status "unavailable" when the key is
absent or the API call fails.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Callable

_SYSTEM_PROMPT = (
    "You are an impartial appeal review assistant for a school uniform and grooming "
    "compliance monitoring system called SECURE. Your job is to analyze a student's "
    "appeal against a detected violation and return a structured JSON recommendation.\n\n"
    "Violation types the system detects:\n"
    "- wrong_uniform: student not wearing the prescribed school uniform\n"
    "- no_id_badge: student not wearing their school ID/badge\n"
    "- wrong_hair_color: student has non-natural or non-compliant hair color\n"
    "- with_earring / earring_violation: male student wearing earrings (school policy)\n"
    "- unknown_person: unrecognized individual detected at entrance\n"
    "- face_detected: generic detection event (usually informational)\n\n"
    "Evaluate the student's reasoning and evidence text. Consider:\n"
    "1. Is the explanation specific and plausible (e.g., lost ID, uniform damage, "
    "   medical reason for hair)?\n"
    "2. Does it address the violation type directly?\n"
    "3. Are there red flags (vague, contradictory, or repeated excuses)?\n\n"
    "Respond ONLY with a valid JSON object — no markdown, no commentary:\n"
    "{\n"
    '  "recommendation": "Recommended Valid" | "Recommended Invalid",\n'
    '  "confidence": "High" | "Medium" | "Low",\n'
    '  "summary": "<2-3 sentence explanation of your recommendation>",\n'
    '  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]\n'
    "}"
)


def analyze_appeal(
    *,
    violation_type: str,
    violation_timestamp: str,
    student_name: str,
    student_id: str,
    reason: str,
    on_complete: Callable[[str, str, str], None],
) -> None:
    """Kick off analysis in a daemon thread.

    ``on_complete(recommendation, confidence, summary)`` is called from the
    background thread when done.  The caller is responsible for scheduling
    any UI updates with ``widget.after(0, ...)``.
    """
    thread = threading.Thread(
        target=_run_analysis,
        kwargs=dict(
            violation_type=violation_type,
            violation_timestamp=violation_timestamp,
            student_name=student_name,
            student_id=student_id,
            reason=reason,
            on_complete=on_complete,
        ),
        daemon=True,
    )
    thread.start()


def _run_analysis(
    *,
    violation_type: str,
    violation_timestamp: str,
    student_name: str,
    student_id: str,
    reason: str,
    on_complete: Callable[[str, str, str], None],
) -> None:
    try:
        import anthropic  # imported here so the module loads without SDK installed
    except ImportError:
        on_complete("Unavailable", "—", "Anthropic SDK not installed.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        # Try loading from a .env file in the project root
        _try_load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not api_key:
        on_complete(
            "Unavailable",
            "—",
            "ANTHROPIC_API_KEY is not set. Set the environment variable to enable AI analysis.",
        )
        return

    user_message = (
        f"Violation type: {violation_type}\n"
        f"Violation recorded: {violation_timestamp}\n"
        f"Student: {student_name} (ID: {student_id})\n\n"
        f"Student's appeal reasoning:\n{reason}"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()
        data = json.loads(raw)
        recommendation = data.get("recommendation", "Unavailable")
        confidence = data.get("confidence", "—")
        summary = data.get("summary", "")
        factors = data.get("key_factors", [])
        full_summary = summary
        if factors:
            full_summary += "\n\nKey factors:\n" + "\n".join(f"• {f}" for f in factors)
        on_complete(recommendation, confidence, full_summary)
    except json.JSONDecodeError:
        on_complete("Unavailable", "—", f"AI returned unexpected format: {raw[:200]}")
    except Exception as exc:
        on_complete("Unavailable", "—", f"Analysis error: {exc}")


def _try_load_dotenv() -> None:
    try:
        from pathlib import Path
        import importlib
        dotenv = importlib.import_module("dotenv")
        root = Path(__file__).resolve().parent.parent
        dotenv.load_dotenv(root / ".env", override=False)
    except Exception:
        pass
