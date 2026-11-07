"""Isolated activation, authorization, integrity, and version-guard tests."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core import uniform_model_registry as registry_module
from core.uniform_model_registry import (
    CLASSES, PREPROCESS, LEGACY_PREPROCESS, UniformModelRegistry, digest, json_hash, policy_rule,
)


class FakeEvaluation:
    def __init__(self, root, report):
        self.root = root
        self.report = report
        self.set_dir = root / "evaluation-set"
        self.set_dir.mkdir()
        (self.set_dir / "frozen_manifest.json").write_text("{}", encoding="utf-8")
        self.dataset_sha256 = "d" * 64

    def load_report(self, report_id):
        if report_id != "report-fixture":
            raise FileNotFoundError(report_id)
        return self.report

    def load_set(self, set_id):
        return {"set_id": set_id, "dataset_sha256": self.dataset_sha256}

    def _set_dir(self, set_id):
        return self.set_dir

    def report_checksum(self, report_id):
        return "c" * 64


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry_root = self.root / "registry"
        self.legacy_root = self.root / "legacy"
        (self.legacy_root / "models").mkdir(parents=True)
        self.legacy_model = self.legacy_root / "models" / "uniform_cls.pt"
        self.legacy_model.write_bytes(b"legacy classifier fixture bytes")
        (self.legacy_root / "models" / "uniform_color_ref.json").write_text(
            '{"hue_lo":10,"hue_hi":80,"n_samples":12}', encoding="utf-8")
        self.candidate_path = self.root / "candidate"
        self.candidate_path.mkdir()
        (self.candidate_path / "candidate.pt").write_bytes(b"candidate-state-dict-fixture")
        self.class_map = {"mapping_version": "uniform-garments-v1",
                          "classes": [{"index": i, "category": name}
                                      for i, name in enumerate(CLASSES)]}
        self._write_candidate_files()
        self.actor_state = {"role": "superadmin", "username": "reviewer"}
        self.rule = policy_rule(0.8)
        self.candidate_info = {
            "id": digest(self.candidate_path / "candidate.pt"),
            "path": str(self.candidate_path), "labels": CLASSES,
            "preprocessing_version": PREPROCESS["version"],
            "dataset_version": "dataset-fixture", "training_hashes": ["1" * 64],
            "training_groups": ["train-group"], "training_dhashes": [],
            "synthetic": False, "mapping_version": "uniform-garments-v1",
            "limitations": ["fixture only"]}
        self.report = self._valid_report()
        self.evaluation = FakeEvaluation(self.root, self.report)
        self.report["frozen_manifest_sha256"] = digest(
            self.evaluation.set_dir / "frozen_manifest.json")
        self.registry = UniformModelRegistry(
            self.registry_root, actor_provider=lambda: dict(self.actor_state),
            legacy_root=self.legacy_root, evaluation_service=self.evaluation)

    def tearDown(self):
        self.temp.cleanup()

    def _write_candidate_files(self):
        (self.candidate_path / "class_map.json").write_text(json.dumps(self.class_map), encoding="utf-8")
        manifest = {"model_preprocess": PREPROCESS, "dataset_snapshot_sha256":"dataset-fixture", "limitations": ["fixture only"], "entries": []}
        (self.candidate_path / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        config = {"synthetic_smoke": False,
                  "dataset_manifest_sha256": digest(self.candidate_path / "dataset_manifest.json")}
        (self.candidate_path / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def _valid_report(self):
        baseline = self.registry.legacy() if hasattr(self, "registry") else None
        # Legacy identity is deterministic from files and available before registry construction.
        files = {name: digest(self.legacy_root / "models" / name)
                 for name in ("uniform_cls.pt", "uniform_color_ref.json")}
        baseline_id = "legacy-" + json_hash(files)
        return {
            "report_id": "report-fixture", "set_id": "set-fixture",
            "dataset_sha256": "d" * 64,
            "frozen_manifest_sha256": digest(self.root / "evaluation-set" / "frozen_manifest.json")
                if (self.root / "evaluation-set" / "frozen_manifest.json").exists() else None,
            "status": "completed", "independent": True, "synthetic": False,
            "baseline": {"id": baseline_id, "preprocessing_version": LEGACY_PREPROCESS["version"], "policy_version": json_hash({
                "kind": "legacy_color_fusion", "minimum_confidence": .60,
                "version": "existing-live-policy-v1"})},
            "candidate": {"id": digest(self.candidate_path / "candidate.pt"),
                          "dataset_version":"dataset-fixture",
                          "policy_version": json_hash(self.rule),
                          "preprocessing_version": PREPROCESS["version"]},
            "per_model": {"candidate": {"classification": {"per_class": {
                name: {"support": 1} for name in CLASSES}}}},
            "predictions": [{"policy_label": "compliant"}, {"policy_label": "violation"}],
        }

    def _review(self):
        return {"accepted": True, "criteria": "All class supports present",
                "rationale": "Isolated fixture acceptance rationale", "report_sha256": "c" * 64}

    def _patch_candidate(self):
        return mock.patch.object(registry_module, "candidate_descriptor",
                                 return_value=dict(self.candidate_info))

    def _patch_predictor(self):
        return mock.patch.object(registry_module, "DeploymentPredictor",
                                 return_value=lambda image: {"label": CLASSES[0]})

    def activate(self, *, expected_token=None):
        current = self.registry.current()
        self.report["baseline"] = {"id": current["id"],
                                    "preprocessing_version":current['preprocessing_version'],
                                    "policy_version": json_hash(current["policy"])}
        self.evaluation.report = self.report
        with self._patch_candidate(), self._patch_predictor():
            return self.registry.activate(self.candidate_path, "report-fixture", self.rule,
                                          self._review(), expected_token=expected_token or self.registry.token())

    def test_current_session_role_rechecked_and_denied_without_pointer(self):
        self.actor_state = {"role": "staff", "username": "reviewer"}
        with self.assertRaisesRegex(PermissionError, "superadmin"):
            self.activate()
        self.assertFalse((self.registry_root / "active.json").exists())

        self.actor_state = {"role": "superadmin", "username": "reviewer"}
        calls = {"count": 0}
        def revoke_during_switch():
            calls["count"] += 1
            return ({"role": "superadmin", "username": "reviewer"} if calls["count"] < 3
                    else {"role": "staff", "username": "reviewer"})
        self.registry.actor_provider = revoke_during_switch
        with self._patch_candidate(), self._patch_predictor():
            with self.assertRaisesRegex(PermissionError, "superadmin"):
                self.registry.activate(self.candidate_path, "report-fixture", self.rule,
                                       self._review(), expected_token=self.registry.token())
        self.assertFalse((self.registry_root / "active.json").exists())

    def test_valid_activation_and_rollback_switch_pointer_atomically(self):
        before = digest(self.legacy_model)
        first_result = self.activate()
        pointer = self.registry.pointer()
        first = pointer["active"]
        self.assertEqual(first_result["active"], first)
        self.assertEqual(self.registry.validate_deployment(pointer["previous"])["kind"], "legacy")
        self.assertEqual(digest(self.legacy_model), before)
        token = pointer["generation"]
        with self._patch_predictor():
            rolled = self.registry.rollback(expected_token=token, reason="Fixture rollback")
        next_pointer = self.registry.pointer()
        self.assertEqual(next_pointer["active"], pointer["previous"])
        self.assertEqual(next_pointer["previous"], first)
        self.assertNotEqual(next_pointer["generation"], token)
        self.assertEqual(digest(self.legacy_model), before)
        self.assertEqual(rolled["active"], next_pointer["active"])

    def test_binding_preprocessing_support_and_synthetic_mismatches_are_denied(self):
        changes = [
            (lambda: self.report["candidate"].update(id="0" * 64), "these candidate weights"),
            (lambda: self.report["candidate"].update(preprocessing_version="other"), "preprocessing"),
            (lambda: self.report["candidate"].update(policy_version="other"), "Policy changed"),
            (lambda: self.report["per_model"]["candidate"]["classification"]["per_class"].update(
                {CLASSES[0]: {"support": 0}}), "missing a garment category"),
            (lambda: self.report.update(synthetic=True), "Synthetic"),
        ]
        for mutate, reason in changes:
            self.report = self._valid_report()
            self.evaluation.report = self.report
            mutate()
            with self._patch_candidate(), self._patch_predictor():
                with self.assertRaisesRegex(ValueError, reason):
                    self.registry.activate(self.candidate_path, "report-fixture", self.rule,
                                           self._review(), expected_token=self.registry.token())
            self.assertFalse((self.registry_root / "active.json").exists())

        self.report = self._valid_report()
        self.evaluation.report = self.report
        self.candidate_info["synthetic"] = True
        with self._patch_candidate(), self._patch_predictor():
            with self.assertRaisesRegex(ValueError, "Synthetic"):
                self.registry.activate(self.candidate_path, "report-fixture", self.rule,
                                       self._review(), expected_token=self.registry.token())

    def test_class_map_preprocess_and_bundle_checksum_corruption_never_switch(self):
        mutations = [
            ("class-map", lambda: (self.candidate_path / "class_map.json").write_text("{}"), "class map"),
            ("preprocessing", lambda: (self.candidate_path / "dataset_manifest.json").write_text(
                json.dumps({"model_preprocess": {"version": "wrong"}, "entries": []})), "preprocessing"),
            ("weights", lambda: (self.candidate_path / "candidate.pt").write_bytes(b"changed weights"), "identity"),
        ]
        for name, mutate, reason in mutations:
            with self.subTest(name=name):
                self.registry = UniformModelRegistry(
                    self.registry_root, actor_provider=lambda: dict(self.actor_state),
                    legacy_root=self.legacy_root, evaluation_service=self.evaluation)
                self._write_candidate_files()
                self.candidate_info["id"] = digest(self.candidate_path / "candidate.pt")
                self.report = self._valid_report()
                self.evaluation.report = self.report
                # Keep the descriptor identity fixed for checksum/class-map tampering.
                if name != "weights":
                    self.report["candidate"]["id"] = self.candidate_info["id"]
                mutate()
                with self._patch_candidate(), self._patch_predictor():
                    with self.assertRaises(ValueError):
                        self.registry.activate(self.candidate_path, "report-fixture", self.rule,
                                               self._review(), expected_token=self.registry.token())
                self.assertFalse((self.registry_root / "active.json").exists())

    def test_reviewed_report_checksum_mismatch_is_denied(self):
        review = self._review()
        review["report_sha256"] = "0" * 64
        with self._patch_candidate(), self._patch_predictor():
            with self.assertRaisesRegex(ValueError, "report changed after review"):
                self.registry.activate(self.candidate_path, "report-fixture", self.rule,
                                       review, expected_token=self.registry.token())
        self.assertFalse((self.registry_root / "active.json").exists())

    def test_published_bundle_checksum_corruption_is_detected_without_fallback(self):
        self.activate()
        active = self.registry.pointer()["active"]
        model_path = self.registry.bundle_path(active) / "candidate.pt"
        model_path.chmod(0o600)
        model_path.write_bytes(b"post-publication mutation")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.registry.current()

    def test_active_pointer_failure_recovers_as_aborted_and_never_partially_switches(self):
        original_atomic = registry_module.atomic_json
        def fail_active(path, value):
            if Path(path).name == "active.json":
                raise OSError("injected active pointer write failure")
            return original_atomic(path, value)
        with self._patch_candidate(), self._patch_predictor(), mock.patch.object(
                registry_module, "atomic_json", side_effect=fail_active):
            with self.assertRaisesRegex(OSError, "injected"):
                self.registry.activate(self.candidate_path, "report-fixture", self.rule,
                                       self._review(), expected_token=self.registry.token())
        self.assertFalse((self.registry_root / "active.json").exists())
        recovered = self.registry.recover()
        self.assertIsNone(recovered)
        journal = json.loads((self.registry_root / "switch-journal.json").read_text())
        self.assertEqual(journal["state"], "aborted")

    def test_recovery_recognizes_pointer_committed_before_journal_update(self):
        self.activate()
        pointer = self.registry.pointer()
        journal_path = self.registry_root / "switch-journal.json"
        journal = json.loads(journal_path.read_text())
        journal["state"] = "prepared"  # Simulate interruption after active.json commit.
        journal_path.write_text(json.dumps(journal))
        recovered = self.registry.recover()
        self.assertEqual(recovered, pointer)
        self.assertEqual(json.loads(journal_path.read_text())["state"], "committed")

    def test_stale_token_denied_and_result_guard_serializes_pointer_switch(self):
        self.activate()
        old_token = self.registry.token()
        self.activate()
        current = self.registry.pointer()
        with self.assertRaisesRegex(ValueError, "Active model changed"):
            with self._patch_predictor():
                self.registry.rollback(expected_token=old_token, reason="stale rollback")

        entered, release, switched = threading.Event(), threading.Event(), threading.Event()
        def guarded_result():
            with self.registry.result_guard(current["generation"]) as valid:
                self.assertTrue(valid)
                entered.set()
                self.assertTrue(release.wait(3))
        def do_rollback():
            with self._patch_predictor():
                self.registry.rollback(expected_token=current["generation"], reason="serialized rollback")
            switched.set()
        reader = threading.Thread(target=guarded_result)
        writer = threading.Thread(target=do_rollback)
        reader.start()
        self.assertTrue(entered.wait(3))
        writer.start()
        time.sleep(.1)
        self.assertFalse(switched.is_set(), "exclusive switch must wait for guarded result persistence")
        release.set()
        reader.join(3)
        writer.join(3)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertTrue(switched.is_set())


if __name__ == "__main__":
    unittest.main()
