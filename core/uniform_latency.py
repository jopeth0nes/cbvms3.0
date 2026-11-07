"""Passage-level replay accounting. Missing ground truth remains missing."""
import math
import statistics
from core.uniform_model_registry import CLASSES


def distribution(values):
    values=sorted(float(x) for x in values if x is not None and math.isfinite(x) and x>=0)
    if not values:return {'count':0,'median_ms':None,'p95_ms':None}
    index=(len(values)-1)*.95;lo=int(index);hi=min(lo+1,len(values)-1)
    return {'count':len(values),'median_ms':statistics.median(values),
            'p95_ms':values[lo]+(values[hi]-values[lo])*(index-lo)}


class UniformPassageMetrics:
    """Caller supplies authorized alias/geometry matches, never inferred labels.

    Annotation times use replay media milliseconds. Both latency endpoints end at
    actual wall-clock completion on that replay clock. Detector-observed timings
    are kept separate from human-annotated zone/usable-frame timings.
    """
    def __init__(self):
        self.passages={}
        self.samples=[]
        self.sample_keys=set()
        self.limit=10000
        self.dropped=0

    def observe_labels(self,people):
        for row in people:
            alias=row.get('passage_alias')
            if not isinstance(alias,str) or not alias:continue
            if alias not in self.passages:
                if len(self.passages)>=self.limit:raise ValueError('Passage report limit exceeded')
                self.passages[alias]={'truth':{},'first_decision':None,'first_usable_decision':None,
                                      'first_recognition':None,'latest':None,'updates':0}
            p=self.passages[alias]
            for name in ('zone_entry_ms','first_usable_torso_ms','garment_label','policy_label'):
                value=row.get(name)
                if value is None:continue
                if name.endswith('_ms'):
                    if type(value) not in (float,int) or not math.isfinite(value) or value<0:
                        raise ValueError('Invalid annotated passage timestamp')
                elif name=='garment_label' and value not in CLASSES:
                    raise ValueError('Invalid garment ground truth')
                elif name=='policy_label' and value not in ('compliant','violation'):
                    raise ValueError('Invalid policy ground truth')
                if name in p['truth'] and p['truth'][name]!=value:
                    raise ValueError('Conflicting passage annotation; split distinct passages explicitly')
                p['truth'][name]=value

    def record(self,alias,garment,completion_ms):
        if alias not in self.passages or garment is None:return
        if not math.isfinite(completion_ms) or completion_ms<0:raise ValueError('Invalid completion time')
        p=self.passages[alias];p['latest']=garment;p['updates']+=1
        if p['first_decision'] is None:p['first_decision']=completion_ms
        usable=p['truth'].get('first_usable_torso_ms')
        if usable is not None and completion_ms>=usable and p['first_usable_decision'] is None:
            p['first_usable_decision']=completion_ms
        if garment.label is not None and p['first_recognition'] is None:p['first_recognition']=completion_ms
        key=(garment.passage_id,garment.evidence_at)
        if not garment.cached and key not in self.sample_keys and garment.timings:
            if len(self.samples)<self.limit:
                self.sample_keys.add(key);self.samples.append(garment.timings)
            else:self.dropped+=1

    def report(self):
        ps=list(self.passages.values())
        zone=[];usable=[];recognition=[];zone_recognition=[]
        labelled=correct=policy_labelled=policy_ground_truth=false_violation=missed_violation=0
        for p in ps:
            truth=p['truth'];last=p['latest']
            entry=truth.get('zone_entry_ms');first=truth.get('first_usable_torso_ms')
            if entry is not None and p['first_decision'] is not None and p['first_decision']>=entry:
                zone.append(p['first_decision']-entry)
            if first is not None and p['first_usable_decision'] is not None:
                usable.append(p['first_usable_decision']-first)
            if first is not None and p['first_recognition'] is not None and p['first_recognition']>=first:
                recognition.append(p['first_recognition']-first)
            if entry is not None and p['first_recognition'] is not None and p['first_recognition']>=entry:
                zone_recognition.append(p['first_recognition']-entry)
            if truth.get('garment_label') is not None:
                labelled+=1;correct+=int(last is not None and last.label==truth['garment_label'])
            policy_ground_truth+=int(truth.get('policy_label') is not None)
            if truth.get('policy_label') is not None and last is not None and last.policy_outcome is not None:
                policy_labelled+=1
                false_violation+=int(truth['policy_label']=='compliant' and last.policy_outcome=='violation')
                missed_violation+=int(truth['policy_label']=='violation' and last.policy_outcome=='compliant')
        return {'passage_count':len(ps),'missed_passages':sum(p['latest'] is None for p in ps),
            'unassessed_passages':sum(p['latest'] is not None and p['latest'].label is None for p in ps),
            'recognized_passages':sum(p['latest'] is not None and p['latest'].label is not None for p in ps),
            'annotated_zone_entry_to_first_decision':distribution(zone),
            'annotated_first_usable_to_first_decision':distribution(usable),
            'annotated_first_usable_to_first_recognition':distribution(recognition),
            'annotated_zone_entry_to_first_recognition':distribution(zone_recognition),
            'classification_ground_truth_passages':labelled,
            'correct_classification_passages':correct if labelled else None,
            'policy_scored_passages':policy_labelled,
            'policy_ground_truth_passages':policy_ground_truth,
            'policy_unassessed_passages':policy_ground_truth-policy_labelled,
            'false_violations':false_violation if policy_labelled else None,
            'missed_violations':missed_violation if policy_labelled else None,
            'inference_samples':len(self.samples),'samples_dropped':self.dropped,
            'stages':{name:distribution([s.get(name) for s in self.samples]) for name in
                ('preprocessing_ms','inference_ms','queue_delay_ms','assessment_ms','capture_to_decision_ms')},
            'limitations':['Decision includes unable-to-assess; recognition latency is reported separately.',
                'Missing passage/timing/garment/policy labels are not inferred from filenames or predictions.',
                'Stage distributions count fresh inference attempts, not cache hits or independent people.',
                '500 ms from first usable torso is an initial target, not an acceptance guarantee.']}
