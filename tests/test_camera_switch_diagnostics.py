"""Focused headless checks for camera-switch diagnostic helpers and import safety."""
import importlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts.camera_switch_diagnostics import (
    callback_wrapper, trace_tk_callbacks, valid_result, write_failure_artifact,
)


class CameraSwitchDiagnosticHelperTests(unittest.TestCase):
    def test_valid_result_requires_both_current_generations_and_task_acceptance(self):
        result = types.SimpleNamespace(
            task=types.SimpleNamespace(
                context=types.SimpleNamespace(generation=4, frame_id=("current-frame", 2)), camera_generation=9,
                valid=lambda: True))
        self.assertTrue(valid_result(result, 9, 4))
        self.assertFalse(valid_result(result, 8, 4))
        self.assertFalse(valid_result(result, 9, 3))
        self.assertFalse(valid_result(result, 9, 4, "stale-frame"))
        self.assertTrue(valid_result(result, 9, 4, "current-frame"))
        result.detail = "Assessment unavailable"
        self.assertFalse(valid_result(result, 9, 4, "current-frame"))
        result.detail = "No faces detected"
        self.assertTrue(valid_result(result, 9, 4, "current-frame"))
        result.task.valid = lambda: False
        self.assertFalse(valid_result(result, 9, 4))
        self.assertFalse(valid_result(None, 9, 4))

    def test_failure_artifact_contains_phase_and_error_type_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = write_failure_artifact(Path(tmp), "startup", RuntimeError("secret URL"))
            data = json.loads(artifact.read_text())
            self.assertEqual(data["phase"], "startup")
            self.assertEqual(data["error_kind"], "RuntimeError")
            self.assertNotIn("secret", artifact.read_text())

    def test_tk_tracing_reports_entry_return_and_last_callback(self):
        import tkinter
        events, last = [], [None]
        callback = tkinter.CallWrapper(lambda: 42, None, None)
        with trace_tk_callbacks(lambda event, **fields: events.append((event, fields)), last):
            self.assertEqual(callback(), 42)
        self.assertEqual([name for name, _ in events], ["tk_callback_enter", "tk_callback_return"])
        self.assertIsNotNone(last[0])

    def test_failed_callback_does_not_replace_last_completed(self):
        events, last = [], ["prior"]
        wrapper = callback_wrapper(lambda event, **fields: events.append((event, fields)),
                                   last, lambda: (_ for _ in ()).throw(RuntimeError("failure")))
        with self.assertRaises(RuntimeError):
            wrapper()
        self.assertEqual(last[0], "prior")
        self.assertFalse(events[-1][1]["completed"])

    def test_entry_point_import_does_not_parse_arguments_or_start_diagnostics(self):
        with patch("sys.argv", ["verify_camera_switch.py", "--unknown"]):
            module = importlib.import_module("scripts.verify_camera_switch")
        self.assertTrue(callable(module.main))

    def test_close_failure_trace_keeps_original_exception_and_safe_artifact(self):
        from scripts.verify_camera_switch import _trace
        class Window:
            def _on_close(self):
                raise RuntimeError("secret camera URL")
        with tempfile.TemporaryDirectory() as tmp:
            traced = _trace(lambda *a, **k: None, Window, "_on_close", Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "secret"):
                traced(Window())
            data = (Path(tmp)/"failure.json").read_text()
            self.assertNotIn("secret", data)
            self.assertIn("close", data)


if __name__ == "__main__":
    unittest.main()
