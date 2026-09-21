"""Student standing is independent of suspension and account registration."""
import re

STUDENT_STATUSES = ("Enrolled", "Graduate", "Unenrolled")
CONTACT_FIELDS = (
    ("Email Address", "email"),
    ("Mobile Number", "mobile_number"),
)


def validate_contacts(values):
    cleaned = {key: str(values.get(key) or "").strip() for _, key in CONTACT_FIELDS}
    for label, key in CONTACT_FIELDS:
        value = cleaned[key]
        if len(value) > 200:
            raise ValueError(f"{label} must be 200 characters or fewer.")
        if value and "email" in key and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError(f"Enter a valid {label.lower()}.")
        if value and "mobile" in key:
            digits = re.sub(r"[\s()\-]", "", value)
            if not re.fullmatch(r"\+?[0-9]{7,15}", digits):
                raise ValueError(f"Enter a valid {label.lower()} (7–15 digits).")
    return cleaned


def standing_label(student):
    return "Pending verification" if student.get("registration_pending") else student.get("student_status", "Enrolled")


def suspension_label(suspension):
    if not suspension:
        return "No active suspension"
    return (f"Suspended until {suspension['ends_at']} UTC" if suspension.get("ends_at")
            else "Suspended — indefinite")
