"""Focused tests for the live-camera violation persistence boundary."""

from __future__ import annotations

import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from database.db_manager import CBVMSDatabase
from ui.dashboard import CBVMSDashboard


class _ViolationTrainer:
    def is_trained(self, _module: str) -> bool:
        return True

    def predict_proba(self, module: str, _crop: np.ndarray) -> dict[str, float] | None:
        if module == "uniform":
            return {"correct_uniform": 0.2, "wrong_uniform": 0.8}
        return None

    def predict(self, module: str, _crop: np.ndarray) -> tuple[str | None, float]:
        if module == "earring":
            return "with_earring", 0.9
        return None, 0.0


class CameraViolationIntegrationTests(unittest.TestCase):
    def test_log_db_persists_real_pending_workflow_record_for_recognized_student(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cbvms_camera_integration_") as tmp_dir:
            database = CBVMSDatabase(Path(tmp_dir) / "cbvms.db")
            database.initialize()
            database.insert_student(
                student_id="S-001",
                name="Student One",
                course="BSIT",
                year_and_section="3A",
                encoding=b"",
                photo=b"",
                gender="Male",
            )
            notifier = MagicMock()
            harness = types.SimpleNamespace(
                _database=database,
                _notifier=notifier,
                _db_log_cooldowns={},
                _violation_dirty=False,
            )

            with patch("ui.dashboard.time.monotonic", return_value=100.0):
                CBVMSDashboard._log_db(
                    harness,
                    {"student_id": "S-001", "name": "Student One"},
                    np.full((40, 40, 3), 127, dtype=np.uint8),
                    [0, 0, 30, 30],
                    "Wrong uniform (80%)",
                    violation_code="wrong_uniform",
                )

            rows = database.get_violations_for_student("S-001")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["student_id"], "S-001")
            self.assertEqual(rows[0]["status"], "pending_review")
            self.assertEqual(rows[0]["violation_code"], "wrong_uniform")
            self.assertTrue(rows[0]["review_deadline"])
            self.assertIsInstance(rows[0]["snapshot"], bytes)
            self.assertTrue(rows[0]["snapshot"])
            self.assertEqual(database.get_strike_count("S-001", "wrong_uniform"), 0)
            self.assertEqual(database.get_notifications_for_student("S-001"), [])
            with database.connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM student_notifications").fetchone()[0],
                    0,
                )
            notifier.notify.assert_called_once_with("Student One", "Wrong uniform (80%)")
            self.assertTrue(harness._violation_dirty)

    def test_joined_overlay_persists_each_category_with_a_stable_code(self) -> None:
        logged: list[tuple[str, str]] = []
        harness = types.SimpleNamespace(
            _person_detector=types.SimpleNamespace(
                detect_persons=lambda _frame: [],
                chest_region=lambda _frame, _face_box, _person_box: (
                    [5, 35, 55, 90],
                    "test",
                ),
            ),
            _checker=types.SimpleNamespace(check_uniform=True, check_earring=True),
            _trainer=_ViolationTrainer(),
            _uniform_matcher=types.SimpleNamespace(
                is_loaded=lambda: True,
                is_uniform=lambda _crop: (False, 0.2),
            ),
            _uniform_ema={},
            _log_db=lambda _det, _frame, _box, display, violation_code=None: logged.append(
                (violation_code, display)
            ),
        )
        detection = {
            "box": [10, 10, 30, 30],
            "name": "Student One",
            "student_id": "S-001",
            "gender": "Male",
            "matched": True,
        }

        with patch("ui.dashboard.skin_fraction", return_value=0.0):
            CBVMSDashboard._check_violations(
                harness, [detection], np.zeros((100, 100, 3), dtype=np.uint8)
            )

        self.assertEqual(
            detection["violation"],
            "Wrong uniform (80%), Earring detected (90%)",
        )
        self.assertEqual(
            logged,
            [
                ("wrong_uniform", "Wrong uniform (80%)"),
                ("earring", "Earring detected (90%)"),
            ],
        )

    def test_unknown_person_is_persisted_with_non_strike_code(self) -> None:
        logged: list[tuple[str, str]] = []
        harness = types.SimpleNamespace(
            _person_detector=None,
            _checker=types.SimpleNamespace(check_uniform=False, check_earring=False),
            _trainer=types.SimpleNamespace(is_trained=lambda _module: False),
            _uniform_matcher=types.SimpleNamespace(is_loaded=lambda: False),
            _uniform_ema={},
            _log_db=lambda _det, _frame, _box, display, violation_code=None: logged.append(
                (violation_code, display)
            ),
        )
        detection = {
            "box": [10, 10, 30, 30],
            "name": "Unknown",
            "student_id": "",
            "gender": "Unknown",
            "matched": False,
        }

        CBVMSDashboard._check_violations(
            harness, [detection], np.zeros((50, 50, 3), dtype=np.uint8)
        )

        self.assertEqual(logged, [("unknown_person", "unknown_person")])

    def test_log_db_uses_pending_review_and_category_specific_cooldowns(self) -> None:
        database = MagicMock()
        database.log_violation.side_effect = [101, 102]
        notifier = MagicMock()
        harness = types.SimpleNamespace(
            _database=database,
            _notifier=notifier,
            _db_log_cooldowns={},
            _violation_dirty=False,
        )
        detection = {"student_id": "S-001", "name": "Student One"}
        frame = np.zeros((40, 40, 3), dtype=np.uint8)

        with patch("ui.dashboard.time.monotonic", return_value=100.0):
            CBVMSDashboard._log_db(
                harness,
                detection,
                frame,
                [0, 0, 30, 30],
                "Wrong uniform (80%)",
                violation_code="wrong_uniform",
            )
            # Same category remains cooldown-gated.
            CBVMSDashboard._log_db(
                harness,
                detection,
                frame,
                [0, 0, 30, 30],
                "Wrong uniform (85%)",
                violation_code="wrong_uniform",
            )
            # A separate category for the same student must still be recorded.
            CBVMSDashboard._log_db(
                harness,
                detection,
                frame,
                [0, 0, 30, 30],
                "Earring detected (90%)",
                violation_code="earring",
            )

        self.assertEqual(database.log_violation.call_count, 2)
        first = database.log_violation.call_args_list[0].kwargs
        second = database.log_violation.call_args_list[1].kwargs
        self.assertEqual(first["violation_code"], "wrong_uniform")
        self.assertEqual(second["violation_code"], "earring")
        self.assertEqual(first["status"], "pending_review")
        self.assertIsInstance(first["snapshot_jpeg"], bytes)
        self.assertTrue(first["snapshot_jpeg"])
        self.assertEqual(
            set(harness._db_log_cooldowns),
            {"S-001:wrong_uniform", "S-001:earring"},
        )
        self.assertEqual(notifier.notify.call_count, 2)
        self.assertTrue(harness._violation_dirty)

    def test_failed_insert_does_not_start_cooldown(self) -> None:
        database = MagicMock()
        database.log_violation.side_effect = [RuntimeError("database busy"), 201]
        harness = types.SimpleNamespace(
            _database=database,
            _notifier=MagicMock(),
            _db_log_cooldowns={},
            _violation_dirty=False,
        )
        args = (
            harness,
            {"student_id": "S-001", "name": "Student One"},
            np.zeros((20, 20, 3), dtype=np.uint8),
            [0, 0, 20, 20],
            "Wrong uniform (80%)",
        )

        with patch("ui.dashboard.time.monotonic", return_value=100.0):
            with contextlib.redirect_stdout(io.StringIO()):
                CBVMSDashboard._log_db(*args, violation_code="wrong_uniform")
            self.assertNotIn("S-001:wrong_uniform", harness._db_log_cooldowns)
            CBVMSDashboard._log_db(*args, violation_code="wrong_uniform")

        self.assertEqual(database.log_violation.call_count, 2)
        self.assertEqual(harness._db_log_cooldowns["S-001:wrong_uniform"], 100.0)
        harness._notifier.notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
