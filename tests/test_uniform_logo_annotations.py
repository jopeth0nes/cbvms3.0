import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
from PIL import Image

from core.uniform_dataset import UniformDataset
from core.uniform_logo_annotations import LOGO_VISIBILITIES, logo_readiness, normalize_visibility


def raster_bytes():
    rng = np.random.default_rng(2241)
    pixels = rng.integers(0, 256, (96, 128, 3), dtype=np.uint8)
    stream = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(stream, format="PNG")
    return stream.getvalue()


class UniformLogoAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "catalog"
        self.ds = UniformDataset(self.root, auto_process=False)
        self.data = raster_bytes()
        self.path = Path(self.tmp.name) / "source.png"
        self.path.write_bytes(self.data)
        self.image_id = self.ds.import_paths([self.path], "campus_uniform", capture_group="camera-a")[0]

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, **kwargs):
        defaults = dict(category="campus_uniform", crop=[8, 8, 120, 88], status="approved",
                        flags=[], logo_visibility="visible", logo_box=[30, 24, 52, 42],
                        logo_reviewed=True, logo_source_kind="photographed_print",
                        logo_association_confirmed=True)
        defaults.update(kwargs)
        return self.ds.review(self.image_id, **defaults)

    def test_legacy_visibility_never_means_absence(self):
        self.assertEqual(LOGO_VISIBILITIES, ("visible", "obscured", "too_small", "back_view", "uncertain"))
        self.assertEqual(normalize_visibility("unknown"), "uncertain")
        self.assertEqual(normalize_visibility("not_visible"), "uncertain")
        self.assertEqual(normalize_visibility("occluded"), "obscured")
        self.assertEqual(normalize_visibility("unexpected"), "uncertain")
        readiness = logo_readiness([{"logo_visibility": "not_visible"}])
        self.assertEqual(readiness["status"], "Not trained")
        self.assertFalse(readiness["enabled"])
        self.assertEqual(readiness["counts"]["reviewed_visible"], 0)

    def test_pending_edits_cannot_bypass_artwork_rereview(self):
        self.review(status='pending_review', logo_reviewed=False, logo_source_kind='reference_artwork')
        row = self.review(status='pending_review', logo_reviewed=False, logo_source_kind='photographed_print')
        self.assertEqual(row['logo_annotation']['source_kind'], 'reference_artwork')
        with self.assertRaisesRegex(ValueError, 'Reference artwork'):
            self.review(logo_reviewed=False, logo_source_kind='uncertain')
        row = self.review()  # Explicit confirmed photographed-print correction is permitted.
        self.assertEqual(row['logo_annotation']['source_kind'], 'photographed_print')

    def test_original_crop_read_is_bounded(self):
        self.review()
        with patch('core.uniform_dataset.MAX_FILE_BYTES', 32):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                self.ds.logo_image(self.image_id)

    def test_explicit_printed_logo_review_and_exact_native_crop(self):
        before = self.path.read_bytes()
        row = self.review()
        annotation = row["logo_annotation"]
        self.assertEqual(annotation["schema"], "printed-logo-v1")
        self.assertTrue(annotation["reviewed"])
        self.assertEqual(annotation["image_sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(annotation["torso_crop"], [8, 8, 120, 88])
        self.assertEqual(annotation["box"], [30, 24, 52, 42])
        self.assertEqual(annotation["source_kind"], "photographed_print")
        self.assertEqual(annotation["provenance"]["capture_group"], "camera-a")
        logo = self.ds.logo_image(self.image_id)
        with Image.open(io.BytesIO(self.data)) as original:
            expected = original.convert("RGB").crop((30, 24, 52, 42))
        self.assertEqual(logo.size, (22, 18))
        self.assertTrue(np.array_equal(np.asarray(logo), np.asarray(expected)))
        self.assertEqual(self.path.read_bytes(), before)
        readiness = self.ds.snapshot()["logo"]
        self.assertFalse(readiness["enabled"])
        self.assertEqual(readiness["counts"]["reviewed_visible"], 1)
        self.assertEqual(readiness["by_category"], {"campus_uniform": 1})
        self.assertEqual(readiness["known_capture_groups"], 1)
        self.assertEqual(readiness["eligible_visible_unknown_provenance"], 0)

    def test_unreviewed_legacy_box_is_not_promoted(self):
        self.ds.review(self.image_id, category="campus_uniform", crop=[8, 8, 120, 88], status="approved",
                        logo_visibility="visible", logo_box=[30, 24, 52, 42], flags=[])
        row = self.ds.snapshot()["records"][0]
        self.assertNotIn("logo_annotation", row)
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        with self.assertRaisesRegex(ValueError, "current eligible"):
            self.ds.logo_image(self.image_id)

    def test_explicit_review_requires_box_inside_torso_and_confirmed_source(self):
        with self.assertRaisesRegex(ValueError, "inside the reviewed crop"):
            self.review(logo_box=[2, 20, 52, 42])
        with self.assertRaisesRegex(ValueError, "confirmed torso association"):
            self.review(logo_association_confirmed=False)
        with self.assertRaisesRegex(ValueError, "photographed prints"):
            self.review(status="pending_review", logo_source_kind="reference_artwork")
        self.assertNotIn("logo_annotation", self.ds.snapshot()["records"][0])

    def test_reference_artwork_is_separate_and_cannot_be_approved(self):
        self.review(status="pending_review", logo_visibility="obscured", logo_box=None,
                    logo_source_kind="reference_artwork")
        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            self.ds.review(self.image_id, category="campus_uniform", crop=[8, 8, 120, 88],
                           status="approved", flags=[])
        row = self.ds.snapshot()["records"][0]
        self.assertEqual(row["logo_annotation"]["visibility"], "obscured")
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        with self.assertRaisesRegex(ValueError, "current eligible"):
            self.ds.logo_image(self.image_id)

    def test_artwork_kind_blocks_approval_without_logo_review_and_persists_as_draft(self):
        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            self.ds.review(self.image_id, category="campus_uniform", crop=[8, 8, 120, 88],
                           status="approved", logo_source_kind="reference_artwork", flags=[])
        draft = self.ds.review(self.image_id, category="campus_uniform", crop=[8, 8, 120, 88],
                               status="pending_review", logo_visibility="uncertain",
                               logo_source_kind="reference_artwork", flags=[])
        self.assertFalse(draft["logo_annotation"]["reviewed"])
        self.assertEqual(draft["logo_annotation"]["source_kind"], "reference_artwork")
        pending = self.ds.review(self.image_id, category="campus_uniform", crop=[8, 8, 120, 88],
                                 status="pending_review", flags=[])
        self.assertEqual(pending["logo_annotation"]["source_kind"], "reference_artwork")
        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            self.ds.review(self.image_id, category="campus_uniform", crop=[8, 8, 120, 88],
                           status="approved", flags=[])

    def test_annotation_versions_are_deep_copied_into_review_history(self):
        self.review()
        first = self.ds.snapshot()["records"][0]["review_history"][0]["logo_annotation"]
        self.assertTrue(first["reviewed"])
        self.ds.review(self.image_id, category="campus_uniform", crop=[8, 8, 120, 88],
                       status="pending_review", logo_visibility="uncertain",
                       logo_source_kind="photographed_print", flags=[])
        row = self.ds.snapshot()["records"][0]
        self.assertEqual(len(row["review_history"]), 2)
        self.assertTrue(row["review_history"][0]["logo_annotation"]["reviewed"])
        self.assertFalse(row["review_history"][1]["logo_annotation"]["reviewed"])
        self.assertFalse(row["logo_annotation"]["reviewed"])
        self.assertEqual(row["review_history"][0]["logo_annotation"]["box"], [30, 24, 52, 42])
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)

    def test_current_logo_box_and_visibility_must_match_review_annotation(self):
        self.review()
        catalog = json.loads(self.ds.index_path.read_text("utf-8"))
        catalog["records"][0]["logo_visibility"] = "uncertain"
        self.ds.index_path.write_text(json.dumps(catalog), encoding="utf-8")
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        with self.assertRaises(ValueError):
            self.ds.logo_image(self.image_id)
        catalog = json.loads(self.ds.index_path.read_text("utf-8"))
        catalog["records"][0]["logo_visibility"] = "visible"
        catalog["records"][0]["logo_box"] = [31, 24, 52, 42]
        self.ds.index_path.write_text(json.dumps(catalog), encoding="utf-8")
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        with self.assertRaises(ValueError):
            self.ds.logo_image(self.image_id)

    def test_changes_to_crop_category_or_provenance_make_review_stale(self):
        self.review()
        self.ds.review(self.image_id, category="campus_uniform", crop=[4, 4, 118, 86],
                       status="approved", flags=[])
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        with self.assertRaises(ValueError):
            self.ds.logo_image(self.image_id)
        self.review()
        self.ds.review(self.image_id, category="other_clothing", crop=[8, 8, 120, 88],
                       status="approved", flags=[])
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        self.review()
        other = Path(self.tmp.name) / "second-source.png"
        other.write_bytes(self.data)
        self.ds.import_paths([other], "campus_uniform", capture_group="camera-b")
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        with self.assertRaises(ValueError):
            self.ds.logo_image(self.image_id)

    def test_reviewed_nonvisible_states_are_not_negative_examples(self):
        for state in ("obscured", "too_small", "back_view", "uncertain"):
            self.review(logo_visibility=state, logo_box=None)
            self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["per_visibility"]["uncertain"], 1)
        self.assertEqual(self.ds.snapshot()["logo"]["known_capture_groups"], 0)

    def test_unknown_provenance_visible_rows_are_not_counted_as_known_groups(self):
        iid = self.ds.import_paths([self.path], "campus_uniform")[0]
        self.assertEqual(iid, self.image_id)
        self.review()
        self.assertEqual(self.ds.snapshot()["logo"]["known_capture_groups"], 0)
        self.assertEqual(self.ds.snapshot()["logo"]["eligible_visible_unknown_provenance"], 1)

    def test_readiness_rejects_malformed_annotation_coordinates(self):
        self.review()
        catalog = json.loads(self.ds.index_path.read_text("utf-8"))
        row = catalog["records"][0]
        row["logo_annotation"]["box"] = ["30", 24, 52, 42]
        row["review_history"][-1]["logo_annotation"]["box"] = ["30", 24, 52, 42]
        row["logo_box"] = ["30", 24, 52, 42]
        self.ds.index_path.write_text(json.dumps(catalog), encoding="utf-8")
        self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0)

    def test_quality_association_and_conflict_flags_block_visible_readiness(self):
        for flag in ("possible_blur", "low_resolution", "obstruction", "multiple_people",
                     "incorrect_association", "label_conflict"):
            self.review(flags=[flag])
            self.assertEqual(self.ds.snapshot()["logo"]["counts"]["reviewed_visible"], 0, flag)
            with self.assertRaises(ValueError):
                self.ds.logo_image(self.image_id)

    def test_original_hash_is_checked_before_serving_logo_crop(self):
        self.review()
        record = self.ds.snapshot()["records"][0]
        original = self.root / record["original_path"]
        original.write_bytes(self.data + b"tamper")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.ds.logo_image(self.image_id)


if __name__ == "__main__":
    unittest.main()
