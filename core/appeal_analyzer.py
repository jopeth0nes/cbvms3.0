"""Appeal analysis for CBVMS.

Two engines, tried in order:
1. Anthropic Claude API  — best quality, requires API key with credits.
2. Local rule-based engine — free, offline, no dependencies.

The local engine scores the student's reasoning against violation-specific
keyword lists, penalises vagueness/brevity, and returns the same structured
output as the Claude engine so the rest of the app is unaffected.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "smtp_config.json"

# ---------------------------------------------------------------------------
# Violation-specific keyword tables for the local engine
# ---------------------------------------------------------------------------

# Each entry: (keywords, score_delta, factor_label)
# Positive score → supports a "Valid" recommendation.
# Negative score → supports "Invalid".

_RULES: dict[str, list[tuple[list[str], int, str]]] = {
    "wrong_uniform": [
        (["wearing uniform", "in uniform", "wearing my uniform", "i wore", "i was wearing",
          "naka uniform", "naka-uniform", "suot ko", "suot ang"], +3,
         "Student claims to be wearing the prescribed uniform"),
        (["damaged", "torn", "broken", "repair", "tailor", "washing", "laundry",
          "pinapalaba", "sira", "nasira"], +2,
         "Plausible uniform maintenance / damage reason"),
        (["wrong person", "not me", "ibang tao", "hindi ako yan", "hindi ako",
          "misidentified", "false detection", "camera error"], +2,
         "Student disputes the identity of the detected person"),
        (["medical", "doctor", "hospital", "prescription", "allergy", "skin"], +2,
         "Medical reason provided"),
        (["event", "activity", "field trip", "intramurals", "school event",
          "sports", "pe", "physical education"], +1,
         "School event / PE exemption cited"),
        (["forgot", "di ko alam", "i don't know", "i didn't know", "excuse",
          "wala akong"], -2,
         "Vague or unsupported excuse"),
        (["not my fault", "bakit ako", "unfair"], -1,
         "Complaint without explanation"),
    ],
    "with_earring": [
        (["medical", "doctor", "healing", "keloid", "infection", "not removable",
          "cannot remove", "hindi matanggal"], +3,
         "Medical reason for not removing earring"),
        (["religious", "cultural", "tradition", "faith", "belief"], +2,
         "Religious or cultural exemption claimed"),
        (["not wearing", "hindi ako", "wrong person", "not me", "ibang tao",
          "misidentified"], +2,
         "Student disputes the detection"),
        (["forgot", "style", "fashion", "gusto ko", "i like"], -2,
         "Fashion / personal preference — not a valid exemption"),
    ],
    "earring_violation": [  # alias
        (["medical", "doctor", "healing", "keloid", "infection", "not removable"], +3,
         "Medical reason provided"),
        (["religious", "cultural", "tradition"], +2,
         "Religious / cultural reason"),
        (["not wearing", "hindi ako", "wrong person", "not me"], +2,
         "Student disputes the detection"),
        (["forgot", "style", "fashion", "i like"], -2,
         "Fashion preference is not a valid reason"),
    ],
    "unknown_person": [
        (["visitor", "guest", "appointment", "invited", "parent", "guardian",
          "teacher", "staff", "authorized", "clearance", "pass"], +3,
         "Authorized visitor status claimed"),
        (["enrolled", "student", "i am a student", "i study here", "my id",
          "student id", "registration"], +2,
         "Claims to be an enrolled student"),
        (["camera", "angle", "lighting", "not recognized", "hindi nakilala",
          "system error"], +1,
         "Technical detection error cited"),
        (["just passing", "random", "wala lang"], -2,
         "No legitimate reason for presence"),
    ],
    "no_id_badge": [
        (["lost", "nawala", "replacement", "applying", "processing", "stolen",
          "hindi ko mahanap"], +3,
         "ID lost or under replacement"),
        (["left at home", "nakalimutan", "forgot at home", "nasa bahay"], +2,
         "ID left at home (minor but common)"),
        (["not issued", "hindi pa naiisyu", "new student", "waiting"], +2,
         "ID not yet issued by school"),
        (["forgot", "di ko dala"], -1,
         "Simple forgetfulness — no supporting context"),
    ],
    "wrong_hair_color": [
        (["natural", "original", "hindi tinina", "di tinina", "not dyed",
          "born with", "likas"], +3,
         "Claim of natural hair color"),
        (["medical", "doctor", "alopecia", "treatment", "chemotherapy",
          "prescription"], +3,
         "Medical reason for hair condition"),
        (["fading", "old dye", "growing out", "hindi pa pala-on"], +1,
         "Old dye growing out — plausible transitional state"),
        (["fashion", "i like", "gusto ko", "aesthetic", "trend"], -3,
         "Fashion choice — violates school policy"),
    ],
}

# Stable discipline codes used by the strike ledger. Keep the older classifier
# labels above as aliases so historical appeals and new submissions receive the
# same advisory analysis.
_RULES["earring"] = _RULES["with_earring"]
_RULES["missing_id"] = _RULES["no_id_badge"]
_RULES["improper_hair"] = _RULES["wrong_hair_color"]

# Generic rules applied to every violation type
_GENERIC_RULES: list[tuple[list[str], int, str]] = [
    (["proof", "evidence", "receipt", "certificate", "letter", "note",
      "photo", "picture", "attached"], +1,
     "Student mentions supporting evidence"),
    (["i am sorry", "pasensya", "next time", "won't happen again",
      "magbabago"], 0,
     "Student expresses remorse"),  # neutral – doesn't validate the appeal
    (["test", "asd", "asdf", "lorem", "sample", "blah", "xxx"], -3,
     "Reasoning appears to be placeholder / test text"),
]


# ---------------------------------------------------------------------------
# Local rule-based engine
# ---------------------------------------------------------------------------

def _local_analyze(violation_type: str, reason: str) -> tuple[str, str, str]:
    """Score the appeal locally and return (recommendation, confidence, summary)."""
    text = reason.lower()
    vtype = (violation_type or "").lower().replace(" ", "_")

    score = 0
    fired_factors: list[str] = []

    rules = _RULES.get(vtype, [])
    for keywords, delta, label in rules + _GENERIC_RULES:
        if any(kw in text for kw in keywords):
            score += delta
            fired_factors.append(label)

    # Length bonus: a detailed explanation earns a small boost
    word_count = len(text.split())
    if word_count >= 40:
        score += 2
        fired_factors.append("Detailed explanation provided")
    elif word_count >= 20:
        score += 1
        fired_factors.append("Reasonably detailed explanation")
    elif word_count < 10:
        score -= 2
        fired_factors.append("Very brief reasoning — lacks detail")

    # Specificity bonus: first-person and dates/numbers suggest authenticity
    if re.search(r"\b(i|ako|kami)\b", text):
        score += 1
    date_pattern = (r"\b\d{1,2}[/-]\d{1,2}"
                    r"|\b(january|february|march|april|may|june|"
                    r"july|august|september|october|november|december)\b")
    if re.search(date_pattern, text):
        score += 1
        fired_factors.append("Specific date or event mentioned")

    # Map score to recommendation + confidence
    if score >= 4:
        recommendation = "Recommended Valid"
        confidence = "High"
        verdict_text = (
            "The student's explanation is detailed and addresses the violation directly. "
            "The reasoning contains specific, plausible factors that support the appeal."
        )
    elif score >= 2:
        recommendation = "Recommended Valid"
        confidence = "Medium"
        verdict_text = (
            "The student has provided a reasonable explanation, though some details "
            "could be verified further. The appeal has moderate supporting evidence."
        )
    elif score >= 0:
        recommendation = "Recommended Invalid"
        confidence = "Low"
        verdict_text = (
            "The student's reasoning is present but lacks sufficient specificity or "
            "strong justification. The appeal is borderline — admin review is advised."
        )
    elif score >= -2:
        recommendation = "Recommended Invalid"
        confidence = "Medium"
        verdict_text = (
            "The explanation does not adequately address the violation. "
            "The reasoning appears vague or does not justify the non-compliance."
        )
    else:
        recommendation = "Recommended Invalid"
        confidence = "High"
        verdict_text = (
            "The appeal contains weak or invalid reasoning. "
            "The student has not provided a credible justification for the violation."
        )

    return recommendation, confidence, verdict_text


# ---------------------------------------------------------------------------
# Anthropic Claude engine
# ---------------------------------------------------------------------------

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
    "1. Is the explanation specific and plausible?\n"
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


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    _try_load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            return data.get("anthropic_api_key", "").strip()
    except Exception:
        pass
    return ""


def _try_load_dotenv() -> None:
    try:
        import importlib
        dotenv = importlib.import_module("dotenv")
        root = Path(__file__).resolve().parent.parent
        dotenv.load_dotenv(root / ".env", override=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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

    Tries Anthropic Claude first; falls back to the local rule engine if the
    API key is absent, has no credits, or the call fails for any reason.
    on_complete(recommendation, confidence, summary) is called from the thread.
    """
    threading.Thread(
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
    ).start()


def _run_analysis(
    *,
    violation_type: str,
    violation_timestamp: str,
    student_name: str,
    student_id: str,
    reason: str,
    on_complete: Callable[[str, str, str], None],
) -> None:
    api_key = _load_api_key()

    # Try Anthropic Claude if an API key is configured
    if api_key:
        try:
            import anthropic
            user_message = (
                f"Violation type: {violation_type}\n"
                f"Violation recorded: {violation_timestamp}\n"
                f"Student: {student_name} (ID: {student_id})\n\n"
                f"Student's appeal reasoning:\n{reason}"
            )
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=[{"type": "text", "text": _SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip()
            data = json.loads(raw)
            rec = data.get("recommendation", "Unavailable")
            conf = data.get("confidence", "—")
            summary = data.get("summary", "")
            factors = data.get("key_factors", [])
            if factors:
                summary += "\n\nKey factors:\n" + "\n".join(f"• {f}" for f in factors)
            on_complete(rec, conf, summary)
            return
        except ImportError:
            pass  # SDK not installed — fall through to local engine
        except Exception:
            pass  # API error (credits, network, etc.) — fall through

    # Free local rule-based engine (always available)
    rec, conf, summary = _local_analyze(violation_type, reason)
    on_complete(rec, conf, summary)
