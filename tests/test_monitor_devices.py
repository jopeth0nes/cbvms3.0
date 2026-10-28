"""Lightweight tests for the device benchmark's report helpers."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_monitor_devices.py"
SPEC = importlib.util.spec_from_file_location("benchmark_monitor_devices", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class MonitorDeviceBenchmarkTests(unittest.TestCase):
    def test_percentiles_are_compact_and_order_independent(self):
        self.assertEqual(benchmark._percentiles([9, 1, 5]),
                         {"n": 3, "p50_ms": 5.0, "p95_ms": 8.6, "p99_ms": 8.92})
        self.assertIsNone(benchmark._percentiles([]))

    def test_device_check_normalizes_torch_device_index(self):
        self.assertTrue(benchmark._device_matches("mps", "mps:0"))
        self.assertTrue(benchmark._device_matches("cpu", "cpu"))
        self.assertFalse(benchmark._device_matches("mps", "cpu"))
        self.assertFalse(benchmark._device_matches("cuda", None))

    def test_unavailable_accelerators_are_not_replaced_by_cpu(self):
        environment = {"torch": {"cuda_available": False, "mps_available": False}}
        self.assertEqual(benchmark._available_devices(environment), ["cpu", "default"])

    def test_default_mode_is_compared_to_observed_cpu_control(self):
        self.assertTrue(benchmark._device_matches("cpu", "cpu:0"))
        self.assertFalse(benchmark._device_matches("cpu", "mps:0"))

    def test_embedding_comparison_reports_only_scalar_metrics(self):
        result = benchmark._embedding_comparison([3.0, 4.0], [6.0, 8.0])
        self.assertEqual(result, {"same_shape": True, "cosine_similarity": 1.0,
                                  "max_abs_normalized_delta": 0.0})
        self.assertIsNone(benchmark._embedding_comparison([0.0], [1.0])["cosine_similarity"])

    def test_synchronize_waits_for_available_accelerators(self):
        calls = []
        torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True,
                                 synchronize=lambda: calls.append("cuda")),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
            mps=SimpleNamespace(synchronize=lambda: calls.append("mps")))
        benchmark._synchronize_accelerators(torch)
        self.assertEqual(calls, ["cuda", "mps"])

    def test_synchronization_failure_cannot_report_valid_timing(self):
        def fail():
            raise RuntimeError("device synchronization failed")
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, synchronize=fail))
        with self.assertRaisesRegex(RuntimeError, "synchronization failed"):
            benchmark._synchronize_accelerators(torch)


if __name__ == "__main__":
    unittest.main()
