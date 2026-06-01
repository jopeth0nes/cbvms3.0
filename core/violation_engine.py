"""Lightweight live violation checker for CBVMS.

Wraps the in-app ViolationTrainer classifiers (uniform / earring) and turns a
per-person face/torso crop into a list of human-readable violation strings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from core.notifier import Notifier
    from core.trainer import ViolationTrainer

# Minimum classifier confidence before a violation is reported.
CONF_THRESHOLD = 0.65


class LiveViolationChecker:
    def __init__(self, trainer: "ViolationTrainer", notifier: "Notifier | None" = None) -> None:
        self._trainer = trainer
        self._notifier = notifier
        # Toggles (match the settings-panel switch semantics).
        self.check_uniform: bool = True
        self.check_earring: bool = True

    def check(
        self,
        face_bgr: np.ndarray | None,
        torso_bgr: np.ndarray | None,
        gender: str = "Unknown",
    ) -> list[str]:
        """Return a list of violation strings for this person. Empty = no violation."""
        violations: list[str] = []

        if self.check_uniform and torso_bgr is not None and torso_bgr.size > 0:
            label, conf = self._trainer.predict("uniform", torso_bgr)
            if label == "wrong_uniform" and conf >= CONF_THRESHOLD:
                violations.append(f"Wrong uniform ({conf:.0%})")

        if (
            self.check_earring
            and face_bgr is not None
            and face_bgr.size > 0
            and (gender or "").lower() == "male"
        ):
            label, conf = self._trainer.predict("earring", face_bgr)
            if label == "with_earring" and conf >= CONF_THRESHOLD:
                violations.append(f"Earring detected ({conf:.0%})")

        # NOTE: check() is currently unused by the live pipeline — ui/dashboard.py
        # predicts inline in _check_violations and fires the notifier from _log_db
        # (which is cooldown-gated). This hook is kept for spec fidelity / future use.
        if self._notifier is not None and violations:
            self._notifier.notify(student_name="", violation=", ".join(violations))

        return violations
