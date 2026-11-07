import unittest
from dataclasses import replace
from core.uniform_latency import UniformPassageMetrics,distribution
from core.uniform_observation import GarmentObservation


class UniformLatencyTests(unittest.TestCase):
    def garment(self,label=None,policy=None,cached=False):
        return GarmentObservation('fixture',label,.9,'fixture','model','policy',policy,cached,
            {'inference_ms':12.,'queue_delay_ms':3.,'assessment_ms':20.},decision_at=1.,evidence_at=.9)

    def test_missing_and_unassessed_not_hidden_and_metrics_require_ground_truth(self):
        m=UniformPassageMetrics();m.observe_labels([{'passage_alias':p} for p in ('missed','blur','recognized')])
        m.record('blur',self.garment(),100);m.record('recognized',self.garment('other_clothing','violation'),200)
        r=m.report();self.assertEqual((r['passage_count'],r['missed_passages'],r['unassessed_passages'],r['recognized_passages']),(3,1,1,1))
        self.assertIsNone(r['false_violations']);self.assertIsNone(r['correct_classification_passages'])
        self.assertIsNone(r['annotated_zone_entry_to_first_decision']['median_ms'])

    def test_both_endpoints_and_later_resolution_one_passage(self):
        m=UniformPassageMetrics();m.observe_labels([{'passage_alias':'p','zone_entry_ms':0,
            'first_usable_torso_ms':100,'garment_label':'organizational_shirt','policy_label':'compliant'}])
        m.record('p',self.garment(),50)
        m.record('p',replace(self.garment('organizational_shirt','compliant'),evidence_at=1.1),250)
        m.record('p',replace(self.garment('organizational_shirt','compliant',True),evidence_at=1.1),300)
        r=m.report();self.assertEqual(r['passage_count'],1)
        self.assertEqual(r['annotated_zone_entry_to_first_decision']['median_ms'],50)
        self.assertEqual(r['annotated_first_usable_to_first_decision']['median_ms'],150)
        self.assertEqual(r['annotated_first_usable_to_first_recognition']['median_ms'],150)
        self.assertEqual(r['inference_samples'],2);self.assertEqual(r['correct_classification_passages'],1)
        self.assertEqual(r['false_violations'],0)

    def test_conflicting_labels_and_negative_timestamps_rejected(self):
        m=UniformPassageMetrics();m.observe_labels([{'passage_alias':'p','garment_label':'campus_uniform'}])
        with self.assertRaises(ValueError):m.observe_labels([{'passage_alias':'p','garment_label':'other_clothing'}])
        with self.assertRaises(ValueError):m.observe_labels([{'passage_alias':'p','zone_entry_ms':-1}])
        with self.assertRaises(ValueError):m.observe_labels([{'passage_alias':'p','garment_label':'wrong_uniform'}])

    def test_summary_percentiles_are_actual_samples(self):
        self.assertEqual(distribution([10,20,30]),{'count':3,'median_ms':20.,'p95_ms':29.})
        self.assertEqual(distribution([]),{'count':0,'median_ms':None,'p95_ms':None})


if __name__=='__main__':unittest.main()
