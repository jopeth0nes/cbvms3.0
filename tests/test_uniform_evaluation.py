"""Disposable independent-evaluation import, overlap, and metrics tests."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from core.uniform_evaluation import CATEGORY_IDS, EvaluationService, _DHashIndex, _wilson


class EvaluationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.service = EvaluationService(self.root / "evaluation-store")
        self.manifest_path = self._write_manifest()
        self.set_id = self.service.import_set(self.manifest_path)
        self.dataset = self.service.load_set(self.set_id)
        self.baseline = {"id": "live-uniform-v0", "labels": ["correct_uniform", "wrong_uniform"],
                         "preprocessing_version": "legacy-transform-v1", "training_hashes": ["f" * 64],
                         "training_groups": ["training-camera-session"], "training_dhashes": ["f" * 16],
                         "synthetic": False, "policy_version": "policy-test-v1",
                         "dataset_version": "legacy-training-set-v0"}
        self.candidate = {"id": "uniform-garments-candidate-1", "labels": list(CATEGORY_IDS),
                          "preprocessing_version": "trainer-letterbox-rgb-0-1-v1", "training_hashes": ["e" * 64],
                          "training_groups": ["candidate-training-session"], "training_dhashes": ["e" * 16],
                          "synthetic": False, "policy_version": "policy-test-v1",
                          "dataset_version": "garments-reviewed-v3"}

    def tearDown(self):
        self.temp.cleanup()

    def _write_manifest(self, *, path_mutator=None, independence=True, group_prefix="heldout", synthetic=False):
        records = []
        for i, category in enumerate(CATEGORY_IDS):
            # Distinct synthetic fixture pixels; these are not model-training data.
            y, x = np.indices((48, 56))
            pixels = np.stack(((x * (i + 2) + y) % 256,
                               (y * (i + 5) + x * 3) % 256,
                               (x + y * (i + 7) + i * 31) % 256), axis=2).astype(np.uint8)
            image_path = self.source / f"capture-{i}.png"
            Image.fromarray(pixels).save(image_path)
            data = image_path.read_bytes()
            path_value = image_path.name
            if path_mutator:
                path_value = path_mutator(path_value)
            records.append({"path": path_value, "sha256": hashlib.sha256(data).hexdigest(),
                            "category": category, "review_status": "approved", "crop": [2, 3, 54, 46],
                            "capture_group": f"{group_prefix}-{i}", "camera_id": None if i == 0 else f"camera-{i}",
                            "lighting": None if i == 1 else "indoor", "assessability": "assessable",
                            "policy_label": ("compliant", "violation", "unknown")[i]})
        manifest = {"schema_version": 1, "version": "fixture-eval-v1", "purpose": "final_test",
                    "synthetic": synthetic, "records": records}
        if independence:
            manifest["independence"] = {"baseline_id": "live-uniform-v0", "reviewed_by": "reviewer-test",
                                        "statement": "Fixture only: heldout capture sessions are separate."}
        path = self.source / f"manifest-{id(records)}.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def _predictors(self, baseline_labels=None, candidate_labels=None):
        baseline_labels = baseline_labels or ["correct_uniform", "wrong_uniform", "wrong_uniform"]
        candidate_labels = candidate_labels or [CATEGORY_IDS[0], CATEGORY_IDS[1], None]
        baseline_policy = ["violation", "compliant", None]
        candidate_policy = ["compliant", "violation", None]
        counter = {"baseline": 0, "candidate": 0}
        def build(key, labels, policies):
            def predictor(image):
                index = counter[key]
                counter[key] += 1
                return {"label": labels[index], "policy_label": policies[index],
                        "probabilities": {labels[index]: 0.8} if labels[index] else None,
                        "reason": None if labels[index] else "no confident prediction"}
            return predictor
        return build("baseline", baseline_labels, baseline_policy), build("candidate", candidate_labels, candidate_policy)

    def test_import_freezes_byte_identical_images_and_provenance(self):
        frozen = self.dataset["frozen_manifest"]
        self.assertEqual(len(frozen["records"]), 3)
        set_dir = self.service._set_dir(self.set_id)
        self.assertEqual(set_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual((set_dir / frozen["records"][0]["stored_path"]).stat().st_mode & 0o777, 0o400)
        from core.uniform_evaluation import _dataset_identity
        self.assertEqual(self.dataset["dataset_sha256"], hashlib.sha256(
            json.dumps(_dataset_identity(frozen), ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest())
        for record in frozen["records"]:
            stored = self.service._set_dir(self.set_id) / record["stored_path"]
            self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), record["sha256"])
            self.assertTrue(record["capture_group"].startswith("heldout-"))
            self.assertIn(record["exif_orientation"], range(1, 9))
        self.assertEqual(self.service.list_sets()[0]["set_id"], self.set_id)

    def test_invalid_manifest_and_paths_rejected_before_set_creation(self):
        for change in (
            lambda name: "../" + name,
            lambda name: "C:/outside.png",
            lambda name: "/absolute.png",
        ):
            manifest = self._write_manifest(path_mutator=change)
            before = set(self.service.sets_root.iterdir())
            with self.assertRaises(ValueError):
                self.service.import_set(manifest)
            self.assertEqual(set(self.service.sets_root.iterdir()), before)
        missing_independence = self._write_manifest(independence=False)
        with self.assertRaisesRegex(ValueError, "independence"):
            self.service.import_set(missing_independence)
        unknown = self._write_manifest(group_prefix="")
        payload = json.loads(unknown.read_text())
        for index, record in enumerate(payload["records"]):
            record["capture_group"] = ""
        unknown.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "capture_group"):
            self.service.import_set(unknown)

    def test_symlink_source_and_bad_hash_rejected(self):
        link = self.source / "linked.png"
        link.symlink_to(self.source / "capture-0.png")
        path = self._write_manifest(path_mutator=lambda _: "linked.png")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.service.import_set(path)
        bad_hash = self._write_manifest()
        payload = json.loads(bad_hash.read_text())
        payload["records"][0]["sha256"] = "0" * 64
        bad_hash.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.service.import_set(bad_hash)

    def test_animated_images_are_rejected(self):
        frames = [Image.fromarray(np.full((32, 32, 3), value, dtype=np.uint8)) for value in (10, 200)]
        path = self.source / "animated.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
        manifest = self._write_manifest()
        payload = json.loads(manifest.read_text())
        record = payload["records"][0]
        record["path"] = path.name
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "animated"):
            self.service.import_set(manifest)

    def test_paired_metrics_keep_legacy_labels_semantically_separate(self):
        baseline_predict, candidate_predict = self._predictors()
        progress = []
        report_id = self.service.evaluate(self.set_id, self.baseline, self.candidate,
                                          baseline_predict, candidate_predict,
                                          progress=lambda completed, total, message: progress.append((completed, total, message)))
        report = self.service.load_report(report_id)
        self.assertEqual(report["status"], "completed")
        self.assertFalse(report["experimental"])
        self.assertTrue(report["eligible_for_real_accuracy_claims"])
        self.assertEqual(report["dataset_sha256"], self.dataset["dataset_sha256"])
        self.assertEqual(report["baseline"]["dataset_version"], "legacy-training-set-v0")
        self.assertEqual(report["candidate"]["dataset_version"], "garments-reviewed-v3")
        baseline = report["metrics"]["baseline"]
        self.assertIsNone(baseline["per_class"])
        self.assertIn("cannot be mapped semantically", baseline["per_class_unavailable_reason"])
        self.assertEqual(baseline["campus_correct_uniform_response_proxy"]["successes"], 1)
        self.assertIn("not semantic Campus Uniform recall", baseline["proxy_interpretation"])
        candidate = report["metrics"]["candidate"]
        self.assertEqual(candidate["confusion"]["columns_predicted"], [*CATEGORY_IDS, "unassessed"])
        self.assertEqual(candidate["per_class"]["other_clothing"]["recall"]["value"], 0.0)
        self.assertEqual(candidate["unassessed_count"], 1)
        self.assertEqual(candidate["unassessed_rate"]["population"], "assessable_reviewed_class_metric_rows")
        self.assertEqual(candidate["unassessed_rate"]["denominator"], 3)
        self.assertEqual(candidate["unassessed_rate"]["successes"], 1)
        self.assertEqual(candidate["all_example_unassessed_rate"]["population"], "all_reviewed_examples")
        self.assertEqual(candidate["all_example_unassessed_rate"]["denominator"], 3)
        self.assertEqual(report["metrics"]["policy"]["baseline"]["false_violation_count"], 1)
        self.assertEqual(report["metrics"]["policy"]["baseline"]["missed_violation_count"], 1)
        self.assertEqual(report["metrics"]["policy"]["candidate"]["missed_violation_count"], 0)
        self.assertTrue(report["metrics"]["policy"]["comparable_versions"])
        self.assertEqual(report["metrics"]["coverage"]["missing_camera_id"], 1)
        self.assertEqual(report["metrics"]["coverage"]["missing_lighting"], 1)
        self.assertEqual(len(report["predictions"]), 3)
        self.assertEqual(len(progress), 3)
        self.assertTrue(report["independent"])
        self.assertEqual(report["campus_comparison"]["kind"], "legacy_response_proxy")
        self.assertEqual(report["per_model"]["baseline"]["classification"]["total"], 3)
        self.assertEqual(len(self.service.report_checksum(report_id)), 64)
        self.assertEqual(self.service.list_reports()[0]["report_id"], report_id)

    def test_three_class_baseline_gets_comparable_recall_delta(self):
        baseline = {**self.baseline, "labels": list(CATEGORY_IDS)}
        baseline_predict = lambda image: {"label": CATEGORY_IDS[0], "policy_label": None}
        candidate_predict = lambda image: {"label": CATEGORY_IDS[0], "policy_label": None}
        report = self.service.load_report(self.service.evaluate(self.set_id, baseline, self.candidate,
                                                                 baseline_predict, candidate_predict))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["metrics"]["baseline"]["per_class"]["campus_uniform"]["recall"]["value"], 1.0)
        self.assertEqual(report["metrics"]["baseline"]["comparison"]["campus_uniform"]["recall_delta"]["value"], 0.0)

    def test_exact_group_and_near_overlap_are_rejected_without_metrics(self):
        sample = self.dataset["records"][0]
        descriptors = [
            {**self.candidate, "training_hashes": [sample["sha256"]]},
            {**self.candidate, "training_groups": [sample["capture_group"]]},
            {**self.candidate, "training_dhashes": [sample["dhash"]]},
        ]
        baseline_predict, candidate_predict = self._predictors()
        for candidate in descriptors:
            report = self.service.load_report(self.service.evaluate(self.set_id, self.baseline, candidate,
                                                                     baseline_predict, candidate_predict))
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["metrics"])
            self.assertIn("overlap", report["failure"])
            self.assertFalse(report["independent"])
            self.assertTrue(report["overlap"]["possible_overlap"])

    def test_wrong_baseline_independence_binding_fails_closed(self):
        baseline_predict, candidate_predict = self._predictors()
        baseline = {**self.baseline, "id": "different-baseline"}
        report = self.service.load_report(self.service.evaluate(self.set_id, baseline, self.candidate,
                                                                 baseline_predict, candidate_predict))
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["metrics"])
        self.assertIn("does not bind", report["failure"])

    def test_cancel_and_predictor_failure_never_emit_partial_metrics(self):
        baseline_predict, candidate_predict = self._predictors()
        report = self.service.load_report(self.service.evaluate(self.set_id, self.baseline, self.candidate,
            baseline_predict, candidate_predict, cancelled=lambda: True))
        self.assertEqual(report["status"], "cancelled")
        self.assertIsNone(report["metrics"])
        self.assertEqual(report["predictions"], [])
        def fail(image):
            raise RuntimeError("fixture predictor failure")
        failed = self.service.load_report(self.service.evaluate(self.set_id, self.baseline, self.candidate,
            fail, candidate_predict))
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["metrics"])
        self.assertIn("fixture predictor failure", failed["failure"])

    def test_evaluation_lock_rejects_overlapping_jobs(self):
        entered, release = threading.Event(), threading.Event()
        def blocking_baseline(image):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test release timeout")
            return {"label": "correct_uniform", "policy_label": None}
        candidate_predict = lambda image: {"label": CATEGORY_IDS[0], "policy_label": None}
        result = []
        worker = threading.Thread(target=lambda: result.append(self.service.evaluate(
            self.set_id, self.baseline, self.candidate, blocking_baseline, candidate_predict)))
        worker.start()
        self.assertTrue(entered.wait(3))
        with self.assertRaisesRegex(RuntimeError, "Another evaluation"):
            self.service.evaluate(self.set_id, self.baseline, self.candidate,
                                  lambda image: {"label": "correct_uniform"}, candidate_predict)
        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.service.load_report(result[0])["status"], "completed")

    def test_synthetic_propagates_noneligible_experimental_status(self):
        source = self._write_manifest(synthetic=True)
        set_id = self.service.import_set(source)
        baseline_predict, candidate_predict = self._predictors()
        report = self.service.load_report(self.service.evaluate(set_id, self.baseline, self.candidate,
                                                                  baseline_predict, candidate_predict))
        self.assertTrue(report["experimental"])
        self.assertFalse(report["eligible_for_real_accuracy_claims"])

    def test_unassessable_human_review_is_covered_but_excluded_from_class_metrics(self):
        source_manifest = self._write_manifest()
        payload = json.loads(source_manifest.read_text())
        payload["records"][0]["assessability"] = "unknown"
        source_manifest.write_text(json.dumps(payload))
        set_id = self.service.import_set(source_manifest)
        baseline_predict = lambda image: {"label": "correct_uniform", "policy_label": None}
        prediction_index = {"value": 0}
        def candidate_predict(image):
            prediction_index["value"] += 1
            return {"label": None if prediction_index["value"] == 1 else CATEGORY_IDS[0],
                    "policy_label": None}
        report = self.service.load_report(self.service.evaluate(set_id, self.baseline, self.candidate,
                                                                  baseline_predict, candidate_predict))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["coverage"]["per_class"]["campus_uniform"], 1)
        self.assertEqual(report["metrics"]["candidate"]["metric_population"], 2)
        self.assertEqual(report["metrics"]["candidate"]["excluded_nonassessable_count"], 1)
        self.assertEqual(report["metrics"]["candidate"]["unassessed_count"], 0)
        self.assertEqual(report["metrics"]["candidate"]["unassessed_rate"]["n"], 2)
        self.assertEqual(report["metrics"]["candidate"]["all_example_unassessed_count"], 1)
        self.assertEqual(report["metrics"]["candidate"]["all_example_unassessed_rate"]["n"], 3)
        self.assertIsNone(report["metrics"]["candidate"]["per_class"]["campus_uniform"]["recall"]["value"])

    def test_frozen_hash_tamper_and_undefined_wilson(self):
        record = self.dataset["records"][0]
        target = self.service._set_dir(self.set_id) / record["stored_path"]
        target.chmod(0o600)
        with target.open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "image hash mismatch"):
            self.service.load_set(self.set_id)
        empty = _wilson(0, 0)
        self.assertIsNone(empty["value"])
        self.assertIsNone(empty["ci95"])
        full = _wilson(5, 5)
        self.assertEqual(full["value"], 1.0)
        self.assertLess(full["ci95"]["low"], 1.0)
        self.assertEqual(full["ci95"]["high"], 1.0)

    def test_dhash_index_finds_all_within_threshold_and_rejects_far_hash(self):
        base = int("0123456789abcdef", 16)
        within = base
        for bit in (0, 11, 22, 33, 44):
            within ^= 1 << bit
        index = _DHashIndex([f"{within:016x}", "ffffffffffffffff"])
        self.assertIsNotNone(index.near(f"{base:016x}"))
        distant = int("0000000000000000", 16)
        self.assertIsNone(_DHashIndex(["ffffffffffffffff"]).near(f"{distant:016x}"))

    def test_stale_report_is_recovered_without_metrics(self):
        report_id = "a" * 32
        path = self.service.reports_root / f"{report_id}.json"
        path.write_text(json.dumps({"report_id": report_id, "set_id": self.set_id,
                                    "status": "evaluating", "metrics": None, "predictions": []}))
        report = self.service.load_report(report_id)
        self.assertEqual(report["status"], "interrupted")
        self.assertIsNone(report["metrics"])

    def test_policy_versions_are_bound_even_after_cancel_and_identical_reimport(self):
        baseline_predict, candidate_predict = self._predictors()
        first_id = self.service.evaluate(self.set_id, self.baseline, self.candidate,
                                         baseline_predict, candidate_predict, cancelled=lambda: True)
        first = self.service.load_report(first_id)
        self.assertEqual(first["status"], "cancelled")
        self.assertTrue(first["evaluation_binding_id"])

        second_set = self.service.import_set(self.manifest_path)
        second_dataset = self.service.load_set(second_set)
        self.assertNotEqual(second_set, self.set_id)
        self.assertEqual(second_dataset["dataset_sha256"], self.dataset["dataset_sha256"])
        changed = {**self.candidate, "policy_version": "policy-test-v2"}
        report_id = self.service.evaluate(second_set, self.baseline, changed,
                                          baseline_predict, candidate_predict)
        report = self.service.load_report(report_id)
        self.assertEqual(report["status"], "failed")
        self.assertIn("already bound", report["failure"])
        self.assertIsNone(report["metrics"])

    def test_load_set_rejects_frozen_manifest_tamper(self):
        manifest = self.service._set_dir(self.set_id) / "frozen_manifest.json"
        manifest.chmod(0o600)
        value = json.loads(manifest.read_text())
        value["records"][0]["category"] = CATEGORY_IDS[1]
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
            self.service.load_set(self.set_id)

    def test_terminal_report_checksum_detects_mutation(self):
        baseline_predict, candidate_predict = self._predictors()
        report_id = self.service.evaluate(self.set_id, self.baseline, self.candidate,
                                          baseline_predict, candidate_predict)
        checksum = self.service.report_checksum(report_id)
        self.assertEqual(len(checksum), 64)
        path = self.service.reports_root / f"{report_id}.json"
        report = json.loads(path.read_text())
        report["message"] = "changed after review"
        path.chmod(0o600)
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "checksum integrity"):
            self.service.load_report(report_id)
        with self.assertRaisesRegex(ValueError, "checksum integrity"):
            self.service.report_checksum(report_id)

    def test_storage_root_symlinks_are_rejected(self):
        external = self.root / "external-storage"
        external.mkdir()
        root = self.root / "linked-storage"
        root.mkdir()
        (root / "sets").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            EvaluationService(root)


if __name__ == "__main__":
    unittest.main()
