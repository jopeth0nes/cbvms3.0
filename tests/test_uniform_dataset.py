import hashlib
import io
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

import numpy as np
from PIL import Image

from core.uniform_dataset import CATEGORIES, ImportLimitError, UniformDataset, _CatalogLock


def png_bytes(color=(30, 100, 180), size=(120, 100)):
    import io
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


class UniformDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "catalog"
        self.ds = UniformDataset(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def image_file(self, name="one.png", data=None):
        path = Path(self.temp.name) / name
        path.write_bytes(data or png_bytes())
        return path

    def test_exact_dedup_keeps_original_bytes_and_source_occurrences(self):
        data = png_bytes()
        first = self.image_file("first.png", data)
        second = self.image_file("second.png", data)
        iid1 = self.ds.import_paths([first], "campus_uniform", capture_group="session-A")[0]
        iid2 = self.ds.import_paths([second], "campus_uniform", capture_group="session-A")[0]
        snap = self.ds.snapshot()
        self.assertEqual(iid1, hashlib.sha256(data).hexdigest())
        self.assertEqual(iid1, iid2)
        self.assertEqual(snap["counts"]["total"], 1)
        self.assertEqual(snap["counts"]["exact_duplicate_occurrences"], 1)
        record = snap["records"][0]
        self.assertEqual((self.root / record["original_path"]).read_bytes(), data)
        self.assertEqual(len(record["sources"]), 2)
        self.assertEqual(record["status"], "pending_review")
        self.assertFalse(snap["readiness"]["model_activation_allowed"])

    def test_incremental_near_duplicate_link_flags_both_records(self):
        first = Image.new("RGB", (160, 160), (40, 80, 120))
        second = first.copy()
        second.putpixel((0, 0), (41, 80, 120))
        paths = []
        for name, image in (("near-a.png", first), ("near-b.png", second)):
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            paths.append(self.image_file(name, stream.getvalue()))
        ids = [self.ds.import_paths([path], "campus_uniform")[0] for path in paths]
        rows = {r["image_id"]: r for r in self.ds.snapshot()["records"]}
        self.assertIn(ids[1], rows[ids[0]]["duplicate_of"])
        self.assertIn(ids[0], rows[ids[1]]["duplicate_of"])
        self.assertIn("near_duplicate_review", rows[ids[0]]["flags"])
        self.assertIn("near_duplicate_review", rows[ids[1]]["flags"])

    def test_exact_duplicate_import_bridges_capture_groups_for_split_safety(self):
        shared = png_bytes()
        a = self.ds.import_paths([self.image_file("shared-a.png", shared)], "campus_uniform", capture_group="session-A")[0]
        self.ds.import_paths([self.image_file("shared-b.png", shared)], "campus_uniform", capture_group="session-B")
        for session, seed in (("session-C", 31), ("session-D", 91)):
            stream = __import__("io").BytesIO()
            Image.fromarray(np.random.default_rng(seed).integers(0, 256, (100, 120, 3), dtype=np.uint8)).save(stream, format="PNG")
            self.ds.import_paths([self.image_file(session + ".png", stream.getvalue())], "campus_uniform", capture_group=session)
        for record in self.ds.snapshot()["records"]:
            self.ds.review(record["image_id"], category="campus_uniform", crop=[0, 0, 100, 80], status="approved")
        snap = self.ds.snapshot()
        row = next(r for r in snap["records"] if r["image_id"] == a)
        self.assertEqual(row["capture_groups"], ["session-A", "session-B"])
        proposed = snap["proposed_splits"]
        split_for = {iid: split for split in ("train", "validation", "evaluation") for iid in proposed[split]}
        self.assertIn(a, split_for)
        self.assertEqual(sum(a in group for group in proposed["grouping"]), 1)

    def test_conflicting_exact_reimport_invalidates_approval_but_same_source_does_not(self):
        path = self.image_file(data=png_bytes())
        iid = self.ds.import_paths([path], "campus_uniform")[0]
        self.ds.review(iid, category="campus_uniform", crop=[0, 0, 100, 80], status="approved")
        self.ds.import_paths([path], "campus_uniform")
        self.assertEqual(self.ds.snapshot()["records"][0]["status"], "approved")
        self.ds.import_paths([path], "other_clothing")
        record = self.ds.snapshot()["records"][0]
        self.assertEqual(record["status"], "pending_review")
        self.assertIn("label_conflict", record["flags"])

    def test_approve_all_pending_is_global_atomic_audited_and_idempotent(self):
        ids = []
        originals = {}
        for index, category in enumerate(("campus_uniform", "organizational_shirt", "other_clothing")):
            pixels = np.random.default_rng(100 + index).integers(0, 256, (160, 180, 3), dtype=np.uint8)
            stream = io.BytesIO(); Image.fromarray(pixels).save(stream, format="PNG")
            path = self.image_file(f"bulk-{index}.png", stream.getvalue())
            iid = self.ds.import_paths([path], category)[0]
            originals[iid] = stream.getvalue()
            self.ds.review(iid, category=category, crop=[10, 10, 100, 120], status="pending_review",
                           flags=["provenance_association_uncertain"] if index == 0 else [], notes=f"notes-{index}")
            ids.append(iid)
        # An exact duplicate occurrence updates sources of one record, never adds a sample.
        duplicate = self.image_file("second-source.png", path.read_bytes())
        self.ds.import_paths([duplicate], "other_clothing")
        snap = self.ds.snapshot()
        self.assertEqual(snap["counts"]["total"], 3)
        self.assertEqual(snap["counts"]["exact_duplicate_occurrences"], 1)
        before = {r["image_id"]: dict(r) for r in snap["records"]}
        result = self.ds.approve_all_pending()
        self.assertEqual((result["total_pending"], result["approved"], result["failed"], result["remaining_pending"]), (3, 3, 0, 0))
        self.assertEqual(result["not_training_eligible"], 1)
        after = {r["image_id"]: r for r in self.ds.snapshot()["records"]}
        for iid in ids:
            self.assertEqual(after[iid]["status"], "approved")
            self.assertEqual(after[iid]["original_path"], before[iid]["original_path"])
            self.assertEqual((self.ds.root / after[iid]["original_path"]).read_bytes(), originals[iid])
            for key in ("sha256", "sources", "crop", "flags", "notes", "crop_proposal_history", "logo_annotation", "duplicate_of"):
                self.assertEqual(after[iid].get(key), before[iid].get(key))
            audit = after[iid]["review_history"][-1]
            self.assertEqual(audit["action"], "bulk_administrative_approval")
            self.assertEqual(audit["flags"], before[iid]["flags"])
        history_lengths = {iid: len(after[iid]["review_history"]) for iid in ids}
        noop = self.ds.approve_all_pending()
        self.assertEqual(noop["total_pending"], 0)
        self.assertEqual(noop["already_approved"], 3)
        self.assertEqual(noop["not_training_eligible"], 1)
        self.assertEqual({r["image_id"]: len(r["review_history"]) for r in self.ds.snapshot()["records"]}, history_lengths)

    def test_approve_all_leaves_excluded_and_legacy_alone_and_save_failure_is_atomic(self):
        rows = []
        for index, (category, status) in enumerate((('campus_uniform','pending_review'), ('organizational_shirt','pending_review'), ('other_clothing','excluded'), ('unassigned','pending_review'))):
            pixels = np.random.default_rng(300 + index).integers(0,256,(100,120,3),dtype=np.uint8)
            data=io.BytesIO(); Image.fromarray(pixels).save(data,format='PNG')
            iid=self.ds.import_paths([self.image_file(f'atomic-{index}.png',data.getvalue())],category)[0]
            if status != 'pending_review' or category != 'unassigned':
                self.ds.review(iid,category=category,crop=[2,2,100,90],status=status,flags=['obstruction'] if index==0 else [])
            rows.append(iid)
        before=self.ds.snapshot()['records']
        original=self.ds._save
        with patch.object(self.ds,'_save',side_effect=OSError('fixture disk full')):
            with self.assertRaisesRegex(RuntimeError,'no photos were approved') as caught:
                self.ds.approve_all_pending()
        self.assertEqual(caught.exception.result, {
            'total_pending': 2, 'approved': 0, 'already_approved': 0, 'failed': 2,
            'not_training_eligible': 0, 'remaining_pending': 2})
        self.assertEqual(self.ds.snapshot()['records'],before)
        result=self.ds.approve_all_pending()
        self.assertEqual(result['approved'],2)
        current={r['image_id']:r for r in self.ds.snapshot()['records']}
        self.assertEqual(current[rows[2]]['status'],'excluded')
        self.assertEqual(current[rows[3]]['status'],'pending_review')
        self.assertEqual(current[rows[0]]['flags'],['obstruction'])

    def test_bulk_preserves_full_metadata_missing_crop_and_nonpending_records(self):
        ids = []
        for index, category in enumerate(('campus_uniform', 'campus_uniform', 'unassigned',
                                          'organizational_shirt', 'other_clothing')):
            pixels = np.random.default_rng(950 + index).integers(0, 256, (160, 180, 3), dtype=np.uint8)
            stream = io.BytesIO(); Image.fromarray(pixels).save(stream, format='PNG')
            ids.append(self.ds.import_paths([self.image_file(f'metadata-{index}.png', stream.getvalue())],
                                            category, capture_group='preserved-session')[0])
        self.ds.review(ids[0], category='campus_uniform', crop=[5, 5, 150, 150], status='approved', flags=[])
        self.ds.review(ids[1], category='campus_uniform', crop=[5, 5, 150, 150], status='excluded', flags=[])
        self.ds.review(ids[4], category='other_clothing', crop=[5, 5, 150, 150], status='pending_review',
                       flags=['near_duplicate_review', 'label_conflict'], notes='Keep this note',
                       logo_box=[20, 20, 40, 40], logo_visibility='visible',
                       logo_source_kind='photographed_print', logo_association_confirmed=True, logo_reviewed=True)
        self.ds._store_crop_proposal(ids[4], {'box': [6, 6, 140, 140], 'source': 'detector'})
        with self.ds._locked():
            catalog = self.ds._load()
            next(r for r in catalog['records'] if r['image_id'] == ids[4])['duplicate_of'] = [ids[0]]
            self.ds._save(catalog)
        before = {r['image_id']: r for r in self.ds.snapshot()['records']}
        original_catalog = self.ds.index_path.read_bytes()
        with patch('core.uniform_dataset.os.replace', side_effect=OSError('atomic replace failed')):
            with self.assertRaisesRegex(RuntimeError, 'no photos were approved'):
                self.ds.approve_all_pending()
        self.assertEqual(self.ds.index_path.read_bytes(), original_catalog)
        self.assertEqual(self.ds.approve_all_pending()['approved'], 2)
        after = {r['image_id']: r for r in UniformDataset(self.ds.root).snapshot()['records']}
        for iid in ids[:3]:
            self.assertEqual(after[iid], before[iid])
        for iid in ids[3:]:
            self.assertEqual(after[iid]['status'], 'approved')
            self.assertEqual(after[iid]['review_history'][:-1], before[iid]['review_history'])
            self.assertEqual({k: v for k, v in after[iid].items() if k not in ('status', 'review_history')},
                             {k: v for k, v in before[iid].items() if k not in ('status', 'review_history')})
        self.assertIsNone(after[ids[3]]['crop'])
        self.assertEqual(self.ds.snapshot()['counts']['eligible_by_category']['other_clothing'], 0)
        with patch.object(self.ds, '_save', side_effect=AssertionError('no-op must not write')):
            self.assertEqual(self.ds.approve_all_pending()['approved'], 0)

    def test_zip_preflight_rejects_traversal_before_import(self):
        archive_path = Path(self.temp.name) / "unsafe.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape.png", png_bytes())
        with self.assertRaises(ValueError):
            self.ds.import_zip(archive_path, "campus_uniform")
        self.assertEqual(self.ds.snapshot()["counts"]["total"], 0)
        self.assertFalse((Path(self.temp.name) / "escape.png").exists())

    def test_zip_rejects_unsafe_directory_and_drive_paths_and_records_archive_hash(self):
        for unsafe_name in ("../folder/", "C:/escape.png"):
            archive_path = Path(self.temp.name) / (str(len(unsafe_name)) + ".zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(unsafe_name, b"")
            with self.assertRaises(ValueError):
                self.ds.import_zip(archive_path, "campus_uniform")
        archive_path = Path(self.temp.name) / "metadata.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("__MACOSX/._photo.png", png_bytes())
            archive.writestr("photo.png", png_bytes(color=(1, 2, 3)))
        iid = self.ds.import_zip(archive_path, "campus_uniform")[0]
        source = self.ds.snapshot()["records"][0]["sources"][0]
        self.assertEqual(source["archive_sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest())
        self.assertNotIn("__MACOSX", source["path"])

    def test_legacy_audit_is_idempotent_and_never_maps_old_labels(self):
        legacy = Path(self.temp.name) / "old" / "wrong_uniform"
        legacy.mkdir(parents=True)
        (legacy / "photo.png").write_bytes(png_bytes())
        self.assertEqual(self.ds.audit_legacy(legacy.parent)["imported"], 1)
        self.assertEqual(self.ds.audit_legacy(legacy.parent)["imported"], 0)
        record = self.ds.snapshot()["records"][0]
        self.assertEqual(record["category"], "unassigned")
        self.assertEqual(record["status"], "pending_review")
        self.assertEqual(record["sources"][0]["legacy_label"], "wrong_uniform")

    def test_review_validation_and_crop_coordinates_preserve_raster(self):
        original = png_bytes(size=(160, 120))
        p = self.image_file(data=original)
        iid = self.ds.import_paths([p], "organizational_shirt")[0]
        with self.assertRaises(ValueError):
            self.ds.review(iid, category="campus_uniform", crop=[-1, 0, 80, 80], status="approved")
        self.ds.review(iid, category="organizational_shirt", crop=[10, 20, 110, 100], status="approved",
                       logo_visibility="visible", logo_box=[30, 40, 60, 70])
        self.assertEqual(self.ds.image(iid).size, (160, 120))
        self.assertEqual(self.ds.image(iid, crop=True).size, (100, 80))
        row = self.ds.snapshot()["records"][0]
        self.assertEqual(row["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(len(row["review_history"]), 1)
        self.ds.review(iid, category="campus_uniform", crop=[0, 0, 100, 100], status="pending_review")
        self.assertEqual(self.ds.snapshot()["records"][0]["status"], "pending_review")
        row = self.ds.snapshot()["records"][0]
        self.assertEqual(row["preprocess_version"], self.ds.snapshot()["preprocess_version"])
        self.assertEqual(row["crop_source"], "manual")
        self.assertEqual(row["review_history"][-1]["crop_source"], "manual")

    def test_unknown_provenance_is_one_group_and_never_claims_eval_readiness(self):
        paths = [self.image_file(f"{i}.png", png_bytes(color=(i * 30 + 5, 40, 100))) for i in range(3)]
        for p in paths:
            self.ds.import_paths([p], "other_clothing")
        snap = self.ds.snapshot()
        self.assertEqual(snap["counts"]["unknown_provenance"], 3)
        self.assertFalse(snap["readiness"]["group_safe_splits_available"])
        self.assertFalse(snap["readiness"]["independent_evaluation_ready"])
        self.assertIsNone(snap["proposed_splits"])

    def test_capture_png_is_lossless_and_imports_have_quality_flags(self):
        frame = np.zeros((96, 128, 3), dtype=np.uint8)
        frame[:, :64] = (0, 0, 255)
        frame[:, 64:] = (0, 255, 0)
        iid = self.ds.import_capture(frame, "campus_uniform", "camera-session")
        stored = np.asarray(self.ds.image(iid))[:, :, ::-1]
        self.assertTrue(np.array_equal(stored, frame))
        record = self.ds.snapshot()["records"][0]
        self.assertEqual(record["format"], "PNG")
        self.assertIn("low_resolution", record["flags"])
        self.assertFalse(self.ds.snapshot()["readiness"]["model_activation_allowed"])

    def test_known_capture_groups_do_not_leak_across_proposed_splits(self):
        for session, base in (("a", 10), ("b", 80), ("c", 150)):
            rng = np.random.default_rng(base)
            pixels = rng.integers(0, 256, (100, 120, 3), dtype=np.uint8)
            import io
            stream = io.BytesIO()
            Image.fromarray(pixels).save(stream, format="PNG")
            self.ds.import_paths([self.image_file(session + ".png", stream.getvalue())],
                                 "campus_uniform", capture_group=session)
            iid = self.ds.snapshot()["records"][-1]["image_id"]
            self.ds.review(iid, category="campus_uniform", crop=[0, 0, 100, 80], status="approved")
        proposed = self.ds.snapshot()["proposed_splits"]
        self.assertIsNotNone(proposed)
        split_for = {iid: split for split in ("train", "validation", "evaluation") for iid in proposed[split]}
        for group in proposed["grouping"]:
            self.assertEqual(len({split_for[iid] for iid in group}), 1)
        self.assertFalse(self.ds.snapshot()["readiness"]["independent_evaluation_ready"])

    def test_proposed_splits_include_only_approved_rows(self):
        ids = []
        for session, seed in (("a", 12), ("b", 34), ("c", 56), ("pending", 78)):
            stream = io.BytesIO()
            Image.fromarray(np.random.default_rng(seed).integers(0, 256, (100, 120, 3), dtype=np.uint8)).save(stream, format="PNG")
            iid = self.ds.import_paths([self.image_file(session + ".png", stream.getvalue())],
                                       "campus_uniform", capture_group=session)[0]
            ids.append(iid)
            if session != "pending":
                self.ds.review(iid, category="campus_uniform", crop=[0, 0, 100, 80], status="approved")
        snap = self.ds.snapshot()
        split_ids = {iid for key in ("train", "validation", "evaluation") for iid in snap["proposed_splits"][key]}
        self.assertEqual(split_ids, set(ids[:3]))
        self.assertEqual(snap["counts"]["approved_by_category"]["campus_uniform"], 3)

    def test_eligibility_excludes_ambiguous_approved_records_and_title_case_labels(self):
        self.assertEqual(CATEGORIES["campus_uniform"], "Campus Uniform")
        iid = self.ds.import_paths([self.image_file()], "campus_uniform")[0]
        self.ds.review(iid, category="campus_uniform", crop=[0, 0, 100, 80], status="approved",
                       flags=["label_conflict"])
        snap = self.ds.snapshot()
        self.assertFalse(snap["experimental_training_eligible"])
        self.assertTrue(snap["readiness"]["experimental_training_reasons"])
        self.assertFalse(snap["readiness"]["independent_evaluation_ready"])

    def test_human_review_can_resolve_import_association_uncertainty(self):
        iid = self.ds.import_paths([self.image_file()], "campus_uniform")[0]
        self.assertIn("provenance_association_uncertain", self.ds.snapshot()["records"][0]["flags"])
        self.ds.review(iid, category="campus_uniform", crop=[0, 0, 100, 80], status="approved", flags=[])
        snap = self.ds.snapshot()
        self.assertFalse(snap["experimental_training_eligible"])
        self.assertEqual(snap['counts']['eligible_by_category']['campus_uniform'], 1)
        for i, category in enumerate(('organizational_shirt', 'other_clothing')):
            path = self.image_file(category+'.png', png_bytes(color=(100+i, 20, 50)))
            other = self.ds.import_paths([path], category)[0]
            self.ds.review(other, category=category, crop=[0, 0, 100, 80], status='approved', flags=[])
        ready = self.ds.snapshot()['readiness']
        self.assertFalse(ready['experimental_training_eligible'])  # The existing 10-example minimum still applies.
        self.assertTrue(any('Fewer than 10' in reason for reason in ready['experimental_training_reasons']))
        self.assertFalse(ready['independent_evaluation_ready'])
        self.assertFalse(ready['model_activation_allowed'])

    def test_crop_proposal_persists_detector_origin_without_approval(self):
        iid = self.ds.import_paths([self.image_file(data=png_bytes(size=(120, 100)))], "campus_uniform")[0]
        detector = unittest.mock.Mock()
        detector.detect_persons.return_value = [[-5, -3, 200, 110], [10, 10, 50, 90]]
        detector.use_pose = False
        detector.get_torso_box.return_value = [-5, 19, 200, 68]
        with patch("core.person_detector.PersonDetector", return_value=detector):
            crop = self.ds.propose_crop(iid)
        self.assertEqual(crop, [0, 19, 120, 68])
        record = self.ds.snapshot()["records"][0]
        self.assertEqual(record["status"], "pending_review")
        self.assertEqual(record["crop_proposal"]["source"], "detector")
        self.assertIn("person_association_uncertain", record["flags"])
        self.assertIn("multiple_people", record["flags"])
        self.ds.review(iid, category="campus_uniform", crop=crop, status="approved", crop_source="detector",
                       flags=[])
        reviewed = self.ds.snapshot()["records"][0]
        self.assertEqual(reviewed["status"], "approved")
        self.assertEqual(reviewed["review_history"][-1]["crop_source"], "detector")

    def test_no_person_proposal_raises_useful_error_and_is_recorded(self):
        iid = self.ds.import_paths([self.image_file()], "campus_uniform")[0]
        detector = unittest.mock.Mock()
        detector.detect_persons.return_value = []
        detector.last_error = "Local detector weights are unavailable."
        with patch("core.person_detector.PersonDetector", return_value=detector):
            with self.assertRaisesRegex(ValueError, "weights are unavailable"):
                self.ds.propose_crop(iid)
        record = self.ds.snapshot()["records"][0]
        self.assertEqual(record["crop_proposal"]["reason"], detector.last_error)

    def test_catalog_lock_open_failure_releases_thread_lock(self):
        import threading
        lock = threading.RLock()
        with self.assertRaises(FileNotFoundError):
            with _CatalogLock(lock, Path(self.temp.name) / "missing" / "lock"):
                pass
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_exif_orientation_is_recorded_without_transforming_raster(self):
        image = Image.new("RGB", (20, 30), (30, 60, 90))
        exif = image.getexif()
        exif[274] = 6
        stream = io.BytesIO()
        image.save(stream, format="JPEG", exif=exif)
        iid = self.ds.import_paths([self.image_file("oriented.jpg", stream.getvalue())], "campus_uniform")[0]
        record = self.ds.snapshot()["records"][0]
        self.assertEqual(record["exif_orientation"], 6)
        self.assertEqual(self.ds.image(iid).size, (20, 30))

    def test_resolved_conflict_survives_same_source_reimport(self):
        path = self.image_file()
        iid = self.ds.import_paths([path], 'campus_uniform')[0]
        self.ds.import_paths([path], 'other_clothing')
        self.ds.review(iid, category='campus_uniform', crop=[0, 0, 120, 100], status='approved', flags=[])
        self.ds.import_paths([path], 'other_clothing')
        row = self.ds.snapshot()['records'][0]
        self.assertEqual(row['status'], 'approved')
        self.assertNotIn('label_conflict', row['flags'])

    def test_zip_symlink_and_compression_abuse_rejected_before_write(self):
        import stat
        archive = Path(self.temp.name)/'unsafe.zip'
        info = zipfile.ZipInfo('photo.jpg')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr(info, '../outside')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.ds.import_zip(archive, 'organizational_shirt')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('bomb.png', b'0'*1024*1024)
        with self.assertRaisesRegex(ValueError, 'compression ratio'):
            self.ds.import_zip(archive, 'organizational_shirt')
        self.assertEqual(self.ds.snapshot()['counts']['total'], 0)

    def test_archive_and_decoded_image_limits(self):
        archive = Path(self.temp.name)/'large.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('one.png', png_bytes())
        with patch('core.uniform_dataset.MAX_ZIP_ARCHIVE_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'compressed archive size'):
                self.ds.import_zip(archive, 'organizational_shirt')
        with patch('core.uniform_dataset.MAX_IMAGE_PIXELS', 100):
            with self.assertRaisesRegex(ValueError, 'pixel count'):
                self.ds.import_paths([self.image_file()], 'campus_uniform')
        self.assertEqual(self.ds.snapshot()['counts']['total'], 0)

    def test_catalog_corruption_fails_closed_without_overwrite(self):
        self.ds.index_path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.ds.snapshot()
        self.assertEqual(self.ds.index_path.read_text("utf-8"), "not-json")

    def test_wrong_uniform_is_display_label_for_stable_class_key(self):
        self.assertEqual(CATEGORIES['other_clothing'], 'Wrong Uniform')
        image_id = self.ds.import_paths([self.image_file()], 'other_clothing')[0]
        row = self.ds.snapshot()['records'][0]
        self.assertEqual(row['category'], 'other_clothing')
        self.assertEqual(image_id, row['image_id'])

    def test_limit_error_reports_progress_and_retry_deduplicates(self):
        paths = [self.image_file(f'{i}.png', png_bytes((i, 10, 20))) for i in range(3)]
        with patch('core.uniform_dataset.MAX_ZIP_FILES', 2):
            with self.assertRaises(ImportLimitError) as caught:
                self.ds.import_paths(paths, 'other_clothing')
        error = caught.exception
        self.assertEqual((error.limit_name, error.configured_limit), ('file count', 2))
        self.assertEqual((error.imported, error.duplicates, error.unprocessed), (2, 0, 1))
        retry = self.ds.import_paths(paths[2:], 'other_clothing')
        self.assertEqual(len(retry), 1)
        self.assertEqual(self.ds.snapshot()['counts']['total'], 3)

    def test_size_limits_report_progress_and_duplicate_retry(self):
        paths = [self.image_file(f'bounded-{i}.png', png_bytes((i+20, 50, 90))) for i in range(3)]
        cap = sum(p.stat().st_size for p in paths[:2])
        with patch('core.uniform_dataset.MAX_ZIP_EXPANDED', cap):
            with self.assertRaises(ImportLimitError) as caught:
                self.ds.import_paths(paths, 'other_clothing')
        error = caught.exception
        self.assertEqual((error.limit_name,error.configured_limit),('total expanded size',cap))
        self.assertEqual((error.imported,error.duplicates,error.unprocessed),(2,0,1))
        result = self.ds.import_paths(paths, 'other_clothing')
        self.assertEqual((result.imported,result.duplicates,result.unprocessed),(1,2,0))
        self.assertEqual(self.ds.snapshot()['counts']['total'],3)
        with patch('core.uniform_dataset.MAX_FILE_BYTES',1):
            with self.assertRaises(ImportLimitError) as caught:
                self.ds.import_paths(paths, 'other_clothing')
        self.assertEqual((caught.exception.imported,caught.exception.unprocessed),(0,3))

    def test_zip_preflight_count_and_size_limits_keep_zero_imports(self):
        archive_path=Path(self.temp.name)/'bounded.zip'
        with zipfile.ZipFile(archive_path,'w') as archive:
            for i in range(3): archive.writestr(f'{i}.png',png_bytes((i,20,40)))
        for constant,value,name in [('MAX_ZIP_FILES',2,'archive entry count'),
                                   ('MAX_ZIP_EXPANDED',1,'archive expanded size'),
                                   ('MAX_FILE_BYTES',1,'per-file size')]:
            with patch('core.uniform_dataset.'+constant,value):
                with self.assertRaises(ImportLimitError) as caught:
                    self.ds.import_zip(archive_path,'other_clothing')
            self.assertEqual(caught.exception.limit_name,name)
            self.assertEqual((caught.exception.imported,caught.exception.unprocessed),(0,3))
        with patch('core.uniform_dataset.MAX_ZIP_ARCHIVE_BYTES',1), \
             patch('core.uniform_dataset.zipfile.ZipFile') as opener:
            with self.assertRaises(ImportLimitError) as caught:
                self.ds.import_zip(archive_path,'other_clothing')
            opener.assert_not_called()
            self.assertIsNone(caught.exception.unprocessed)
        result=self.ds.import_zip(archive_path,'other_clothing')
        self.assertEqual(result.imported,3)
        retry=self.ds.import_zip(archive_path,'other_clothing')
        self.assertEqual((retry.imported,retry.duplicates),(0,3))
        self.assertEqual(self.ds.snapshot()['counts']['total'],3)


if __name__ == "__main__":
    unittest.main()
