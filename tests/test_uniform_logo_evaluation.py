"""Synthetic accounting fixtures only; no measured model accuracy/latency."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from core.uniform_logo_evaluation import compare_logo_runs, CRITERIA
from core.uniform_model_registry import json_hash


class LogoComparisonTests(unittest.TestCase):
    def setUp(self):
        self.criteria={key:'Reviewer fixture criterion; not a production threshold' for key in CRITERIA}
        self.manifest={'schema':1,'purpose':'logo-held-out-comparison','reviewed':True,'footage_sha256':'a'*64,
            'passages':[{'passage_alias':'A','garment_label':'campus_uniform','policy_label':'compliant',
                         'logo_visibility':'visible','capture_group':'day1','camera':'fixture','lighting':'fixture'},
                        {'passage_alias':'B','garment_label':'organizational_shirt','logo_visibility':'obscured'},
                        {'passage_alias':'C'}]}
        self.base={'mode':'torso_only','footage_sha256':'a'*64,'frame_sequence_sha256':'b'*64,
            'annotation_manifest_sha256':json_hash(self.manifest),'torso_model_sha256':'c'*64,
            'torso_preprocessing':'224-fixture','policy_version':'fixture-policy','configuration_sha256':'d'*64,
            'test_conditions':{'synthetic':True,'backend':'fixture'},'criteria_sha256':json_hash(self.criteria),
            'results':[{'passage_alias':'A','label':'other_clothing','policy_outcome':'violation','first_usable_to_decision_ms':100},
                       {'passage_alias':'B','label':None,'first_usable_to_decision_ms':200}]}
        self.logo=deepcopy(self.base)
        self.logo.update(mode='torso_plus_logo',logo_model_sha256='e'*64,logo_preprocessing='fixture-print-v1')
        self.logo['results']=[{'passage_alias':'A','label':'campus_uniform','policy_outcome':'compliant','first_usable_to_decision_ms':90},
                              {'passage_alias':'B','label':None,'first_usable_to_decision_ms':230}]

    def compare(self):return compare_logo_runs(self.manifest,self.base,self.logo,criteria=self.criteria)

    def test_classification_policy_missing_and_paired_signed_latency(self):
        report=self.compare();a,b=(report['per_mode'][m] for m in ('torso_only','torso_plus_logo'))
        self.assertEqual((a['classification_errors'],b['classification_errors']),(1,0))
        self.assertEqual((a['policy']['false_violation_count'],b['policy']['false_violation_count']),(1,0))
        self.assertEqual(a['missed'],1);self.assertEqual(a['unassessed'],1)
        self.assertEqual(a['unassessed_rate_on_returned'],.5)
        self.assertEqual(report['paired_added_latency_ms']['median_ms'],10)
        self.assertEqual(report['paired_added_latency_ms']['count'],2)
        self.assertEqual(b['per_class']['organizational_shirt']['recall']['n'],1)
        self.assertFalse(report['activation_allowed'])

    def test_no_ground_truth_does_not_invent_error_or_policy_metrics(self):
        for person in self.manifest['passages']:
            person.pop('garment_label',None);person.pop('policy_label',None)
        for run in (self.base,self.logo):run['annotation_manifest_sha256']=json_hash(self.manifest)
        report=self.compare()
        for mode in report['per_mode'].values():
            self.assertIsNone(mode['classification_errors'])
            self.assertIsNone(mode['policy']['false_violation_count'])
            self.assertEqual(mode['policy']['explicit_truth_count'],0)

    def test_mismatched_footage_frames_models_conditions_and_criteria_fail(self):
        for key in ('footage_sha256','frame_sequence_sha256','torso_model_sha256','torso_preprocessing',
                    'policy_version','configuration_sha256','test_conditions','criteria_sha256'):
            with self.subTest(key=key):
                bad=deepcopy(self.logo);bad[key]='f'*64
                with self.assertRaises(ValueError):compare_logo_runs(self.manifest,self.base,bad,criteria=self.criteria)

    def test_duplicate_unknown_and_unassessed_violation_rejected(self):
        for result in ({'passage_alias':'A'},{'passage_alias':'unknown'},
                       {'passage_alias':'C','label':None,'policy_outcome':'violation'}):
            bad=deepcopy(self.logo);bad['results'].append(result)
            with self.assertRaises(ValueError):compare_logo_runs(self.manifest,self.base,bad,criteria=self.criteria)

    def test_manifest_and_criteria_are_pinned_without_default_thresholds(self):
        self.manifest['passages'][0]['garment_label']='organizational_shirt'
        with self.assertRaises(ValueError):self.compare()
        with self.assertRaises(ValueError):compare_logo_runs(self.manifest,self.base,self.logo,criteria={})

    def test_missing_latency_stays_null_and_bad_measurements_fail(self):
        for run in (self.base,self.logo):
            for row in run['results']:row.pop('first_usable_to_decision_ms')
        self.assertIsNone(self.compare()['paired_added_latency_ms']['median_ms'])
        self.logo['results'][0]['logo_ms']=float('nan')
        with self.assertRaises(ValueError):self.compare()

    def test_cli_writes_only_new_report_and_cannot_overwrite(self):
        from scripts.compare_uniform_logo import main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);args=[]
            for option,value in (('manifest',self.manifest),('torso-only',self.base),
                                 ('torso-plus-logo',self.logo),('criteria',self.criteria)):
                path=root/(option+'.json');path.write_text(json.dumps(value));args.extend(['--'+option,str(path)])
            target=root/'report.json';args.extend(['--output',str(target)])
            self.assertEqual(main(args),0)
            saved=target.read_bytes()
            self.assertFalse(json.loads(saved)['activation_allowed'])
            with self.assertRaises(FileExistsError):main(args)
            self.assertEqual(target.read_bytes(),saved)


if __name__=='__main__':unittest.main()
