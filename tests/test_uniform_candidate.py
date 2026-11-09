"""Disposable safety and integrity checks for isolated uniform candidates."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import random
import threading
import time
from unittest.mock import patch
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from core.uniform_candidate import (
    BLOCKING_FLAGS, CandidateService, _TrainingLock, candidate_summary, assign_group_splits,
    _related_group_roots,
)
from core.uniform_candidate_worker import classification_metrics
from core.uniform_dataset import CATEGORIES, UniformDataset
from core.trainer import _letterbox


class CandidateSummaryTests(unittest.TestCase):
    def record(self, category, status="approved", *, flags=None, crop=(0, 0, 100, 100), group=None):
        state = "queued" if status == "pending_review" else "training_ready"
        return {"category": category, "status": "excluded" if status == "excluded" else status,
                "processing_state": state, "training_eligible": True, "flags": flags or [],
                "crop": list(crop) if crop is not None else None, "width": 100, "height": 100,
                "capture_group": group, "capture_groups": [group] if group else [],
                "has_unknown_provenance": not bool(group)}

    def test_only_training_ready_clean_valid_crops_count_and_unknown_is_shared_group(self):
        records = [self.record("campus_uniform"), self.record("campus_uniform", group="camera-session")]
        records += [self.record("organizational_shirt") for _ in range(2)]
        records += [self.record("other_clothing") for _ in range(2)]
        records += [self.record("campus_uniform", flags=["possible_blur"]),
                    self.record("campus_uniform", crop=(-1, 0, 99, 99)),
                    self.record("campus_uniform", "pending_review"),
                    self.record("campus_uniform", "excluded")]
        result = candidate_summary({"records": records}, min_per_class=2)
        self.assertEqual(result["included_by_category"], {
            "campus_uniform": 2, "organizational_shirt": 2, "other_clothing": 2})
        self.assertEqual(result["training_ready_by_category"]["campus_uniform"], 2)
        self.assertEqual(result["groups_by_category"]["campus_uniform"], {"known": 1, "unknown": 1})
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(result["quality_blocked"], 2)
        self.assertTrue(result["eligible"])
        self.assertFalse(result.get("evaluation_ready", False))

    def test_config_bounds_and_unknown_keys(self):
        with self.assertRaises(ValueError):
            CandidateService.validate_config({"epochs": 41})
        with self.assertRaises(ValueError):
            CandidateService.validate_config({"synthetic_smoke": True, "min_per_class": 1})
        with self.assertRaises(ValueError):
            CandidateService.validate_config({"publish": True})

    def test_missing_approved_categories_are_not_eligible(self):
        summary = candidate_summary({"records": [self.record("campus_uniform") for _ in range(10)]})
        self.assertFalse(summary["eligible"])
        self.assertFalse(summary["experimental_training_eligible"])
        self.assertFalse(summary["evaluation_ready"])
        self.assertTrue(any("Organizational Shirt" in reason for reason in summary["readiness_reasons"]))

    def test_real_warnings_missing_crops_and_artwork_stay_ineligible_but_duplicates_remain_usable(self):
        records = [self.record('campus_uniform', flags=['possible_blur']),
                   self.record('campus_uniform', crop=None),
                   {**self.record('campus_uniform'), 'duplicate_of':['other-id']},
                   {**self.record('campus_uniform'), 'logo_annotation': {'source_kind':'reference_artwork'}},
                   self.record('campus_uniform')]
        summary = candidate_summary({'records':records}, min_per_class=1)
        self.assertEqual(summary['training_ready_by_category']['campus_uniform'],2)
        self.assertEqual(summary['included_by_category']['campus_uniform'],2)
        self.assertEqual(summary['quality_blocked'],3)

    def test_resolved_measured_warning_is_not_a_global_training_blocker(self):
        row = self.record("campus_uniform", flags=["possible_blur", "provenance_association_uncertain"])
        row["resolved_warnings"] = ["possible_blur"]
        result = candidate_summary({"records": [row]}, min_per_class=1)
        self.assertEqual(result["included_by_category"]["campus_uniform"], 1)

    def test_ready_state_ignores_administrative_status_and_advisories(self):
        categories = ("campus_uniform", "organizational_shirt", "other_clothing")
        rows = [self.record(category, "pending_review", flags=["provenance_association_uncertain", "near_duplicate_review"])
                for category in categories]
        for row in rows: row.update(processing_state="training_ready", training_eligible=True)
        summary = candidate_summary({"records": rows}, min_per_class=1)
        self.assertEqual(summary["included_by_category"], {category: 1 for category in categories})
        legacy = self.record("campus_uniform")
        legacy.pop("processing_state")
        self.assertFalse(candidate_summary({"records": [legacy]}, min_per_class=1)["eligible"])

    def test_group_splits_are_transitive_deterministic_and_fail_closed(self):
        categories = ("campus_uniform", "organizational_shirt", "other_clothing")
        entries = []
        for category in categories:
            for index in range(30):
                entries.append({"image_id": f"{category}-{index}", "category": category,
                                "group_ids": [f"capture:{category}:{index}", f"provenance:{category}:{index}"]})
        entries[0]["group_ids"].append("shared-chain")
        entries[30]["group_ids"].append("shared-chain")
        first, available, _ = assign_group_splits(entries, seed=44)
        second, available_again, _ = assign_group_splits(entries, seed=44)
        self.assertEqual(first, second)
        self.assertEqual(available, available_again)
        self.assertEqual(first[entries[0]["image_id"]], first[entries[30]["image_id"]])
        self.assertEqual(set(first.values()), {"train", "validation", "evaluation"})
        tiny = [{"image_id": str(i), "category": category, "group_ids": ["provenance:unknown-shared"]}
                for i, category in enumerate(categories)]
        splits, eval_available, note = assign_group_splits(tiny, seed=1)
        self.assertEqual(set(splits.values()), {"train"})
        self.assertFalse(eval_available)
        self.assertIn("unavailable", note)

    def test_ineligible_catalog_record_still_bridges_related_capture_groups(self):
        rows = [{"image_id": "eligible-a", "capture_group": "capture-a"},
                {"image_id": "blocked-bridge", "capture_groups": ["capture-a", "capture-b"],
                 "provenance_group": "same-source"},
                {"image_id": "eligible-b", "capture_group": "capture-b"}]
        roots = _related_group_roots(rows)
        self.assertEqual(roots["eligible-a"], roots["blocked-bridge"])
        self.assertEqual(roots["eligible-b"], roots["blocked-bridge"])

    def test_classification_metrics_calculate_macro_values_and_confusion(self):
        metrics = classification_metrics([0, 0, 1, 2], [0, 1, 1, 1])
        self.assertEqual(metrics["confusion_matrix"], [[1, 1, 0], [0, 1, 0], [0, 1, 0]])
        self.assertEqual(metrics["accuracy"], .5)
        self.assertAlmostEqual(metrics["macro_f1"], (2/3 + 1/2) / 3)

    def test_summary_counts_related_group_once_across_metadata_tokens(self):
        first = {**self.record("campus_uniform", group="same-capture"), "image_id": "first",
                 "provenance_group": "same-source", "duplicate_group": "near-group"}
        second = {**first, "image_id": "second"}
        result = candidate_summary({"records": [first, second]})
        self.assertEqual(result["groups_by_category"]["campus_uniform"]["known"], 1)


class CandidateFreezeTests(unittest.TestCase):
    def add_approved(self, ds: UniformDataset, category: str, seed: int, group: str):
        rng = np.random.default_rng(seed)
        arr = rng.integers(0, 256, size=(72, 91, 3), dtype=np.uint8)
        path = ds.root / f"fixture-{seed}.png"
        Image.fromarray(arr).save(path)
        image_id = ds.import_paths([path], category, capture_group=group)[0]
        record = ds.snapshot()["records"]
        row = next(item for item in record if item["image_id"] == image_id)
        # These are synthetic fixtures, explicitly labelled only in this disposable catalog.
        cat = ds._load()
        target = next(item for item in cat["records"] if item["image_id"] == image_id)
        target["sources"][0]["kind"] = "synthetic_smoke"
        ds._save(cat)
        ds.review(image_id, category=category, crop=[0, 0, row["width"], row["height"]],
                  status="approved", flags=[])
        catalog = ds._load()
        next(item for item in catalog["records"] if item["image_id"] == image_id).update(
            processing_state="training_ready", training_eligible=True, eligibility_reasons=[])
        ds._save(catalog)
        return image_id, arr

    def make_fixture(self, root: Path):
        dataset = UniformDataset(root / "dataset", auto_process=False)
        examples = {}
        for class_index, category in enumerate(("campus_uniform", "organizational_shirt", "other_clothing")):
            examples[category] = [self.add_approved(dataset, category, 10 * class_index + i + 1, f"camera-{class_index}")
                                  for i in range(2)]
        base = root / "local-yolov8n-cls.pt"
        base.write_bytes(b"fixture-only local weights placeholder")
        service = CandidateService(dataset, candidates_root=root / "candidates", base_weights=base)
        return dataset, service, examples

    def test_readiness_names_missing_local_weights_before_start(self):
        with tempfile.TemporaryDirectory() as temp:
            dataset, service, _ = self.make_fixture(Path(temp))
            service.base_weights.unlink()
            summary = service.summary(min_per_class=2)
            self.assertFalse(summary['eligible'])
            self.assertIn('Trusted local classifier weights are unavailable', ' '.join(summary['reasons']))
            self.assertEqual(summary['approved_by_category']['other_clothing'], 2)
            service.base_weights.write_bytes(b'')
            self.assertIn('nonempty', ' '.join(service.summary(min_per_class=2)['reasons']))

    def test_frozen_data_matches_live_letterbox_and_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, service, examples = self.make_fixture(root)
            pending_path = root / 'pending.png'
            Image.fromarray(np.random.default_rng(991).integers(0, 256, (72, 91, 3), dtype=np.uint8)).save(pending_path)
            pending_id = dataset.import_paths([pending_path], 'other_clothing', capture_group='camera-pending')[0]
            excluded_path = root / 'excluded.png'
            Image.fromarray(np.random.default_rng(992).integers(0, 256, (72, 91, 3), dtype=np.uint8)).save(excluded_path)
            excluded_id = dataset.import_paths([excluded_path], 'other_clothing', capture_group='camera-excluded')[0]
            row = next(r for r in dataset.snapshot()['records'] if r['image_id'] == excluded_id)
            dataset.review(excluded_id, category='other_clothing', crop=[0, 0, row['width'], row['height']],
                           status='excluded')
            summary = service.summary(min_per_class=2)
            self.assertTrue(summary["eligible"])
            self.assertEqual(summary['approved_by_category']['other_clothing'], 2)
            self.assertEqual(summary['pending'], 1)
            self.assertEqual(summary['excluded'], 1)
            run_id = "a" * 32
            run_dir = service._run_dir(run_id)
            run_dir.mkdir()
            config = service.validate_config({"synthetic_smoke": True, "min_per_class": 2, "epochs": 1})
            frozen = service._freeze(run_dir, config)
            self.assertEqual(len(json.loads((run_dir / "dataset_manifest.json").read_text())["entries"]), 6)
            service._verify_bundle(run_dir)
            manifest = json.loads((run_dir / "dataset_manifest.json").read_text())
            self.assertEqual(json.loads((run_dir / 'class_map.json').read_text())['classes'][2]['category'], 'other_clothing')
            wrong_entries = [entry for entry in manifest['entries'] if entry['category'] == 'other_clothing']
            self.assertEqual(len(wrong_entries), 2)
            self.assertTrue(all(entry['class_index'] == 2 and entry['split'] == 'train' for entry in wrong_entries))
            self.assertNotIn(pending_id, [entry['image_id'] for entry in manifest['entries']])
            self.assertNotIn(excluded_id, [entry['image_id'] for entry in manifest['entries']])
            self.assertEqual(manifest["eligibility_snapshot"]["included_by_category"], {
                "campus_uniform": 2, "organizational_shirt": 2, "other_clothing": 2})
            entry = manifest["entries"][0]
            row = next(r for r in dataset.snapshot()["records"] if r["image_id"] == entry["image_id"])
            raster = np.asarray(dataset.image(entry["image_id"]), dtype=np.uint8)
            x1, y1, x2, y2 = row["crop"]
            crop_bgr = cv2.cvtColor(raster[y1:y2, x1:x2], cv2.COLOR_RGB2BGR)
            expected = cv2.cvtColor(_letterbox(crop_bgr, 224), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            actual = np.load(run_dir / entry["prepared_path"], allow_pickle=False)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(entry["split"], "train")
            self.assertTrue(all(e["unknown_provenance"] is False for e in manifest["entries"]))
            self.assertTrue(frozen["config"]["nondeployable"])

    def test_invalid_approved_crop_is_excluded_consistently_with_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            dataset, service, _ = self.make_fixture(Path(temporary))
            image_id, _ = self.add_approved(dataset, 'campus_uniform', 999, 'extra-session')
            catalog = dataset._load()
            next(r for r in catalog['records'] if r['image_id'] == image_id)['crop'] = [-1, 0, 10, 10]
            dataset._save(catalog)
            self.assertEqual(service.summary(min_per_class=2)['quality_blocked'], 1)
            run_dir = service._run_dir('e' * 32)
            run_dir.mkdir()
            service._freeze(run_dir, service.validate_config({'synthetic_smoke': True, 'min_per_class': 2}))
            manifest = json.loads((run_dir / 'dataset_manifest.json').read_text())
            self.assertEqual(len(manifest['entries']), 6)
            self.assertNotIn(image_id, [r['image_id'] for r in manifest['entries']])

    def test_mutated_prepared_tensor_and_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, service, _ = self.make_fixture(root)
            run_dir = service._run_dir("b" * 32)
            run_dir.mkdir()
            service._freeze(run_dir, service.validate_config({"synthetic_smoke": True, "min_per_class": 2}))
            entry = json.loads((run_dir / "dataset_manifest.json").read_text())["entries"][0]
            with (run_dir / entry["prepared_path"]).open("ab") as stream:
                stream.write(b"tamper")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                service._verify_bundle(run_dir)

    def test_symlink_original_is_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ds, service, _ = self.make_fixture(root)
            snap = ds.snapshot()
            row = snap["records"][0]
            original = ds.root / row["original_path"]
            target = original.with_suffix(".hidden")
            original.rename(target)
            original.symlink_to(target.name)
            run_dir = service._run_dir("c" * 32)
            run_dir.mkdir()
            with self.assertRaisesRegex(ValueError, "symlink"):
                service._freeze(run_dir, service.validate_config({"synthetic_smoke": True, "min_per_class": 2}))

    def test_checkpoint_pointer_is_the_recoverable_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "last_checkpoint.json").write_text("{}")
            self.assertTrue(CandidateService._has_checkpoint(run_dir))

    def test_real_child_inherited_flock_blocks_competitor_and_releases_on_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner = _TrainingLock(root)
            self.assertTrue(owner.acquire())
            fd = owner.file.fileno()
            child = subprocess.Popen([sys.executable, "-c",
                                      "import os,sys,time; os.fstat(int(sys.argv[1])); time.sleep(.35)", str(fd)],
                                     pass_fds=(fd,), stdin=subprocess.DEVNULL)
            owner.handoff()
            self.assertFalse(_TrainingLock(root).acquire())
            self.assertEqual(child.wait(timeout=3), 0)
            contender = _TrainingLock(root)
            self.assertTrue(contender.acquire())
            contender.release()

    def test_cancel_during_prepare_writes_only_flag_and_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, service, _ = self.make_fixture(root)
            entered, proceed = threading.Event(), threading.Event()
            def freeze(run_dir, config):
                entered.set()
                self.assertTrue(proceed.wait(3))
                return {"cancelled_during_freeze": True}
            service._freeze = freeze
            run_id = service.start({"epochs": 1, "batch": 2, "threads": 1,
                                    "min_per_class": 2, "synthetic_smoke": True})
            self.assertTrue(entered.wait(3))
            status_path = Path(service.candidates_root) / run_id / "status.json"
            before = json.loads(status_path.read_text())
            service.cancel(run_id)
            self.assertEqual(json.loads(status_path.read_text()), before)
            proceed.set()
            deadline = time.monotonic() + 3
            while service.status(run_id)["status"] != "cancelled" and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(service.status(run_id)["status"], "cancelled")

    def test_stale_status_recovers_and_latest_uses_microsecond_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CandidateService(candidates_root=root / "candidates", dataset_root=root / "dataset")
            older, newer = "1" * 32, "2" * 32
            for run_id, started in ((older, "2026-11-01T00:00:00.000001Z"),
                                    (newer, "2026-11-01T00:00:00.000002Z")):
                run_dir = Path(service.candidates_root) / run_id
                run_dir.mkdir(mode=0o700)
                (run_dir / "status.json").write_text(json.dumps({"run_id": run_id, "status": "running",
                    "epoch": 0, "epochs": 3, "started_at": started, "resumable": False}))
            self.assertEqual(service.status()["run_id"], newer)
            recovered = service.status(older)
            self.assertEqual(recovered["status"], "interrupted")
            self.assertFalse(recovered["resumable"])

    def test_symlink_run_directories_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external"
            external.mkdir()
            (external / "status.json").write_text(json.dumps({"run_id": "3" * 32, "status": "running"}))
            candidates = root / "candidates"
            candidates.mkdir()
            (candidates / ("3" * 32)).symlink_to(external, target_is_directory=True)
            service = CandidateService(candidates_root=candidates, dataset_root=root / "dataset")
            self.assertEqual(service.status()["status"], "idle")
            self.assertEqual(json.loads((external / "status.json").read_text())["status"], "running")

    def test_class_map_mutation_and_resume_checkpoint_tamper_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, service, _ = self.make_fixture(root)
            run_id = "e" * 32
            run_dir = service._run_dir(run_id)
            run_dir.mkdir(mode=0o700)
            service._freeze(run_dir, service.validate_config({"synthetic_smoke": True, "min_per_class": 2}))
            class_map_path = run_dir / "class_map.json"
            saved_map = class_map_path.read_bytes()
            class_map_path.write_text(json.dumps({"mapping_version": "wrong", "classes": []}))
            with self.assertRaisesRegex(ValueError, "Class mapping"):
                service._verify_bundle(run_dir)
            class_map_path.write_bytes(saved_map)

            import numpy as np
            import torch
            from core.uniform_candidate_worker import _checkpoint
            config = json.loads((run_dir / "config.json").read_text())
            metadata = {"run_uuid": run_id, "mapping_version": config["mapping_version"],
                        "dataset_manifest_sha256": config["dataset_manifest_sha256"],
                        "base_weights_sha256": config["base_weights_sha256"],
                        "config_sha256": hashlib.sha256((run_dir / "config.json").read_bytes()).hexdigest()}
            model = torch.nn.Linear(4, 3)
            _checkpoint(torch, random, np, model, torch.optim.AdamW(model.parameters()), 1, run_dir, metadata)
            self.assertEqual(service._checkpoint_epoch(run_dir), 1)
            with (run_dir / "checkpoint_epoch_000001.pt").open("ab") as stream:
                stream.write(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                service._checkpoint_epoch(run_dir)
            (run_dir / "status.json").write_text(json.dumps({"run_id": run_id, "status": "cancelled"}))
            with self.assertRaisesRegex(ValueError, "SHA256"):
                service.resume(run_id)

    def test_start_hands_lock_to_child_and_reaps_without_a_real_worker(self):
        import time
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, service, _ = self.make_fixture(root)
            fake = MagicMock()
            fake.pid = 24680
            fake.wait.return_value = 0
            fake.poll.return_value = 0
            with patch("core.uniform_candidate.subprocess.Popen", return_value=fake) as popen:
                run_id = service.start({"epochs": 1, "batch": 2, "threads": 1,
                                        "min_per_class": 2, "synthetic_smoke": True})
                self.assertEqual(len(run_id), 32)
                self.assertEqual((Path(service.candidates_root) / run_id).stat().st_mode & 0o777, 0o700)
                deadline = time.monotonic() + 5
                while not popen.called and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not popen.called:
                    self.fail(f"Candidate preparation did not launch worker: {service.status(run_id)}")
                popen.assert_called_once()
                kwargs = popen.call_args.kwargs
                self.assertTrue(kwargs["pass_fds"])
                self.assertIn(service.status(run_id)["status"], {"running", "interrupted"})
                deadline = time.monotonic() + 2
                while service.is_busy() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(service.is_busy())
                self.assertIn(service.status(run_id)["status"], {"running", "interrupted"})

    def test_lock_probe_and_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _TrainingLock(root)
            self.assertTrue(first.acquire())
            self.assertFalse(_TrainingLock(root).acquire())
            first.release()
            third = _TrainingLock(root)
            self.assertTrue(third.acquire())
            third.release()

    def test_worker_cpu_cross_entropy_step_is_finite_and_updates_weights(self):
        import torch
        import torch.nn as nn
        torch.set_num_threads(1)
        model = nn.Sequential(nn.Flatten(), nn.Linear(3 * 8 * 8, 3)).cpu()
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        batch = torch.rand(2, 3, 8, 8)
        labels = torch.tensor([0, 2], dtype=torch.long)
        before = model[-1].weight.detach().clone()
        loss = nn.CrossEntropyLoss()(model(batch), labels)
        self.assertTrue(torch.isfinite(loss).item())
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        self.assertFalse(torch.equal(before, model[-1].weight.detach()))

    def test_worker_checkpoint_sidecar_and_pointer_are_consistent(self):
        import numpy as np
        import torch
        from core.uniform_candidate_worker import _checkpoint
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            model = torch.nn.Linear(4, 3)
            optimizer = torch.optim.AdamW(model.parameters())
            metadata = {"run_uuid": "d" * 32, "mapping_version": "uniform-garments-v1",
                        "dataset_manifest_sha256": "a" * 64, "base_weights_sha256": "b" * 64,
                        "config_sha256": "c" * 64}
            _checkpoint(torch, random, np, model, optimizer, 1, run_dir, metadata)
            pointer = json.loads((run_dir / "last_checkpoint.json").read_text())
            sidecar = json.loads((run_dir / f"{pointer['file']}.sha256.json").read_text())
            self.assertEqual(pointer["sha256"], sidecar["sha256"])
            self.assertEqual(pointer["epoch"], 1)
            self.assertEqual(hashlib.sha256((run_dir / pointer["file"]).read_bytes()).hexdigest(), pointer["sha256"])

    def test_real_cpu_worker_trains_validates_saves_best_and_keeps_active_weights(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = UniformDataset(root / "dataset", auto_process=False)
            for class_index, category in enumerate(("campus_uniform", "organizational_shirt", "other_clothing")):
                for index in range(12):
                    self.add_approved(dataset, category, 1000 + class_index * 100 + index,
                                      f"session-{category}-{index}")
            repo = Path(__file__).resolve().parents[1]
            base_weights = repo / "yolov8n-cls.pt"
            protected = [base_weights, repo / "models" / "uniform_cls.pt"]
            before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
            service = CandidateService(dataset, candidates_root=root / "candidates", base_weights=base_weights)
            run_id = service.start({"epochs": 1, "batch": 16, "threads": 1,
                                    "min_per_class": 10, "synthetic_smoke": True})
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                state = service.status(run_id)
                if state.get("status") in {"completed", "failed", "interrupted"}:
                    break
                time.sleep(.25)
            state = service.status(run_id)
            run_dir = Path(service.candidates_root) / run_id
            self.assertEqual(state["status"], "completed", state.get("message", "") + "\n" +
                             ((run_dir / "train.log").read_text() if (run_dir / "train.log").exists() else ""))
            metrics = json.loads((run_dir / "metrics.json").read_text())
            self.assertIsNotNone(metrics["validation_loss"])
            self.assertEqual(len(metrics["validation"]["confusion_matrix"]), 3)
            self.assertTrue((run_dir / "best_checkpoint.pt").is_file())
            self.assertEqual({p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}, before)
            resources = json.loads((run_dir / "runtime_resources.json").read_text())
            self.assertEqual(resources["device"], "cpu")
            self.assertLessEqual(resources["actual_batch"], 4)
            self.assertTrue(state["nondeployable"])
            retained = os.environ.get("CBVMS_TEST_RETAIN_CANDIDATE")
            if retained:
                import shutil
                shutil.copytree(run_dir, Path(retained) / run_id)

    def test_cancel_crash_resume_restores_best_rng_and_early_stopping(self):
        """Exercise the real optimizer/checkpoint loop with a tiny model and fixed validation loss."""
        import torch
        import torch.nn as nn
        from core import uniform_candidate_worker as worker

        class TinyClassifier(nn.Module):
            def __init__(self):
                super().__init__()
                head = nn.Module()
                head.linear = nn.Linear(4, 3)
                self.model = nn.ModuleList([nn.Linear(3, 4), head])
                self.yaml = {"scale": "n", "yaml_file": "yolov8n-cls.yaml"}

            def forward(self, batch):
                return self.model[-1].linear(self.model[0](batch.mean(dim=(2, 3))).relu())

            def to(self, device, *args, **kwargs):
                if str(device) == "cuda":
                    raise RuntimeError("simulated accelerator allocation failure")
                return super().to(device, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = UniformDataset(root / "dataset", auto_process=False)
            for class_index, category in enumerate(("campus_uniform", "organizational_shirt", "other_clothing")):
                for index in range(12):
                    self.add_approved(dataset, category, 2000 + class_index * 100 + index,
                                      f"session-{category}-{index}")
            base = root / "base.pt"
            base.write_bytes(b"tiny-model-test-only")
            runs = []
            for name in ("uninterrupted", "resumed"):
                service = CandidateService(dataset, candidates_root=root / name, base_weights=base)
                run_dir = service._run_dir("a" * 32)
                run_dir.mkdir()
                service._freeze(run_dir, service.validate_config({"epochs": 8, "threads": 1,
                    "synthetic_smoke": True, "allow_accelerator": True}))
                (run_dir / "status.json").write_text("{}")
                runs.append(run_dir)
            real_load, real_evaluate = torch.load, worker._evaluate
            real_checkpoint, real_best = worker._checkpoint, worker._write_best_artifact

            def load(path, *args, **kwargs):
                if Path(path).name == "base_weights.pt":
                    return {"model": TinyClassifier()}
                return real_load(path, *args, **kwargs)

            def evaluate(*args):
                value = real_evaluate(*args)
                # Force no improvement after epoch one to test the stopping boundary.
                value["loss"] = 1.0
                return value

            def cancel_after_one(*args, **kwargs):
                real_checkpoint(*args, **kwargs)
                if args[5] == 1:
                    (args[6] / "cancel.flag").touch()

            def crash_after_stopping_checkpoint(torch_arg, run_dir, selection):
                if selection.get("early_stopping_wait") == 5:
                    raise RuntimeError("simulated crash before best artifact refresh")
                real_best(torch_arg, run_dir, selection)

            with patch("ultralytics.nn.tasks.ClassificationModel", TinyClassifier), \
                    patch.object(torch, "load", side_effect=load), \
                    patch.object(worker, "_evaluate", side_effect=evaluate), \
                    patch.object(worker, "_device_supported", side_effect=lambda _, name: name in {"cpu", "cuda"}), \
                    patch.object(torch.cuda, "manual_seed_all"), patch.object(torch.cuda, "empty_cache"), \
                    (root / "worker.lock").open("w") as lock:
                self.assertEqual(worker.train(runs[0], lock.fileno(), "start"), 0)
                with patch.object(worker, "_checkpoint", side_effect=cancel_after_one):
                    self.assertEqual(worker.train(runs[1], lock.fileno(), "start"), 0)
                cancelled = json.loads((runs[1] / "status.json").read_text())
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(cancelled["epoch"], 1)
                (runs[1] / "cancel.flag").unlink()
                with patch.object(worker, "_write_best_artifact", side_effect=crash_after_stopping_checkpoint):
                    with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                        worker.train(runs[1], lock.fileno(), "resume")
                (runs[1] / "best_checkpoint.pt").unlink()
                (runs[1] / "best_checkpoint.json").unlink()
                self.assertEqual(worker.train(runs[1], lock.fileno(), "resume"), 0)
            for run_dir in runs:
                state = json.loads((run_dir / "status.json").read_text())
                self.assertEqual(state["status"], "completed")
                self.assertEqual(state["epoch"], 6)
                self.assertEqual(state["best_epoch"], 1)
                self.assertIn("using CPU", state["accelerator_fallback"])
            uninterrupted = real_load(runs[0] / "checkpoint_epoch_000006.pt", weights_only=False)
            recovered = real_load(runs[1] / "checkpoint_epoch_000006.pt", weights_only=False)
            self.assertTrue(torch.equal(uninterrupted["torch_rng_state"], recovered["torch_rng_state"]))
            for key, expected in uninterrupted["model_state_dict"].items():
                self.assertTrue(torch.equal(expected, recovered["model_state_dict"][key]), key)
            exported = real_load(runs[1] / "candidate.pt", weights_only=False)
            for key, expected in recovered["training_state"]["best_model_state_dict"].items():
                self.assertTrue(torch.equal(expected, exported["state_dict"][key]), key)


if __name__ == "__main__":
    unittest.main()
