"""Live presence cards driven only by the monitor's authoritative assessments.

The pure model owns display history, never attendance, disciplinary records, or
cooldowns. Clearing this list cannot affect persistence or notification decisions.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime
import time
from typing import Iterable, Mapping

import customtkinter as ctk

from ui.components import (
    COLOR_ACCENT, COLOR_BG, COLOR_BORDER, COLOR_DANGER, COLOR_SAFE,
    COLOR_SURFACE, COLOR_TEXT, COLOR_TEXT_MUTED, COLOR_WARNING, CORNER_RADIUS,
    body_font, body_small_font,
)

_STATE_LABELS = {
    "identifying": ("Identifying", COLOR_TEXT_MUTED),
    "identity_uncertain": ("Identity uncertain", COLOR_WARNING),
    "checking_uniform": ("Checking uniform", COLOR_ACCENT),
    "uniform_compliant": ("Uniform compliant", COLOR_SAFE),
    "suspected_uniform_violation": ("Suspected uniform violation", COLOR_DANGER),
    "uniform_not_assessed": ("Uniform not assessed", COLOR_TEXT_MUTED),
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
        self._order: tuple[str, ...] = ()
        self._empty = ctk.CTkLabel(self, text="No live alerts", font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._empty.grid(row=0, column=0, pady=20)

    def update_assessments(self, assessments: Iterable[Mapping], now: float | None = None) -> None:
        self.model.update_assessments(assessments, now)
        self._refresh()

    def mark_all_inactive(self) -> None:
        self.model.mark_all_inactive()
        self._refresh()

    def clear(self) -> None:
        self.model.clear()
        self._refresh()

    def _refresh(self) -> None:
        for key in self._cards.keys() - self.model.rows.keys():
            self._cards.pop(key).destroy()
        order = tuple(self.model.rows)
        if order:
            self._empty.grid_remove()
        else:
            self._empty.grid()
        for index, (key, row) in enumerate(self.model.rows.items()):
            card = self._cards.get(key)
            if card is None:
                card = self._cards[key] = _PresenceCard(self)
            if order != self._order:
                card.grid(row=index, column=0, sticky="ew", pady=(0, 6))
            card.update_assessment(row)
        self._order = order
