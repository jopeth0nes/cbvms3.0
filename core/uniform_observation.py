"""Candidate garment assessment on tracked pixels; no database, camera or Tk owner.

The tracker supplies immutable tickets. New tracking observations can invalidate
in-flight work without waiting for ML. One analysis worker owns the predictor.
Policy results are descriptive observations only and never disciplinary events.
"""
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
import math
import os
import threading
import time
import uuid

import cv2

from core.live_state import validate_torso
from core.person_detector import skin_fraction, MAX_TORSO_SKIN_FRACTION
from core.uniform_model_registry import (CandidatePredictor, candidate_descriptor,
    json_hash, read_json, policy_rule, CLASSES)
from core.uniform_logo import LogoVerificationConfig, unavailable_logo_status

LABELS = {'campus_uniform':'Campus Uniform recognized',
          'organizational_shirt':'Organizational Shirt recognized',
          'other_clothing':'Wrong Uniform recognized', None:'Unable to assess'}
CONFIG_ENV = 'CBVMS_UNIFORM_OBSERVATION_CONFIG'


@dataclass(frozen=True)
class ObservationConfig:
    candidate_bundle: str
    candidate_sha256: str
    recognition_confidence: float
    backend: str = 'cpu'
    assessment_zone: tuple | None = None
    policy: dict | None = None
    # Stage 1 quality-review heuristics, not calibrated accuracy thresholds.
    minimum_torso_px: int = 160
    minimum_sharpness: float = 25.
    evidence_ttl: float = 1.
    refresh_interval: float = .5
    frame_ttl: float = 1.
    max_passages: int = 48
    logo_verification: dict | None = None

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        data = read_json(path)
        if data.pop('schema', None) != 1 or data.pop('mode', None) != 'observation_only':
            raise ValueError('Explicit schema-1 observation_only configuration required')
        if set(data) - set(cls.__dataclass_fields__):
            raise ValueError('Unknown observation configuration fields')
        bundle = Path(data['candidate_bundle'])
        data['candidate_bundle'] = str(bundle if bundle.is_absolute() else path.parent/bundle)
        if data.get('assessment_zone') is not None:
            data['assessment_zone'] = tuple(data['assessment_zone'])
        config = cls(**data)
        config.validate()
        return config

    def validate(self):
        LogoVerificationConfig.parse(self.logo_verification).require_available()
        if self.backend not in ('cpu','mps'):
            raise ValueError('Candidate backend must be cpu or mps')
        if (not isinstance(self.candidate_sha256,str) or len(self.candidate_sha256) != 64 or
                any(c not in '0123456789abcdef' for c in self.candidate_sha256)):
            raise ValueError('Pin the candidate weight SHA256')
        for name, low, high in (('recognition_confidence',.5,1),('minimum_sharpness',0,10000),
                                ('evidence_ttl',.1,3),('refresh_interval',.1,3),('frame_ttl',.1,3)):
            value = getattr(self,name)
            if type(value) not in (int,float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('Invalid observation '+name)
        if self.refresh_interval > self.evidence_ttl:
            raise ValueError('Refresh interval cannot exceed evidence TTL')
        if type(self.minimum_torso_px) is not int or not 32 <= self.minimum_torso_px <= 1024:
            raise ValueError('Invalid minimum torso resolution')
        if type(self.max_passages) is not int or not 1 <= self.max_passages <= 48:
            raise ValueError('Passage capacity must be from 1 to 48')
        if self.assessment_zone is not None:
            z = self.assessment_zone
            if (len(z)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in z)
                    or not (0<=z[0]<z[2]<=1 and 0<=z[1]<z[3]<=1)):
                raise ValueError('Assessment zone must be normalized x1,y1,x2,y2')
        if self.policy is not None:
            if not isinstance(self.policy,dict) or self.policy != policy_rule(self.policy.get('minimum_confidence')):
                raise ValueError('Only the existing versioned garment policy is supported')

    @property
    def version(self):
        from dataclasses import asdict
        return json_hash(asdict(self))


@dataclass(frozen=True)
class TorsoTicket:
    key: tuple
    passage_id: str
    revision: int
    captured_at: float
    frame_id: tuple
    torso_box: tuple | None
    usable: bool
    reason: str


@dataclass(frozen=True)
class GarmentObservation:
    passage_id: str
    label: str | None
    confidence: float
    reason: str
    model_version: str
    policy_version: str
    policy_outcome: str | None = None
    cached: bool = False
    timings: dict = field(default_factory=dict)
    decision_at: float = 0.
    evidence_at: float = 0.
    logo_verification: dict = field(default_factory=unavailable_logo_status)

    @property
    def state(self): return LABELS[self.label]


@dataclass
class _Passage:
    passage_id: str
    entered: float
    seen: float
    first_usable: float | None = None
    revision: int = 0
    signature: tuple | None = None
    last_ticket: TorsoTicket | None = None
    result: GarmentObservation | None = None
    last_attempt_frame: tuple | None = None
    identity_owner: str | None = None


class ObservationUniformSession:
    """Bounded same-camera/session/track evidence, with no production activation."""
    def __init__(self, config, *, predictor_factory=CandidatePredictor, clock=time.monotonic):
        config.validate()
        self.config = config
        self.clock = clock
        self.session_id = uuid.uuid4().hex
        self._version = config.version
        self._epoch = 0
        self._lock = threading.RLock()
        self._load_lock = threading.Lock()
        self._passages = OrderedDict()
        self._scope = None
        self._latest = -math.inf
        self._predictor_factory = predictor_factory
        self.predictor = None
        self.descriptor = None
        self.error = ''

    @classmethod
    def from_environment(cls, *, observation_only):
        path = os.environ.get(CONFIG_ENV)
        if not path: return None
        if not observation_only:
            raise ValueError(CONFIG_ENV+' requires CBVMS_ASSESSMENT_OBSERVATION_ONLY=1')
        return cls(ObservationConfig.load(path))

    @property
    def version_token(self): return f'{self.session_id}:{self._epoch}:{self._version}'

    def matches(self, token): return token == self.version_token

    def invalidate(self):
        with self._lock:
            self._epoch += 1
            self._passages.clear()
            self._scope = None
            self._latest = -math.inf

    def prepare(self):
        # Called only by model warmup/analysis workers, never rendering or Tk.
        with self._load_lock:
            if self.predictor is not None: return
            if self.error: raise RuntimeError(self.error)
            try:
                info = candidate_descriptor(self.config.candidate_bundle)
                if info['id'] != self.config.candidate_sha256:
                    raise ValueError('Observation candidate hash changed')
                predictor = self._predictor_factory(self.config.candidate_bundle, backend=self.config.backend)
                self.descriptor, self.predictor = info, predictor
            except Exception as exc:
                self.error = str(exc)
                raise

    def status_text(self):
        if self.error: return 'Candidate observation unavailable: '+self.error
        if self.predictor is None: return 'Observation only · Loading candidate'
        device = self.predictor.backend_status()['active']
        synthetic = ' · synthetic test model' if self.descriptor.get('synthetic') else ''
        return (f'Observation only · Candidate {self.config.candidate_sha256[:12]} · {device.upper()}{synthetic}'
                ' · Logo verifier: Not trained (disabled)')

    def _quality(self, task, observation, observations):
        o = observation
        if o.body_index is None:
            return False,'No tracked body association',None
        valid, reason = validate_torso(o.box,o.body_box,o.torso_box,task.frame.shape,
            other_faces=[x.box for x in observations if x is not o],
            other_bodies=[x.body_box for x in observations if x is not o and x.body_box is not None])
        if not valid: return False, reason, None
        x1,y1,x2,y2 = o.torso_box
        for other in observations:
            if other is o or other.torso_box is None:continue
            a,b,c,d = other.torso_box
            overlap = max(0,min(x2,c)-max(x1,a))*max(0,min(y2,d)-max(y1,b))
            if overlap>.03*(x2-x1)*(y2-y1):
                return False,'Another tracked torso overlaps this crop',None
        face,body = o.box,o.body_box
        fw,fh = max(1,face[2]-face[0]),max(1,face[3]-face[1])
        signature = ((body[0]-face[0])/fw,(body[1]-face[1])/fh,
                     (body[2]-face[2])/fw,(body[3]-face[3])/fh)
        x1,y1,x2,y2 = map(int,o.torso_box)
        crop = task.frame[y1:y2,x1:x2]
        if min(crop.shape[:2]) < self.config.minimum_torso_px:
            return False,'Insufficient torso resolution',signature
        if skin_fraction(crop)>MAX_TORSO_SKIN_FRACTION:
            return False,'Torso obscured or mostly skin',signature
        # Same scale/cutoff as Stage 1's review warning, not a measured accuracy gate.
        h,w = crop.shape[:2]
        scale = min(1.,512/max(h,w))
        small = cv2.resize(crop,(round(w*scale),round(h*scale))) if scale<1 else crop
        sharpness = float(cv2.Laplacian(cv2.cvtColor(small,cv2.COLOR_BGR2GRAY),cv2.CV_32F).var())
        if sharpness < self.config.minimum_sharpness:
            return False,'Possible blur; waiting for fresh usable torso evidence',signature
        return True,'Usable associated torso',signature

    def _inside_zone(self, observation, shape):
        if self.config.assessment_zone is None: return True
        box = observation.body_box or observation.box
        h,w = shape[:2]; x,y = (box[0]+box[2])/(2*w),(box[1]+box[3])/(2*h)
        a,b,c,d = self.config.assessment_zone
        return a<=x<=c and b<=y<=d

    def observe(self, task, observations):
        """Tracking-thread handoff: validate fresh geometry even while ML is busy."""
        observations = tuple(observations or ())
        now = self.clock()
        tickets = []
        # Retain the bounded Event identity itself; a recycled Python id must not
        # let a later session inherit evidence when external generations repeat.
        scope = (task.source_id,self.session_id,task.cancelled,task.context.generation,
                 task.camera_generation,self._version)
        with self._lock:
            if not task.uniform_enabled:
                self.invalidate()
                return self._attach(task,())
            if not task.valid(now) or not 0<=now-task.context.captured_at<=self.config.frame_ttl:
                return self._attach(task, ())
            if scope != self._scope:
                self.invalidate()
                self._scope = scope
            stamp = task.context.captured_at
            if stamp <= self._latest:
                return self._attach(task, ())
            self._latest = stamp
            keys = {scope+(o.track_id,o.presence_id) for o in observations if self._inside_zone(o,task.frame.shape)}
            for key in list(self._passages):
                if key not in keys or now-self._passages[key].seen>self.config.evidence_ttl:
                    del self._passages[key]
            for o in observations[:self.config.max_passages]:
                key = scope+(o.track_id,o.presence_id)
                if key not in keys: continue
                p = self._passages.get(key)
                if p is None:
                    p = self._passages[key] = _Passage(uuid.uuid4().hex,stamp,stamp)
                p.seen = stamp
                usable,reason,signature = self._quality(task,o,observations)
                changed = (signature is not None and p.signature is not None and
                    max(abs(a-b) for a,b in zip(signature,p.signature))>1.25)
                if not usable or changed:
                    p.revision += 1
                    p.result = None
                p.signature = signature
                if usable and p.first_usable is None: p.first_usable = stamp
                ticket = TorsoTicket(key,p.passage_id,p.revision,stamp,task.context.frame_id,
                                     o.torso_box,usable,reason)
                p.last_ticket = ticket
                tickets.append(ticket)
                self._passages.move_to_end(key)
            while len(self._passages)>self.config.max_passages:
                self._passages.popitem(last=False)
        return self._attach(task,tuple(tickets))

    @staticmethod
    def _attach(task,tickets):
        from copy import copy
        result = copy(task)
        object.__setattr__(result,'uniform_tickets',tickets)
        return result

    def _current(self,ticket,now):
        p = self._passages.get(ticket.key)
        return p if (p is not None and not ticket.key[2].is_set() and p.passage_id==ticket.passage_id and
            p.revision==ticket.revision and 0<=now-p.seen<=self.config.evidence_ttl and
            0<=now-ticket.captured_at<=self.config.frame_ttl) else None

    def result_valid(self, result, ticket):
        with self._lock:
            return bool(self._current(ticket,self.clock()) is not None and
                result.passage_id==ticket.passage_id and
                result.model_version==self.config.candidate_sha256 and
                result.policy_version==self._version and
                (result.label is None or self.clock()-result.evidence_at<=self.config.evidence_ttl))

    def bind_identity(self,ticket,owner):
        """Identity changes revoke cached garment evidence, never relabel it."""
        with self._lock:
            p = self._current(ticket,self.clock())
            if p is not None:
                if p.identity_owner is not None and owner!=p.identity_owner:
                    p.revision += 1
                    p.result = None
                    p.last_attempt_frame = None
                    ticket = replace(ticket,revision=p.revision)
                p.identity_owner = owner
            return ticket

    def assess(self, task, ticket):
        started = self.clock()
        logo_status = unavailable_logo_status(LogoVerificationConfig.parse(self.config.logo_verification))
        policy_text = 'Policy not configured; department/day/schedule permissions unassessed'
        if self.config.policy is not None:
            policy_text = 'Existing garment policy only; department/day/schedule permissions unassessed'
        with self._lock:
            p = self._current(ticket,started)
            if p is None or not task.valid(started): return None
            if p.result is not None and ticket.captured_at<p.result.evidence_at:return None
            def unknown(reason):
                return GarmentObservation(p.passage_id,None,0.,reason+' · '+policy_text,
                    self.config.candidate_sha256,self._version,decision_at=self.clock(),evidence_at=ticket.captured_at,
                    logo_verification=logo_status)
            if not ticket.usable: return unknown(ticket.reason)
            if (p.result is not None and p.result.label is not None and
                    0<=ticket.captured_at-p.result.evidence_at<self.config.refresh_interval and
                    started-p.result.evidence_at<=self.config.evidence_ttl):
                return replace(p.result,cached=True,reason='Reused recent associated evidence · '+policy_text)
            if p.last_attempt_frame==ticket.frame_id: return p.result
            p.last_attempt_frame=ticket.frame_id
        try:
            self.prepare()
            # Loading can outlive this frame. Never run inference on that stale crop.
            with self._lock:
                if self._current(ticket,self.clock()) is None or not task.valid(self.clock()): return None
            x1,y1,x2,y2 = map(int,ticket.torso_box)
            measured = self.predictor.timed_probabilities(task.frame[y1:y2,x1:x2])
            probabilities = measured['probabilities']
            if (set(probabilities)!=set(CLASSES) or any(not math.isfinite(v) or not 0<=v<=1 for v in probabilities.values())
                    or abs(sum(probabilities.values())-1)>.001):
                raise ValueError('Invalid garment probabilities')
            raw = max(probabilities,key=probabilities.get)
            confidence = probabilities[raw]
            label = raw if confidence>=self.config.recognition_confidence else None
            policy = None
            if label is not None and self.config.policy is not None:
                compliant = sum(probabilities[k] for k in self.config.policy['accepted_classes'])
                if max(compliant,1-compliant)>=self.config.policy['minimum_confidence']:
                    policy = 'compliant' if compliant>=.5 else 'violation'
            finished = self.clock()
            with self._lock:
                p = self._current(ticket,finished)
                if p is None or not task.valid(finished): return None
                if p.result is not None and ticket.captured_at<p.result.evidence_at:return None
                timings = dict(measured['timings'])
                timings.update(queue_delay_ms=(1000*max(0,task.analysis_started_at-task.assessment_queued_at)
                        if task.analysis_started_at is not None and task.assessment_queued_at is not None else None),
                    capture_to_classifier_start_ms=1000*max(0,started-ticket.captured_at),
                    assessment_ms=1000*(finished-started),
                    capture_to_decision_ms=1000*(finished-ticket.captured_at),
                    zone_entry_to_decision_ms=(1000*(finished-p.entered) if self.config.assessment_zone is not None else None),
                    first_usable_to_decision_ms=1000*(finished-p.first_usable))
                reason = ('Garment recognized' if label else 'Uncertain garment classification')+' · '+policy_text
                if policy: reason += ' · Garment policy: '+policy
                result = GarmentObservation(p.passage_id,label,confidence,reason,
                    self.config.candidate_sha256,self._version,policy,timings=timings,
                    decision_at=finished,evidence_at=ticket.captured_at,logo_verification=logo_status)
                p.result = result
                return result
        except Exception as exc:
            with self._lock:
                p = self._current(ticket,self.clock())
                if p is None: return None
                p.result = unknown('Candidate assessment failed: '+str(exc))
                return p.result

    def snapshot(self):
        with self._lock:
            return {'version':self.version_token,'passages':len(self._passages),
                    'max_passages':self.config.max_passages,'model':self.config.candidate_sha256,
                    'policy_configured':self.config.policy is not None,'error':self.error,
                    'logo_verification':unavailable_logo_status(LogoVerificationConfig.parse(self.config.logo_verification))}
