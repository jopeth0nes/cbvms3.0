"""Headless layout/card/annotation regressions; no camera, Tk root, or database.

These verify pixel transforms and Tk calls with fakes. Native font layout, window
compositing, and the physical camera still need the documented manual checks.
"""

import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from ui.camera_feed import CameraFeed, fitted_frame_rect
from ui.live_alerts import LiveAlerts, LiveAlertsModel, _PresenceCard, assessment_style
from ui.live_overlay import clipped_box, draw_assessments, label_rect


def assessment(track=1, state="Checking uniform", **kwargs):
    return dict(track_id=track, presence_id=f"session-2:{track}", student_id=f"S{track}",
                name=f"Student {track}", gender="Female", student_status="Enrolled",
                state=state, observed_at=1_800_000_000, **kwargs)


def fake_canvas(width=800, height=600):
    canvas = types.SimpleNamespace(
        _photo=None, _item=None, _placeholder_item=None, _placeholder_key=None,
        _geometry_key=None, _frame_rect=(0, 0, 0, 0),
        winfo_width=lambda: width, winfo_height=lambda: height,
        create_image=MagicMock(return_value=1), create_text=MagicMock(return_value=2),
        itemconfig=MagicMock(), coords=MagicMock(), delete=MagicMock(),
    )
    return canvas


class CameraFeedLayoutTests(unittest.TestCase):
    def test_landscape_frame_is_letterboxed_without_cropping(self):
        self.assertEqual(fitted_frame_rect(1280, 720, 800, 600), (0, 75, 800, 450))
        self.assertEqual(fitted_frame_rect(640, 480, 1280, 720), (160, 0, 960, 720))

    def test_portrait_and_resizing_preserve_aspect_ratio(self):
        self.assertEqual(fitted_frame_rect(480, 640, 800, 600), (175, 0, 450, 600))
        self.assertEqual(fitted_frame_rect(480, 640, 400, 300), (87, 0, 225, 300))
        self.assertEqual(fitted_frame_rect(480, 640, 0, 300), (0, 0, 0, 0))

    @patch("ui.camera_feed.ImageTk.PhotoImage")
    def test_render_scales_entire_frame_and_reuses_canvas_item(self, photo):
        canvas = fake_canvas()
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[:, :, 2] = 200
        self.assertTrue(CameraFeed.render(canvas, frame))
        image = photo.call_args.args[0]
        self.assertEqual(image.size, (800, 450))
        self.assertEqual(image.getpixel((0, 0)), (200, 0, 0))
        self.assertEqual(canvas.create_image.call_args.args, (0, 75))
        self.assertTrue(CameraFeed.render(canvas, frame))
        canvas.create_image.assert_called_once()
        self.assertEqual(photo.call_count, 2)
        canvas.coords.assert_called_once_with(1, 0, 75)
        self.assertEqual(frame[0, 0].tolist(), [0, 0, 200])

    @patch("ui.camera_feed.ImageTk.PhotoImage")
    def test_placeholders_are_text_cached_and_restore_render(self, photo):
        canvas = fake_canvas()
        self.assertTrue(CameraFeed.show_placeholder(canvas, "Loading recognition models"))
        self.assertFalse(CameraFeed.show_placeholder(canvas, "Loading recognition models"))
        canvas.create_text.assert_called_once()
        photo.assert_not_called()
        self.assertTrue(CameraFeed.show_placeholder(canvas, "Reconnecting camera"))
        self.assertTrue(CameraFeed.render(canvas, np.zeros((480, 640, 3), np.uint8)))
        canvas.itemconfig.assert_any_call(2, state="hidden")
        self.assertIsNone(canvas._placeholder_key)

    def test_placeholder_resizes_and_cleanup_resets_all_cached_state(self):
        canvas = fake_canvas()
        CameraFeed.show_placeholder(canvas)
        canvas.winfo_width = lambda: 1200
        self.assertTrue(CameraFeed.show_placeholder(canvas))
        canvas.coords.assert_called_once_with(2, 600, 300)
        CameraFeed.cleanup(canvas)
        self.assertIsNone(canvas._geometry_key)
        self.assertIsNone(canvas._placeholder_item)
        canvas.delete.assert_called_once_with("all")

    def test_hidden_canvas_does_not_count_as_rendered_frame(self):
        canvas = fake_canvas(width=1)
        self.assertFalse(CameraFeed.render(canvas, np.zeros((10, 10, 3), np.uint8)))
        self.assertFalse(CameraFeed.show_placeholder(canvas))


class LiveAlertsModelTests(unittest.TestCase):
    def test_incomplete_assessments_never_display_ok_or_compliant(self):
        for state in ("Identifying", "Identity uncertain", "Checking uniform",
                      "Uniform not assessed", "Unknown person", "missing", None):
            label, color = assessment_style({"state": state})
            self.assertNotIn("OK", label)
            self.assertNotIn("compliant", label)
            self.assertNotEqual(color, "#10B981")

    def test_accepted_independent_category_and_suspension_are_not_green(self):
        for extra in ({"accepted_categories": ("earring",)}, {"suspension_tag": "Suspended"}):
            label, color = assessment_style({"state": "Uniform compliant", **extra})
            self.assertEqual(label, "Uniform compliant")
            self.assertEqual(color, "#EF4444")

    def test_uniform_state_replaces_same_presence_card_and_retains_ownership(self):
        model = LiveAlertsModel()
        model.update_assessments([assessment(1), assessment(2, "Uniform compliant")])
        model.update_assessments([assessment(1, "Suspected uniform violation"), assessment(2, "Uniform compliant")])
        self.assertEqual(len(model.rows), 2)
        self.assertEqual(model.rows["session-2:1"].student_id, "S1")
        self.assertEqual(model.rows["session-2:1"].presentation()[3], "Suspected uniform violation")
        self.assertEqual(model.rows["session-2:2"].presentation()[3], "Uniform compliant")

    def test_unknown_people_get_distinct_presence_and_never_share_fallback_key(self):
        model = LiveAlertsModel()
        rows = [assessment(1, "Unknown person"), assessment(2, "Unknown person")]
        for row in rows:
            row.update(student_id=None, name="Unknown")
        model.update_assessments(rows)
        self.assertEqual(len(model.rows), 2)
        with self.assertRaises(ValueError):
            model.update_assessments([{"student_id": None, "name": "Unknown"}])

    def test_input_snapshot_cannot_be_mutated_after_handoff(self):
        model = LiveAlertsModel()
        source = assessment(accepted_categories=["earring"], detail="Awaiting review")
        model.update_assessments([source])
        source["accepted_categories"].append("wrong_uniform")
        source["name"] = "Another student"
        self.assertEqual(model.rows["session-2:1"].accepted_categories, ("earring",))
        self.assertEqual(model.rows["session-2:1"].name, "Student 1")

    def test_missing_tracks_become_historical_and_session_reset_marks_all_inactive(self):
        model = LiveAlertsModel()
        model.update_assessments([assessment(1), assessment(2)])
        model.update_assessments([assessment(2)])
        self.assertIn("Earlier presence", model.rows["session-2:1"].presentation()[1])
        self.assertIn("Current presence", model.rows["session-2:2"].presentation()[1])
        model.mark_all_inactive()
        self.assertTrue(all(not row.active for row in model.rows.values()))

    def test_clear_hides_unchanged_presence_until_new_assessment_or_return(self):
        model = LiveAlertsModel()
        model.update_assessments([assessment()])
        model.clear()
        source = assessment()
        source["observed_at"] += 10
        model.update_assessments([source])
        self.assertEqual(len(model.rows), 0)
        model.update_assessments([assessment(state="Suspected uniform violation")])
        self.assertEqual(len(model.rows), 1)
        model.clear()
        model.update_assessments([])
        model.update_assessments([assessment(state="Suspected uniform violation")])
        self.assertEqual(len(model.rows), 1)

    def test_max_cards_bounds_history_preferring_current_presence(self):
        model = LiveAlertsModel(max_cards=3)
        model.update_assessments([assessment(1), assessment(2), assessment(3)])
        model.update_assessments([assessment(2), assessment(3), assessment(4)])
        self.assertEqual(len(model.rows), 3)
        self.assertNotIn("session-2:1", model.rows)
        self.assertTrue(all(row.active for row in model.rows.values()))

    def test_cards_are_reused_without_destroying_or_forcing_scroll(self):
        widget = types.SimpleNamespace(model=LiveAlertsModel(), _cards={}, _order=(), _empty=MagicMock())
        widget.model.update_assessments([assessment()])
        with patch("ui.live_alerts._PresenceCard") as factory:
            LiveAlerts._refresh(widget)
            widget.model.update_assessments([assessment(state="Uniform compliant")])
            LiveAlerts._refresh(widget)
            factory.assert_called_once_with(widget)
            factory.return_value.grid.assert_called_once()
            factory.return_value.destroy.assert_not_called()
            self.assertEqual(factory.return_value.update_assessment.call_count, 2)

    def test_identical_card_content_avoids_repeated_widget_updates(self):
        model = LiveAlertsModel()
        model.update_assessments([assessment()])
        widget = types.SimpleNamespace(_presentation=None, _labels=[MagicMock() for _ in range(6)], configure=MagicMock())
        row = next(iter(model.rows.values()))
        _PresenceCard.update_assessment(widget, row)
        for label in widget._labels:
            label.reset_mock()
        widget.configure.reset_mock()
        _PresenceCard.update_assessment(widget, row)
        widget.configure.assert_not_called()
        for label in widget._labels:
            label.configure.assert_not_called()

    def test_long_names_and_details_wrap_to_card_width(self):
        widget = types.SimpleNamespace(_wrap=0, _labels=[MagicMock() for _ in range(6)])
        _PresenceCard._resize(widget, types.SimpleNamespace(width=220))
        _PresenceCard._resize(widget, types.SimpleNamespace(width=220))
        for label in widget._labels:
            label.configure.assert_called_once_with(wraplength=196)


class AssessmentOverlayTests(unittest.TestCase):
    def test_mirrored_coordinates_stay_aligned_and_clipped(self):
        self.assertEqual(clipped_box((20, 30, 100, 150), 640, 480, True), (540, 30, 620, 150))
        self.assertEqual(clipped_box((-10, -5, 1000, 800), 640, 480), (0, 0, 639, 479))
        self.assertIsNone(clipped_box((3, 4, 2, 8), 640, 480))
        self.assertIsNone(clipped_box((0, 0, float("nan"), 10), 640, 480))
        self.assertIsNone(clipped_box(None, 640, 480))
        self.assertIsNone(clipped_box((0, 0, "unavailable", 10), 640, 480))

    def test_label_rect_always_stays_inside_frame(self):
        for anchor in ((0, 0, 80, 80), (610, 410, 660, 510), (200, 200, 300, 300)):
            x1, y1, x2, y2 = label_rect(anchor, (200, 50), (640, 480))
            self.assertGreaterEqual(min(x1, y1), 0)
            self.assertLessEqual(x2, 640)
            self.assertLessEqual(y2, 480)

    def test_neighboring_labels_use_an_available_non_overlapping_row(self):
        anchor = (200, 180, 260, 240)
        first = label_rect(anchor, (150, 40), (640, 480))
        second = label_rect(anchor, (150, 40), (640, 480), [first])
        x_overlap = max(0, min(first[2], second[2]) - max(first[0], second[0]))
        y_overlap = max(0, min(first[3], second[3]) - max(first[1], second[1]))
        self.assertEqual(x_overlap * y_overlap, 0)

    def test_unannotated_mirror_flips_only_output(self):
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[:, :100] = 200
        output = draw_assessments(frame, [], mirror=True)
        np.testing.assert_array_equal(output[:, -100:], frame[:, :100])
        self.assertFalse(np.shares_memory(frame, output))

    def test_overlay_uses_same_state_as_cards_and_does_not_mutate_frame(self):
        frame = np.zeros((480, 640, 3), np.uint8)
        row = assessment(state="Suspected uniform violation", face_box=[210, 110, 310, 210],
                         torso_box=[170, 215, 355, 440])
        original = frame.copy()
        with patch("ui.live_overlay.cv2.putText", wraps=__import__("cv2").putText) as text:
            output = draw_assessments(frame, [row], mirror=True)
            labels = " ".join(call.args[1] for call in text.call_args_list)
        self.assertIn("Student 1", labels)
        self.assertIn("Suspected uniform violation", labels)
        self.assertIn("[T1]", labels)
        self.assertNotIn("OK", labels)
        np.testing.assert_array_equal(frame, original)
        self.assertTrue(np.any(output))
        self.assertEqual(row["face_box"], [210, 110, 310, 210])


if __name__ == "__main__":
    unittest.main()
