import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.uniform_walkthrough import MATCH_IOU, WalkthroughMetrics
from scripts.replay_camera_assessment import load_annotations, validate_replay_fps


def observation(pid, face, torso=None, body=None, track=1):
    return SimpleNamespace(presence_id=pid, track_id=track, box=face,
                           torso_box=torso, body_box=body)


def assessment(pid, garment="campus_uniform", policy="compliant"):
    return SimpleNamespace(presence_id=pid,
        garment=SimpleNamespace(label=garment, policy_outcome=policy))


def label(alias="a", *, garment="campus_uniform", policy="compliant", face=(0, 0, 20, 20),
          torso=(0, 30, 20, 60), entry=0, exit=300, usable=50, scenario="day", camera="gate"):
    return {"passage_alias": alias, "garment_label": garment, "policy_label": policy,
        "face_box": list(face) if face else None, "torso_box": list(torso) if torso else None,
        "body_box": [*face[:2], face[2], 80] if face else None,
        "zone_entry_ms": entry, "zone_exit_ms": exit,
        "first_usable_torso_ms": usable, "first_usable_face_ms": usable,
        "scenario": scenario, "camera": camera, "camera_mode": "color"}


class UniformWalkthroughTests(unittest.TestCase):
    def feed(self, metrics, frame_index=0, people=None, observations=(), results=(), media_ms=None,
             frame_size=(100, 100)):
        if media_ms is None:
            media_ms = frame_index * 33.3
        metrics.observe_frame(frame_index, media_ms, people or [], frame_size=frame_size)
        metrics.record_detections(frame_index, media_ms, observations)
        if results:
            metrics.record_results(frame_index, results[0].completion_ms, observations, results)

    def test_own_torso_required_and_same_garment_neighbor_is_wrong_person(self):
        metrics = WalkthroughMetrics()
        a = label("a", face=(0, 0, 20, 20), torso=(0, 30, 20, 60))
        b = label("b", face=(40, 0, 60, 20), torso=(40, 30, 60, 60))
        metrics.observe_frame(0, 0, [a, b], frame_size=(100, 100))
        wrong_owner = observation("presence-a", (0, 0, 20, 20), (40, 30, 60, 60))
        metrics.record_detections(0, 0, [wrong_owner])
        metrics.record_results(0, 100, [wrong_owner], [assessment("presence-a", "campus_uniform")])
        report = metrics.report(interval_end_ms=300)
        self.assertEqual(report["wrong_person_association_passages"], 1)
        self.assertEqual(report["garment_correct_associated_before_exit"], 0)
        self.assertEqual(report["garment_scored_denominator"], 2)
        self.assertEqual(report["association_unscorable_passages"], 0)
        self.assertIn("b", [p["passage_alias"] for p in report["passages"]])

    def test_poor_overlap_without_neighbor_match_is_mismatch_not_wrong_person(self):
        metrics = WalkthroughMetrics()
        metrics.observe_frame(0, 0, [label()], frame_size=(100, 100))
        obs = observation("p", (0, 0, 20, 20), (70, 70, 90, 90))
        metrics.record_detections(0, 0, [obs])
        report = metrics.report()
        self.assertEqual(report["association_mismatch_passages"], 1)
        self.assertEqual(report["wrong_person_association_passages"], 0)
        self.assertEqual(report["association_unscorable_passages"], 0)

    def test_result_assessments_join_by_presence_when_lists_are_reordered(self):
        metrics = WalkthroughMetrics()
        a = label("a", garment="campus_uniform", face=(0, 0, 20, 20), torso=(0, 30, 20, 60))
        b = label("b", garment="other_clothing", face=(40, 0, 60, 20), torso=(40, 30, 60, 60))
        metrics.observe_frame(0, 0, [a, b], frame_size=(100, 100))
        obs_a = observation("presence-a", (0, 0, 20, 20), (0, 30, 20, 60))
        obs_b = observation("presence-b", (40, 0, 60, 20), (40, 30, 60, 60))
        observations = [obs_b, obs_a]
        metrics.record_detections(0, 0, observations)
        metrics.record_results(0, 100, observations,
            [assessment("presence-a", "campus_uniform"), assessment("presence-b", "other_clothing")])
        report = metrics.report(interval_end_ms=300)
        self.assertEqual(report["garment_correct_associated_before_exit"], 2)
        self.assertEqual(report["garment_scored_denominator"], 2)
        self.assertEqual(report["garment_accuracy_all_labeled_passages"], 1.0)

    def test_late_bad_result_cannot_replace_timely_correct_and_updates_are_not_duplicates(self):
        metrics = WalkthroughMetrics()
        metrics.observe_frame(0, 0, [label(exit=200)], frame_size=(100, 100))
        obs = observation("presence-a", (0, 0, 20, 20), (0, 30, 20, 60))
        metrics.record_detections(0, 0, [obs])
        metrics.record_results(0, 150, [obs], [assessment("presence-a", "campus_uniform")])
        metrics.record_results(0, 250, [obs], [assessment("presence-a", "other_clothing")])
        report = metrics.report(interval_end_ms=300)
        self.assertEqual(report["garment_correct_associated_before_exit"], 1)
        self.assertEqual(report["late_results"], 1)
        self.assertEqual(report["duplicate_logical_result_ids"], 0)
        self.assertEqual(report["passages"][0]["result_rows"][0]["completion_media_ms"], 150.0)

    def test_distinct_presence_ids_duplicate_logical_results_but_same_presence_updates_do_not(self):
        metrics = WalkthroughMetrics()
        metrics.observe_frame(0, 0, [label(exit=300)], frame_size=(100, 100))
        first = observation("p1", (0, 0, 20, 20), (0, 30, 20, 60))
        metrics.record_detections(0, 0, [first])
        metrics.record_results(0, 100, [first], [assessment("p1")])
        metrics.record_results(0, 120, [first], [assessment("p1")])
        second_label = label(exit=300)
        metrics.observe_frame(1, 33, [second_label], frame_size=(100, 100))
        second = observation("p2", (0, 0, 20, 20), (0, 30, 20, 60))
        metrics.record_detections(1, 33, [second])
        metrics.record_results(1, 140, [second], [assessment("p2")])
        self.assertEqual(metrics.report()["duplicate_logical_result_ids"], 1)

    def test_missed_unlabeled_and_missing_timestamps_do_not_create_accuracy(self):
        metrics = WalkthroughMetrics()
        metrics.observe_frame(0, 0, [{"passage_alias": "unknown"}], frame_size=(100, 100))
        report = metrics.report(interval_end_ms=33)
        self.assertEqual(report["missed_passages"], 1)
        self.assertIsNone(report["garment_accuracy_all_labeled_passages"])
        self.assertEqual(report["garment_accuracy_status"], "unavailable_incomplete_ground_truth")
        self.assertEqual(report["missing_data"]["zone_entry_ms"], 1)
        self.assertEqual(report["missing_data"]["garment_label"], 1)

    def test_incomplete_cutoff_excludes_future_passages_and_marks_started_one(self):
        metrics = WalkthroughMetrics()
        metrics.observe_frame(0, 0, [label(entry=0, exit=500)], frame_size=(100, 100))
        report = metrics.report(interval_end_ms=100, input_complete=False)
        self.assertEqual(report["passage_count"], 1)
        self.assertEqual(report["incomplete_at_cutoff_passages"], 1)

    def test_fixed_500ms_timing_trial_and_policy_are_scored_separately(self):
        metrics = WalkthroughMetrics()
        metrics.observe_frame(0, 0, [label(entry=0, exit=1000, usable=50)], frame_size=(100, 100))
        obs = observation("p", (0, 0, 20, 20), (0, 30, 20, 60))
        metrics.record_detections(0, 80, [obs])
        metrics.record_results(0, 400, [obs], [assessment("p", "campus_uniform", "violation")])
        report = metrics.report(interval_end_ms=1000)
        self.assertEqual(report["timing"]["fixed_500ms_torso_trial"]["successes"], 1)
        self.assertEqual(report["timing"]["fixed_500ms_torso_trial"]["denominator"], 1)
        self.assertEqual(report["policy"]["false_violations"], 1)
        self.assertEqual(report["timing"]["first_correct_recognition_from_usable_torso_ms"], [350.0])

    def test_policy_prediction_without_independent_policy_truth_is_not_scored(self):
        metrics = WalkthroughMetrics()
        row = label()
        row["policy_label"] = None
        metrics.observe_frame(0, 0, [row], frame_size=(100, 100))
        obs = observation("p", (0, 0, 20, 20), (0, 30, 20, 60))
        metrics.record_detections(0, 0, [obs])
        metrics.record_results(0, 100, [obs], [assessment("p", policy="violation")])
        self.assertIsNone(metrics.report()["policy"]["false_violations"])

    def test_legacy_binary_uniform_output_is_policy_only_not_three_class_garment(self):
        metrics = WalkthroughMetrics()
        row = label()
        row["garment_label"] = None
        metrics.observe_frame(0, 0, [row], frame_size=(100, 100))
        obs = observation("p", (0, 0, 20, 20), (0, 30, 20, 60))
        metrics.record_detections(0, 0, [obs])
        baseline = SimpleNamespace(presence_id="p", garment=None,
                                   uniform_assessment=SimpleNamespace(value="wrong_uniform"))
        metrics.record_results(0, 100, [obs], [baseline])
        report = metrics.report(interval_end_ms=300)
        self.assertEqual(report["policy"]["scored_passages"], 1)
        self.assertEqual(report["garment_per_class"]["campus_uniform"]["support"], 0)
        self.assertIsNone(report["garment_accuracy_all_labeled_passages"])

    def test_assessed_and_timed_out_are_separate_from_detection(self):
        metrics = WalkthroughMetrics()
        row = label("timeout", exit=300)
        metrics.observe_frame(0, 0, [row], frame_size=(100, 100))
        obs = observation("p", (0, 0, 20, 20), (0, 30, 20, 60))
        metrics.record_detections(0, 100, [obs])
        report = metrics.report(interval_end_ms=300)
        self.assertEqual(report["detected_passages"], 1)
        self.assertEqual(report["assessed_before_exit_passages"], 0)
        self.assertEqual(report["timed_out_passages"], 1)

    def test_annotation_conflicts_types_bounds_and_duplicate_frames_reject(self):
        metrics = WalkthroughMetrics()
        metrics.observe_frame(0, 0, [label()], frame_size=(100, 100))
        with self.assertRaisesRegex(ValueError, "unique and ordered"):
            metrics.observe_frame(0, 1, [label()], frame_size=(100, 100))
        other = WalkthroughMetrics()
        bad = label(face=(0, 0, 200, 20))
        with self.assertRaisesRegex(ValueError, "outside"):
            other.observe_frame(0, 0, [bad], frame_size=(100, 100))
        other = WalkthroughMetrics()
        other.observe_frame(0, 0, [label()], frame_size=(100, 100))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            other.observe_frame(1, 33, [label(garment="other_clothing")], frame_size=(100, 100))

    def test_history_is_bounded_and_dropped_rows_reported(self):
        metrics = WalkthroughMetrics(history_limit=1)
        metrics.observe_frame(0, 0, [label()], frame_size=(100, 100))
        metrics.observe_frame(1, 33, [label()], frame_size=(100, 100))
        report = metrics.report()
        self.assertEqual(report["history"]["limit"], 1)
        self.assertGreaterEqual(report["history"]["dropped_frame_rows"], 1)
        self.assertEqual(report["passage_count"], 1)

    def test_replay_annotation_rows_are_unique_and_fps_is_never_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "labels.jsonl"
            path.write_text(json.dumps({"frame_index": 0, "people": []}) + "\n" +
                            json.dumps({"frame_index": 0, "people": []}) + "\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_annotations(path)
            path.write_text(json.dumps([{"frame_index": 0}]) + "\n")
            with self.assertRaisesRegex(ValueError, "invalid annotation line"):
                load_annotations(path)
        for fps in (0, -1, float("nan"), None, "not-a-rate"):
            with self.assertRaises(ValueError):
                validate_replay_fps(fps)
        self.assertEqual(validate_replay_fps(29.97), 29.97)
        self.assertEqual(MATCH_IOU, .25)

    def test_never_usable_views_stay_in_all_passage_denominator(self):
        metrics=WalkthroughMetrics()
        unseen=label('hidden',face=None,torso=None,usable=None)
        unseen.update(usable_face_status='never_usable',usable_torso_status='never_usable')
        metrics.observe_frame(0,0,[label(),unseen],frame_size=(100,100))
        obs=observation('p',(0,0,20,20),(0,30,20,60))
        metrics.record_detections(0,0,[obs])
        metrics.record_results(0,100,[obs],[assessment('p')])
        result=metrics.report(interval_end_ms=300)
        self.assertEqual(result['garment_accuracy_all_labeled_passages'],.5)
        self.assertEqual(result['garment_scored_denominator'],2)
        self.assertEqual(result['human_never_usable_view_passages'],1)
        self.assertEqual(result['missed_passages'],1)

    def test_first_unassessed_and_later_usable_results_have_distinct_clocks(self):
        metrics=WalkthroughMetrics()
        metrics.observe_frame(0,0,[label(usable=50)],frame_size=(100,100))
        obs=observation('p',(0,0,20,20),(0,30,20,60))
        metrics.record_detections(0,0,[obs])
        metrics.record_results(0,20,[obs],[assessment('p',None,None)])
        metrics.record_results(0,100,[obs],[assessment('p')])
        timing=metrics.report(interval_end_ms=300)['timing']
        self.assertEqual(timing['entry_to_first_result']['values_ms'],[20])
        self.assertEqual(timing['usable_torso_to_first_result']['values_ms'],[50])
        self.assertEqual(timing['entry_to_first_correct_associated_recognition']['median_ms'],100)
        self.assertEqual(timing['usable_torso_to_first_correct_associated_recognition']['p95_ms'],50)
        self.assertTrue(timing['entry_to_first_result']['small_sample'])

    def test_shared_history_and_lost_denominator_disable_rates(self):
        metrics=WalkthroughMetrics(history_limit=2)
        metrics.observe_frame(0,0,[label('a'),label('b'),label('c')],frame_size=(100,100))
        metrics.observe_frame(1,33,[label('a')],frame_size=(100,100))
        result=metrics.report()
        self.assertEqual(result['history']['used'],2)
        self.assertEqual(result['history']['dropped_passages'],1)
        self.assertIsNone(result['garment_accuracy_all_labeled_passages'])
        self.assertIsNone(result['timing']['fixed_500ms_torso_trial']['success_rate'])
        self.assertEqual(result['garment_accuracy_status'],'unavailable_history_truncated')

    def test_zero_passages_missing_exit_and_preentry_results_never_score(self):
        self.assertEqual(WalkthroughMetrics().report()['garment_accuracy_status'],'unavailable_no_passages')
        metrics=WalkthroughMetrics()
        row=label(entry=100,exit=None,usable=110)
        metrics.observe_frame(0,100,[row],frame_size=(100,100))
        obs=observation('p',(0,0,20,20),(0,30,20,60))
        metrics.record_detections(0,100,[obs])
        metrics.record_results(0,90,[obs],[assessment('p')])
        metrics.record_results(0,120,[obs],[assessment('p')])
        result=metrics.report()
        self.assertIsNone(result['garment_accuracy_all_labeled_passages'])
        self.assertFalse(result['passages'][0]['correct_before_exit'])
        self.assertIsNone(result['passages'][0]['result_rows'][0]['before_zone_exit'])

    def test_actual_logical_passage_ids_drive_duplicate_count(self):
        metrics=WalkthroughMetrics()
        metrics.observe_frame(0,0,[label()],frame_size=(100,100))
        obs=observation('p',(0,0,20,20),(0,30,20,60))
        metrics.record_detections(0,0,[obs])
        item=assessment('p');item.garment.passage_id='logical-one'
        metrics.record_results(0,100,[obs],[item])
        metrics.record_results(0,110,[obs],[item])
        item.garment.passage_id='logical-two'
        metrics.record_results(0,120,[obs],[item])
        self.assertEqual(metrics.report()['duplicate_logical_result_ids'],1)

    def test_replay_rejects_oversize_annotations_and_existing_output_before_models(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); labels=root/'labels.jsonl'; video=root/'fixture.avi'; report=root/'report.json'
            with labels.open('wb') as stream:stream.truncate(33*1024*1024)
            with self.assertRaisesRegex(ValueError,'32 MiB'):load_annotations(labels)
            labels.write_text('');video.write_bytes(b'fixture');report.write_text('preserve')
            command=[sys.executable,'scripts/replay_camera_assessment.py','--video',str(video),
                '--annotations',str(labels),'--mode','candidate','--output',str(report)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Output already exists',result.stderr)
            self.assertEqual(report.read_text(),'preserve')


if __name__ == "__main__":
    unittest.main()
