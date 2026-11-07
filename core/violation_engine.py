"""Lightweight live violation checker for CBVMS.

Wraps the in-app ViolationTrainer uniform classifier and turns a
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

    @property
    def check_earring(self) -> bool:
        """Compatibility for saved settings; this retired check cannot be enabled."""
        return False

    @check_earring.setter
    def check_earring(self, _enabled) -> None:
        pass

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

        # Compatibility helper; the live pipeline owns guarded persistence/audio.
        if self._notifier is not None and violations:
            self._notifier.notify(student_name="", violation=", ".join(violations))

        return violations
