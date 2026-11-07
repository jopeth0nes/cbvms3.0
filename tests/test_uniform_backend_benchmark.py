"""No-model tests for the bounded uniform backend benchmark CLI and aggregation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from scripts import benchmark_uniform_backends as benchmark


class FakePredictor:
    def __init__(self, path, *, backend):
        self.backend = backend
        self.calls = 0

    def timed_probabilities(self, image):
        self.calls += 1
        return {"probabilities": {"campus_uniform": 0.4},
                "timings": {"preprocessing_ms": float(self.calls),
                            "inference_ms": float(self.calls * 2),
                            "total_ms": float(self.calls * 3)},
                "backend": self.backend_status()}

    def backend_status(self):
        return {"requested": self.backend, "active": self.backend,
                "model_parameter_device": self.backend, "fallback_reason": None}


class FakeDetector:
    def __init__(self, *, backend):
        self.backend = backend
        self.calls = 0
        self.last_error = None
        self._model = FakeYolo()

    def _ensure_model(self):
        return self._model

    def detect_persons(self, image):
        self.calls += 1
        self._model.predictor.preprocess(image)
        self._model.predictor.inference(image)
        self._model.predictor.postprocess(image)
        return [[0, 0, 20, 40]] if self.calls % 2 else []

    def backend_status(self):
        return {"requested": self.backend, "active": "cpu" if self.backend == "mps" else "cpu",
                "model_parameter_device": "cpu", "fallback_reason": "test MPS unavailable" if self.backend == "mps" else None}


class FakeYolo:
    def __init__(self):
        self.predictor = self.Predictor()

    class Predictor:
        def preprocess(self, image):
            return image
        def inference(self, image):
            return image
        def postprocess(self, image):
            return image


class BackendBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle = self.root / "candidate-bundle"
        self.bundle.mkdir()
        self.image = np.full((64, 80, 3), 127, dtype=np.uint8)
        self.metadata = [{"sha256": "a" * 64, "width": 80, "height": 64}]

    def tearDown(self):
        self.temp.cleanup()

    def test_both_backends_reuse_same_inputs_and_write_aggregated_output(self):
        events = []
        seen_inputs = []
        def descriptor(path):
            events.append("validated_candidate")
            self.assertEqual(path, self.bundle)
            return {"id": "b" * 64, "preprocessing_version": "fixed-224-v1",
                    "dataset_version": "train-set-3", "synthetic": False}
        def predictor_factory(path, *, backend):
            events.append("predictor:" + backend)
            class RecordingPredictor(FakePredictor):
                def timed_probabilities(self, image):
                    seen_inputs.append((backend, image.shape, int(image[0, 0, 0])))
                    return super().timed_probabilities(image)
            return RecordingPredictor(path, backend=backend)
        def detector_factory(*, backend):
            events.append("body:" + backend)
            return FakeDetector(backend=backend)
        ticks = {"now": 0.0}
        def clock():
            ticks["now"] += 0.001
            return ticks["now"]
        thread_counts = {"requested": 2, "torch": 2, "opencv": 2}
        with mock.patch.object(benchmark, "_runtime_environment", return_value={"python_version": "test"}), \
                mock.patch.object(benchmark, "_set_benchmark_threads", return_value=thread_counts) as set_threads, \
                mock.patch.object(benchmark, "_thread_counts", return_value=thread_counts):
            report = benchmark.run_benchmark(
                self.bundle, images=[self.image], image_metadata=self.metadata,
                backend="both", repeats=3, warmup=1, descriptor_fn=descriptor,
                detector_factory=detector_factory, predictor_factory=predictor_factory,
                clock=clock)
        self.assertEqual(events[0], "validated_candidate")
        self.assertEqual(report["candidate"]["id"], "b" * 64)
        self.assertEqual(report["candidate"]["dataset_version"], "train-set-3")
        self.assertEqual(report["configuration"]["backends"], ["cpu", "mps"])
        self.assertFalse(report["synthetic_inputs"])
        self.assertFalse(report["accuracy_evaluation"])
        self.assertFalse(report["passage_metrics"]["available"])
        self.assertNotIn("queue_delay", json.dumps(report))
        self.assertEqual(set_threads.call_count, 16)
        self.assertEqual(len(seen_inputs), 8)
        self.assertEqual({(shape, pixel) for _, shape, pixel in seen_inputs}, {((64, 80, 3), 127)})
        cpu, mps = report["results"]
        for result in (cpu, mps):
            self.assertEqual(result["candidate"]["status"], "completed")
            self.assertEqual(result["candidate"]["warmup_count"], 1)
            summary = result["candidate"]["timings"]["total_ms"]
            self.assertEqual(summary["count"], 3)
            self.assertEqual(summary["median_ms"], 9.0)
            self.assertEqual(summary["p95_ms"], 11.7)
            self.assertEqual(result["body_detector"]["status"], "completed")
            self.assertEqual(result["body_detector"]["backend_status"]["requested"], result["requested_backend"])
            self.assertEqual(set(result["body_detector"]["timings"]), {"total_ms"})
            self.assertIn("not separately exposed", result["body_detector"]["measurement_scope"])
            self.assertEqual(result["candidate"]["thread_settings"]["requested"], 2)
            self.assertEqual(result["candidate"]["thread_settings"]["measured_sample_count"], 3)
            stage_times = result["body_detector"]["predictor_stages"]["timings_ms"]
            self.assertEqual({name: row["count"] for name, row in stage_times.items()},
                             {"preprocess": 3, "inference": 3, "postprocess": 3})
        self.assertEqual(mps["body_detector"]["backend_status"]["active"], "cpu")
        output = self.root / "benchmark.json"
        benchmark._atomic_output(output, report)
        serialized = json.loads(output.read_text())
        self.assertEqual(serialized["inputs"], self.metadata)
        self.assertNotIn("candidate-bundle", output.read_text())

    def test_synthetic_fixture_is_deterministic_and_explicitly_labeled(self):
        first_images, first_info = benchmark._load_input_images(None, synthetic_fixture=True)
        second_images, second_info = benchmark._load_input_images(None, synthetic_fixture=True)
        self.assertEqual(first_info, second_info)
        np.testing.assert_array_equal(first_images[0], second_images[0])
        self.assertEqual(first_info[0]["width"], 640)
        self.assertEqual(first_info[0]["height"], 480)

    def test_mps_predictor_failure_is_reported_and_cpu_result_retained(self):
        def predictor_factory(path, *, backend):
            if backend == "mps":
                raise RuntimeError("explicit MPS unavailable")
            return FakePredictor(path, backend=backend)
        thread_counts = {"requested": 2, "torch": 2, "opencv": 2}
        with mock.patch.object(benchmark, "_runtime_environment", return_value={}), \
                mock.patch.object(benchmark, "_set_benchmark_threads", return_value=thread_counts), \
                mock.patch.object(benchmark, "_thread_counts", return_value=thread_counts):
            report = benchmark.run_benchmark(
                self.bundle, images=[self.image], image_metadata=self.metadata,
                backend="both", repeats=1, warmup=0,
                descriptor_fn=lambda path: {"id": "b" * 64},
                detector_factory=FakeDetector, predictor_factory=predictor_factory)
        self.assertEqual(report["results"][0]["candidate"]["status"], "completed")
        self.assertEqual(report["results"][1]["candidate"]["status"], "error")
        self.assertEqual(report["results"][1]["candidate"]["error"]["type"], "RuntimeError")
        self.assertEqual(report["results"][1]["body_detector"]["backend_status"]["fallback_reason"],
                         "test MPS unavailable")

    def test_body_stage_wrapper_synchronizes_mps_and_reports_each_method(self):
        ticks = {"time": 0.0}
        def fake_clock():
            ticks["time"] += 0.01
            return ticks["time"]
        synchronizations = []
        model = FakeYolo()
        instrumentation = benchmark._instrument_predictor_stages(
            model, lambda: {"active": "mps"}, perf_counter=fake_clock,
            synchronize=lambda: synchronizations.append("sync"))
        for method in ("preprocess", "inference", "postprocess"):
            getattr(model.predictor, method)(self.image)
            timing = instrumentation["sample_values"][method]
            self.assertEqual(len(timing), 1)
            self.assertGreater(timing[0], 0)
        result = benchmark._finish_predictor_stage_timings(instrumentation, repeats=1)
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(synchronizations), 6)

    def test_candidate_validation_precedes_model_factories_and_missing_weights_fail_closed(self):
        calls = []
        with mock.patch.object(benchmark, "ROOT", self.root):
            # Trusted local weights are absent in this isolated root; no loader may be called.
            with self.assertRaisesRegex(ValueError, "trusted local model weights"):
                benchmark.run_benchmark(self.bundle, images=[self.image], image_metadata=self.metadata,
                    descriptor_fn=lambda path: calls.append("descriptor"),
                    detector_factory=lambda **kwargs: calls.append("detector"),
                    predictor_factory=lambda *a, **kw: calls.append("predictor"))
        self.assertEqual(calls, [])

    def test_invalid_bounds_paths_and_cli_source_are_rejected(self):
        for kwargs in ({"backend": "cuda"}, {"repeats": 0}, {"repeats": 101}, {"warmup": -1}, {"warmup": 21}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                benchmark.run_benchmark(self.bundle, images=[self.image], image_metadata=self.metadata,
                    descriptor_fn=lambda path: {"id": "b" * 64}, detector_factory=FakeDetector,
                    predictor_factory=FakePredictor, **kwargs)
        parser = benchmark.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--candidate", str(self.bundle), "--output", str(self.root / "out.json")])
        unsafe = self.root / "unsafe.png"
        unsafe.symlink_to(self.root / "missing.png")
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            benchmark._load_input_images([str(unsafe)], synthetic_fixture=False)

    def test_image_header_bounds_and_total_pixel_budget_precede_opencv_decode(self):
        image_path = self.root / "header-only.png"
        image_path.write_bytes(b"bounded fixture bytes")

        class Header:
            def __init__(self, size):
                self.size, self.format, self.n_frames = size, "PNG", 1
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with mock.patch.object(benchmark.Image, "open", return_value=Header((12001, 100))):
            with mock.patch.object(benchmark.cv2, "imdecode") as decode:
                with self.assertRaisesRegex(ValueError, "dimensions"):
                    benchmark._load_input_images([str(image_path)], synthetic_fixture=False)
                decode.assert_not_called()

        class Raster:
            ndim = 3
            def __init__(self, width, height):
                self.shape = (height, width, 3)
        headers = [Header((6500, 6000)) for _ in range(3)]
        with mock.patch.object(benchmark.Image, "open", side_effect=headers), \
                mock.patch.object(benchmark.cv2, "imdecode",
                                  side_effect=[Raster(6500, 6000), Raster(6500, 6000)]) as decode:
            with self.assertRaisesRegex(ValueError, "Combined input pixel count"):
                benchmark._load_input_images([str(image_path)] * 3, synthetic_fixture=False)
            self.assertEqual(decode.call_count, 2)

    def test_cli_partial_results_are_written_before_nonzero_stage_failure(self):
        output = self.root / "partial-results.json"
        partial = {"results": [
            {"candidate": {"status": "error"},
             "body_detector": {"status": "completed"}}]}
        with mock.patch.object(benchmark, "_load_input_images",
                               return_value=([self.image], self.metadata)), \
                mock.patch.object(benchmark, "run_benchmark", return_value=partial):
            code = benchmark.main(["--candidate", str(self.bundle), "--synthetic-fixture",
                                   "--output", str(output), "--repeats", "1", "--warmup", "0"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.read_text()), partial)

    def test_script_bootstraps_repo_import_path_without_pythonpath(self):
        script = Path(benchmark.__file__).resolve()
        expected_root = str(script.parents[1])
        child_code = (
            "import runpy,sys,os; "
            f"runpy.run_path({str(script)!r}, run_name='benchmark_import_test'); "
            f"assert sys.path[0] == {expected_root!r}; "
            "assert 'MPLCONFIGDIR' in os.environ and 'YOLO_CONFIG_DIR' in os.environ"
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        completed = subprocess.run([sys.executable, "-c", child_code], cwd=self.root,
                                   env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
