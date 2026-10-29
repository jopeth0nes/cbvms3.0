"""Live presence cards driven only by the monitor's authoritative assessments.

The pure model owns display history, never attendance, disciplinary records, or
cooldowns. Clearing this list cannot affect persistence or notification decisions.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import time
from typing import Iterable, Mapping
from core.attendance import MANILA

import customtkinter as ctk
from PIL import Image, ImageTk
from core.attendance import MANILA

from ui.activity_dismissals import ActivityDismissals

from ui.components import (
    COLOR_ACCENT, COLOR_BG, COLOR_BORDER, COLOR_DANGER, COLOR_SAFE,
    COLOR_SURFACE, COLOR_TEXT, COLOR_TEXT_MUTED, COLOR_WARNING, CORNER_RADIUS,
    body_font, body_small_font,
)

_STATE_LABELS = {
    "identifying": ("Identifying", COLOR_TEXT_MUTED),
    "locating_torso": ("Locating torso", COLOR_TEXT_MUTED),
    "identity_uncertain": ("Identity uncertain", COLOR_WARNING),
    "checking_uniform": ("Checking uniform", COLOR_ACCENT),
    "uniform_compliant": ("Uniform compliant", COLOR_SAFE),
    "suspected_uniform_violation": ("Suspected uniform violation", COLOR_DANGER),
    "uniform_not_assessed": ("Uniform not assessed", COLOR_TEXT_MUTED),
    "enrollment_review": ("Enrollment review", "#C778DD"),
    "unknown_person": ("Unknown person", COLOR_WARNING),
}


def state_key(value) -> str:
    value = getattr(value, "value", value)
    return str(value or "identifying").strip().lower().replace(" ", "_")


def assessment_style(row: Mapping) -> tuple[str, str]:
    """Shared text/color for cards and annotations; absence never means compliance."""
    key = state_key(row.get("state", row.get("assessment")))
    label, color = _STATE_LABELS.get(key, ("Uniform not assessed", COLOR_TEXT_MUTED))
    if row.get("accepted_categories") or row.get("suspension_tag"):
        color = COLOR_DANGER
    return label, color


def presence_key(row: Mapping) -> str:
    """Unknown faces require the same stable per-presence key as known faces."""
    if row.get("presence_id") is not None:
        return str(row["presence_id"])
    if row.get("track_id") is not None:
        return f"{row.get('generation', 0)}:track:{row['track_id']}"
    raise ValueError("Live assessment needs a presence_id or track_id")


def passage_association_key(row: Mapping) -> str | None:
    """Full same-session passage provenance; tracker IDs alone never identify a card."""
    required = ("monitor_session_id", "source_id", "camera_generation",
                "monitor_generation", "presence_id", "student_id")
    values = [row.get(key) for key in required]
    if any(value in (None, "") for value in values):
        return None
    return hashlib.sha256(repr(tuple(str(value) for value in values)).encode()).hexdigest()


def passage_identity_sequence(row: Mapping):
    """Decode the processor's generation-scoped monotonic presence sequence."""
    scope = (str(row.get("source_id") or ""), row.get("camera_generation"),
             row.get("monitor_generation"))
    parts = str(row.get("presence_id") or "").split(":")
    try:
        if (not scope[0] or scope[1] is None or scope[2] is None
                or len(parts) != 3 or parts[0] != "person"
                or int(parts[1]) != int(scope[2])):
            return None
        sequence = int(parts[2])
        return (scope, sequence) if sequence >= 0 else None
    except (TypeError, ValueError):
        return None


def _activity_timestamp(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _activity_time_label(value) -> str:
    try:
        if isinstance(value, (float, int)):
            parsed = datetime.fromtimestamp(float(value), timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(MANILA).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "Time unavailable"


@dataclass(frozen=True)
class AlertRow:
    key: str
    track_id: str
    name: str
    student_id: str
    gender: str
    student_status: str
    state: str
    detail: str
    suspension_tag: str
    accepted_categories: tuple[str, ...]
    observed_at: float
    active: bool = True

    @classmethod
    def from_assessment(cls, row: Mapping, now: float) -> "AlertRow":
        categories = row.get("accepted_categories") or ()
        if isinstance(categories, str):
            categories = (categories,)
        observed = row.get("observed_at")
        return cls(
            key=presence_key(row), track_id=str(row.get("track_id", presence_key(row))),
            name=str(row.get("name") or "Unidentified person"),
            student_id=str(row.get("student_id") or "—"), gender=str(row.get("gender") or "—"),
            student_status=str(row.get("student_status") or ""),
            state=state_key(row.get("state", row.get("assessment"))),
            detail=str(row.get("detail") or ""),
            suspension_tag=str(row.get("suspension_tag") or ""),
            accepted_categories=tuple(str(c) for c in categories),
            observed_at=float(observed if observed is not None else now),
        )

    def fingerprint(self) -> tuple:
        # New camera pixels, timestamps, and fluctuating confidences do not undo Clear.
        return (self.name, self.student_id, self.gender, self.student_status,
                self.state, self.detail, self.suspension_tag, self.accepted_categories)

    def presentation(self) -> tuple[str, str, str, str, str, str]:
        label, color = assessment_style(vars(self))
        stamp = datetime.fromtimestamp(self.observed_at).strftime("%m-%d %H:%M:%S")
        presence = f"{'Current presence' if self.active else 'Earlier presence'} · {stamp}"
        info = f"ID: {self.student_id}   Gender: {self.gender}"
        if self.student_status:
            info += f"\n{self.student_status}"
        detail = self.detail
        if self.accepted_categories:
            categories = ", ".join(category.replace("_", " ") for category in self.accepted_categories)
            detail = f"{detail}\nAssessment: {categories}" if detail else categories
        return (f"[T{self.track_id}] {self.name}", presence, info, label, detail, color)


class LiveAlertsModel:
    """Bounded, insertion-ordered snapshots, with display-only dismissal semantics."""

    def __init__(self, max_cards: int = 50) -> None:
        self.max_cards = max(1, max_cards)
        self.rows: OrderedDict[str, AlertRow] = OrderedDict()
        self._dismissed: dict[str, tuple] = {}

    def update_assessments(self, assessments: Iterable[Mapping], now: float | None = None) -> None:
        now = time.time() if now is None else now
        incoming = {row.key: row for row in
                    (AlertRow.from_assessment(value, now) for value in assessments)}
        # Leaving and returning is a new appearance even if the tracker reuses the ID.
        self._dismissed = {key: value for key, value in self._dismissed.items() if key in incoming}
        for key, row in tuple(self.rows.items()):
            if key not in incoming and row.active:
                self.rows[key] = replace(row, active=False)
        for key, row in incoming.items():
            if self._dismissed.get(key) == row.fingerprint():
                continue
            self._dismissed.pop(key, None)
            self.rows[key] = row
        while len(self.rows) > self.max_cards:
            oldest = next((key for key, row in self.rows.items() if not row.active), next(iter(self.rows)))
            del self.rows[oldest]

    def mark_all_inactive(self) -> None:
        self.rows = OrderedDict((key, replace(row, active=False)) for key, row in self.rows.items())
        self._dismissed.clear()

    def clear(self) -> None:
        self._dismissed.update((key, row.fingerprint()) for key, row in self.rows.items() if row.active)
        self.rows.clear()


class _PresenceCard(ctk.CTkFrame):
    def __init__(self, master) -> None:
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                         border_width=1, border_color=COLOR_BORDER)
        self.grid_columnconfigure(0, weight=1)
        self._labels = []
        self._presentation = None
        self._wrap = 0
        for index in range(6):
            label = ctk.CTkLabel(
                self, text="", anchor="w", justify="left", wraplength=180,
                font=body_font(13) if index == 0 else body_small_font(),
                text_color=COLOR_TEXT if index == 0 else COLOR_TEXT_MUTED,
            )
            label.grid(row=index, column=0, sticky="ew", padx=10,
                       pady=(8 if index == 0 else 0, 8 if index == 5 else 3))
            self._labels.append(label)
        self.bind("<Configure>", self._resize, add="+")

    def _resize(self, event) -> None:
        width = max(40, event.width - 24)
        if width != self._wrap:
            self._wrap = width
            for label in self._labels:
                label.configure(wraplength=width)

    def update_assessment(self, row: AlertRow) -> None:
        values = row.presentation() + (row.suspension_tag,)
        if self._presentation == values:
            return
        name, presence, info, label, detail, color, suspension = values
        for widget, text in zip(self._labels, (name, presence, info, label, detail, suspension)):
            if widget.cget("text") != text:
                widget.configure(text=text)
            if text:
                widget.grid()
            else:
                widget.grid_remove()
        self._labels[3].configure(text_color=color)
        self._labels[5].configure(text_color=COLOR_DANGER)
        self.configure(border_color=color if row.active else COLOR_BORDER)
        self._presentation = values


class LiveAlerts(ctk.CTkScrollableFrame):
    """Tk-thread-only sidebar: stable widgets, wrapped text, no forced scrolling."""

    def __init__(self, master, *, max_cards: int = 50, **kwargs) -> None:
        kwargs.setdefault("fg_color", COLOR_BG)
        kwargs.setdefault("corner_radius", CORNER_RADIUS)
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.model = LiveAlertsModel(max_cards)
        self._cards: dict[str, _PresenceCard] = {}
        self._unknown_cards: dict[int, _UnknownSightingCard] = {}
        self._unknown_rows: dict[int, Mapping] = {}
        self._monitor_session_id = None
        self._session_mode = False
        self._live_session_rows: OrderedDict[str, dict] = OrderedDict()
        self._persisted_session_rows: OrderedDict[str, dict] = OrderedDict()
        self._activity_cards: dict[str, _SessionActivityCard] = {}
        self._session_dismissals = ActivityDismissals()
        self._current_monitor_generation = None
        self.on_open_activity = None
        self.on_open_unknown = None
        self._order: tuple[tuple[str, object], ...] = ()
        self._empty = ctk.CTkLabel(self, text="No confirmed activity yet", font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._empty.grid(row=0, column=0, pady=20)
        self._empty_text = "No confirmed activity yet"

    def show_message(self, text: str) -> None:
        self._empty_text = text
        has_rows = (bool(self._live_session_rows or self._persisted_session_rows)
                    if getattr(self, "_session_mode", False)
                    else bool(self.model.rows or self._unknown_rows))
        if not has_rows:
            self._empty.configure(text=text)
            self._empty.grid()

    def update_assessments(self, assessments: Iterable[Mapping], now: float | None = None) -> None:
        # Activity cards describe confirmed identities and alerts only. Transient
        # faces remain visible in the camera status/overlay, never as history.
        confirmed = (row for row in assessments
            if row.get("reliable_identity", True)
            and state_key(row.get("state")) not in
            ("identifying", "identity_uncertain", "unknown_person")
            and str(row.get("student_id") or "—") not in ("", "—")
            and str(row.get("name") or "") not in
            ("Identifying", "Identity uncertain", "Unidentified person", "Unknown"))
        self.model.update_assessments(confirmed, now)
        self._refresh()

    def mark_all_inactive(self) -> None:
        self.model.mark_all_inactive()
        for row in self._live_session_rows.values():
            row["active"] = False
        self._refresh()

    def clear(self) -> None:
        self.model.clear()
        self._refresh()

    def update_unknown_sightings(self, rows: Iterable[Mapping]) -> None:
        """Show a bounded snapshot of persisted Unknown Logs; IDs own card identity."""
        incoming = {}
        limit = max(0, self.model.max_cards - len(self.model.rows))
        for row in list(rows)[:limit]:
            try:
                event_id = int(row['id'])
            except (KeyError, TypeError, ValueError):
                continue
            incoming[event_id] = dict(row)
        self._unknown_rows = incoming
        for event_id in self._unknown_cards.keys() - incoming.keys():
            self._unknown_cards.pop(event_id).destroy()
        self._refresh()

    def set_monitor_session(self, monitor_session_id: str) -> None:
        if monitor_session_id == self._monitor_session_id:
            return
        self._monitor_session_id = monitor_session_id
        self._session_mode = True
        self.model.clear()
        self._unknown_rows.clear()
        self._live_session_rows.clear()
        self._persisted_session_rows.clear()
        self._session_dismissals.close()
        self._session_dismissals = ActivityDismissals()
        self._current_monitor_generation = None
        for card in self._activity_cards.values():
            card.destroy()
        self._activity_cards.clear()
        self._refresh_session_activity()

    def set_activity_opener(self, callback) -> None:
        self.on_open_activity = callback

    def note_monitor_generation(self, generation: int) -> None:
        self._current_monitor_generation = int(generation)

    def update_session_assessments(self, assessments: Iterable[Mapping], thumbnails=None,
                                   monitor_generation: int | None = None) -> None:
        if not self._monitor_session_id:
            return
        if monitor_generation is not None:
            self._current_monitor_generation = int(monitor_generation)
        matched = {row.get("association_key") for row in self._persisted_session_rows.values()
                   if row.get("association_key")}
        incoming = {}
        for source in assessments:
            if (source.get("monitor_session_id") != self._monitor_session_id
                    or not source.get("reliable_identity") or not source.get("student_id")
                    or state_key(source.get("state")) in
                    ("identifying", "identity_uncertain", "unknown_person")):
                continue
            association = passage_association_key(source)
            if (association is None or association in matched
                    or self._session_dismissals.contains("passage:" + association, "live-retired:" + association)):
                continue
            key = "live:" + association
            incoming[key] = dict(source, event_type="live", event_id=None,
                activity_key=key, association_key=association, action_label="Log not saved",
                thumbnail=(thumbnails or {}).get(association), active=True,
                preview_kind="Live assessment · no saved log")
        for key, row in tuple(self._live_session_rows.items()):
            if row.get("association_key") in matched:
                del self._live_session_rows[key]
            elif key not in incoming:
                row["active"] = False
        self._live_session_rows.update(incoming)
        self._trim_session_rows()
        self._refresh_session_activity()

    def update_persisted_session_activity(self, records: Iterable[Mapping]) -> None:
        if not self._monitor_session_id:
            return
        incoming = {}
        for source in records:
            if source.get("monitor_session_id") != self._monitor_session_id:
                continue
            kind = str(source.get("event_type") or "")
            if kind not in ("attendance", "violation", "unknown"):
                continue
            try:
                event_id = int(source["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if event_id <= 0:
                continue
            key = f"{kind}:{event_id}"
            association = passage_association_key(source)
            # Passage dismissal is created only from a displayed UNSAVED card.
            # Clearing a saved attendance event does not dismiss a distinct future
            # violation merely because both belong to the same student/passage.
            if self._session_dismissals.contains(key,
                    "passage:" + association if association else None):
                continue
            incoming[key] = dict(source, activity_key=key, event_id=event_id,
                association_key=association, thumbnail=source.get("thumbnail"),
                action_label={"attendance": "Open attendance log", "violation": "Open violation",
                              "unknown": "Open Unknown Logs"}[kind])
        self._persisted_session_rows = OrderedDict(incoming)
        matched = {row.get("association_key") for row in incoming.values() if row.get("association_key")}
        self._session_dismissals.add("live-retired:" + value for value in matched)
        for key, row in tuple(self._live_session_rows.items()):
            if row.get("association_key") in matched:
                self._live_session_rows.pop(key, None)
        self._trim_session_rows()
        self._refresh_session_activity()

    def clear_session_activity(self) -> None:
        keys = [row["activity_key"] for row in self._persisted_session_rows.values()]
        # Saved cards retire their live representation, but do not suppress other
        # saved event types. Unsaved cards also suppress their eventual write.
        keys.extend("passage:" + row["association_key"]
                    for row in self._live_session_rows.values() if row.get("association_key"))
        keys.extend("live-retired:" + row["association_key"]
                    for row in self._persisted_session_rows.values() if row.get("association_key"))
        self._session_dismissals.add(keys)
        self._live_session_rows.clear()
        self._persisted_session_rows.clear()
        self._refresh_session_activity()

    def destroy(self):
        self._session_dismissals.close()
        super().destroy()

    def _trim_session_rows(self) -> None:
        while len(self._live_session_rows) + len(self._persisted_session_rows) > self.model.max_cards:
            if self._live_session_rows:
                self._live_session_rows.popitem(last=False)
            elif self._persisted_session_rows:
                self._persisted_session_rows.popitem(last=False)

    def _refresh_session_activity(self) -> None:
        rows = list(self._live_session_rows.values()) + list(self._persisted_session_rows.values())
        rows.sort(key=lambda row: _activity_timestamp(row.get("observed_at")), reverse=True)
        desired = {row["activity_key"]: row for row in rows[:self.model.max_cards]}
        for key in self._activity_cards.keys() - desired.keys():
            self._activity_cards.pop(key).destroy()
        for index, (key, row) in enumerate(desired.items()):
            card = self._activity_cards.get(key)
            if card is None:
                card = self._activity_cards[key] = _SessionActivityCard(
                    self, self._open_activity)
            card.update_activity(row)
            card.grid(row=index, column=0, sticky="ew", pady=(0, 6))
        self._empty.configure(text=self._empty_text)
        if desired:
            self._empty.grid_remove()
        else:
            self._empty.grid()

    def _open_activity(self, row: Mapping) -> None:
        if self.on_open_activity is not None and row.get("event_id") is not None:
            self.on_open_activity(row["event_type"], row["event_id"], row)

    def _refresh(self) -> None:
        if getattr(self, "_session_mode", False):
            self._refresh_session_activity()
            return
        for key in self._cards.keys() - self.model.rows.keys():
            self._cards.pop(key).destroy()
        order = tuple(self.model.rows)
        unknown_rows = getattr(self, "_unknown_rows", {})
        unknown_cards = getattr(self, "_unknown_cards", {})
        all_order = tuple(("known", key) for key in order) + tuple(
            ("unknown", event_id) for event_id in unknown_rows)
        if all_order:
            self._empty.grid_remove()
        else:
            self._empty.configure(text=self._empty_text)
            self._empty.grid()
        for index, (key, row) in enumerate(self.model.rows.items()):
            card = self._cards.get(key)
            if card is None:
                card = self._cards[key] = _PresenceCard(self)
            card.update_assessment(row)
        if all_order != self._order:
            for index, (kind, key) in enumerate(all_order):
                card = (self._cards[key] if kind == "known"
                        else unknown_cards.get(key))
                if kind == "unknown":
                    row = unknown_rows[key]
                    if card is None:
                        card = unknown_cards[key] = _UnknownSightingCard(
                            self, key, self._open_unknown)
                    card.update_sighting(row)
                card.grid(row=index, column=0, sticky="ew", pady=(0, 6))
        else:
            for key, row in unknown_rows.items():
                unknown_cards[key].update_sighting(row)
        self._order = all_order

    def _open_unknown(self, event_id: int, reference_id: str) -> None:
        if self.on_open_unknown is not None:
            self.on_open_unknown(event_id, reference_id)


class _UnknownSightingCard(ctk.CTkFrame):
    """Compact presentation of one already-persisted, authorized unknown event."""

    def __init__(self, master, event_id: int, on_open) -> None:
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                         border_width=1, border_color=COLOR_BORDER)
        self.event_id, self.on_open = event_id, on_open
        self.grid_columnconfigure(0, weight=1)
        self._photo = None
        self._signature = None
        self._title = ctk.CTkLabel(self, text="Unknown person", anchor="w",
                                   font=body_font(13), text_color=COLOR_TEXT)
        self._title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 3))
        self._details = ctk.CTkLabel(self, text="", anchor="w", justify="left",
                                     wraplength=220, font=body_small_font(),
                                     text_color=COLOR_TEXT_MUTED)
        self._details.grid(row=1, column=0, sticky="ew", padx=10, pady=3)
        self._image = ctk.CTkLabel(self, text="Evidence unavailable", anchor="w",
                                   text_color=COLOR_TEXT_MUTED)
        self._image.grid(row=2, column=0, sticky="w", padx=10, pady=3)
        self._reference_id = ""
        self._open = ctk.CTkButton(self, text="Open Unknown Logs record", height=28,
                                   command=self._open_record)
        self._open.grid(row=3, column=0, sticky="ew", padx=10, pady=(3, 8))

    def update_sighting(self, row: Mapping) -> None:
        stamp = _activity_time_label(row.get("observed_at"))
        source = str(row.get("source_label") or "Camera not recorded")
        self._reference_id = str(row.get("reference_id") or self.event_id)
        self._details.configure(text=f"{stamp} · {source}")
        data = row.get("thumbnail")
        if data is None:
            signature = None
        elif hasattr(data, "size") and hasattr(data, "mode"):
            signature = (data.size, data.mode, hashlib.sha256(data.tobytes()).digest())
        else:
            signature = hashlib.sha256(bytes(data)).digest()
        if signature == self._signature:
            return
        self._signature = signature
        self._photo = None
        if signature is None:
            self._image.configure(image=None, text="Evidence unavailable")
            return
        try:
            if isinstance(data, Image.Image):
                image = data.copy()
            else:
                image = Image.open(BytesIO(data)).convert("RGB")
            image.thumbnail((160, 100))
            self._photo = ImageTk.PhotoImage(image)
            self._image.configure(image=self._photo, text="")
        except Exception:
            self._image.configure(image=None, text="Evidence unavailable")

    def _open_record(self) -> None:
        self.on_open(self.event_id, self._reference_id)


class _SessionActivityCard(ctk.CTkFrame):
    """One current-session event or live passage; display only, exact IDs navigate."""

    def __init__(self, master, on_open) -> None:
        super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                         border_width=1, border_color=COLOR_BORDER)
        self.grid_columnconfigure(0, weight=1)
        self._on_open, self._row = on_open, None
        self._photo = None
        self._signature = None
        self._title = ctk.CTkLabel(self, text="", width=1, wraplength=180, justify="left", anchor="w", font=body_font(13), text_color=COLOR_TEXT)
        self._title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 3))
        self._details = ctk.CTkLabel(self, text="", width=1, anchor="w", justify="left", wraplength=180,
                                     font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._details.grid(row=1, column=0, sticky="ew", padx=10, pady=3)
        self._student_id = ctk.CTkLabel(self, text="", width=1, anchor="w", wraplength=180,
                                        font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._student_id.grid(row=2, column=0, sticky="ew", padx=10, pady=3)
        self._image = ctk.CTkLabel(self, text="Photo unavailable", anchor="w", text_color=COLOR_TEXT_MUTED)
        self._image.grid(row=3, column=0, sticky="w", padx=10, pady=3)
        self._action = ctk.CTkButton(self, text="", width=1, height=28, font=body_small_font(), command=self._open)
        self._action.grid(row=4, column=0, sticky="ew", padx=10, pady=(3, 8))
        self.bind("<Configure>", self._resize, add="+")

    def _resize(self, event) -> None:
        width = max(100, event.width - 24)
        self._title.configure(wraplength=width)
        self._details.configure(wraplength=width)
        self._student_id.configure(wraplength=width)

    def update_activity(self, row: Mapping) -> None:
        self._row = dict(row)
        kind = str(row.get("event_type") or "activity")
        title = str(row.get("student_name") or row.get("name") or
                    ("Unknown person" if kind == "unknown" else "Current presence"))
        try:
            stamp = datetime.fromtimestamp(_activity_timestamp(row.get("observed_at")), MANILA).strftime("%m-%d %H:%M:%S (UTC+08)")
        except (ValueError, OverflowError, OSError):
            stamp = "Time unavailable"
        source = str(row.get("source_label") or "Camera not recorded")
        status = str(row.get("status") or row.get("violation_type") or row.get("state") or "")
        details = " · ".join(part for part in (stamp, source, status) if part)
        preview_kind = str(row.get("preview_kind") or "")
        if preview_kind:
            details += f"\n{preview_kind}"
        self._title.configure(text=title)
        self._details.configure(text=details)
        student_id = str(row.get("student_id") or "")
        self._student_id.configure(text=f"ID: {student_id}" if student_id else "")
        if student_id:
            self._student_id.grid()
        else:
            self._student_id.grid_remove()
        self.configure(border_color=COLOR_ACCENT if kind == "live" else COLOR_BORDER)
        self._action.configure(text=str(row.get("action_label") or "Open log"),
                               state="normal" if row.get("event_id") is not None else "disabled")
        data = row.get("thumbnail")
        if isinstance(data, Image.Image):
            signature = (data.size, data.mode, hashlib.sha256(data.tobytes()).digest())
        else:
            signature = hashlib.sha256(bytes(data)).digest() if data else None
        if signature == self._signature:
            return
        self._signature, self._photo = signature, None
        if not data:
            self._image.configure(image=None, text="Photo unavailable")
            return
        try:
            if not isinstance(data, Image.Image):
                raise TypeError("UI accepts only a background-decoded thumbnail")
            image = data.copy()
            image.thumbnail((160, 100))
            self._photo = ImageTk.PhotoImage(image)
            self._image.configure(image=self._photo, text="")
        except Exception:
            self._image.configure(image=None, text="Photo unavailable")

    def _open(self) -> None:
        if self._row is not None:
            self._on_open(self._row)
