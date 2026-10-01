"""Frame-bound Live Monitor analysis and guarded side effects; no Tk calls.

One serial analysis owner handles every face in a submitted frame. Latest-only
queues keep preview independent and prevent a growing inference backlog.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from collections import deque
import queue
import threading
import time
import weakref

import cv2
import numpy as np

from core.live_state import FrameContext, LiveState, associate_faces_to_bodies, validate_torso
from core.person_detector import MAX_TORSO_SKIN_FRACTION, skin_fraction
from core.student_status import standing_label, suspension_label
from core.uniform_matcher import fuse_uniform_prob
from core.diagnostics import event


def put_latest(q, item):
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


@dataclass(frozen=True)
class TrackedFace:
    box: tuple
    score: float
    keypoints: tuple
    track_id: int
    presence_id: str
    body_index: int | None = None
    body_box: tuple | None = None
    torso_box: tuple | None = None
    reason: str = ''

    def detection(self):
        return dict(box=self.box, score=self.score, keypoints=self.keypoints)


@dataclass(frozen=True)
class MonitorTask:
    context: FrameContext
    frame: np.ndarray
    observed_at: float
    cancelled: threading.Event
    uniform_enabled: bool = True
    earring_enabled: bool = True
    camera_generation: int = 0
    observations: tuple[TrackedFace, ...] | None = None

    def __post_init__(self):
        frame = self.frame.copy()
        frame.setflags(write=False)
        object.__setattr__(self, 'frame', frame)

    def valid(self, now=None):
        now = time.monotonic() if now is None else now
        return not self.cancelled.is_set() and 0 <= now-self.context.captured_at <= 3.0

    def rejection(self):
        return "session_cancelled" if self.cancelled.is_set() else "frame_expired"


@dataclass(frozen=True)
class MonitorResult:
    task: MonitorTask
    assessments: tuple
    finished_at: float
    detail: str = ""
    observations: tuple[TrackedFace, ...] | None = None


class MotionProjection:
    """Conservative, display-only propagation from analyzed pixels to latest pixels.

    Failure hides identity and cancels side effects; these boxes never choose a
    different person's embedding or supply a saved crop.
    """
    def __init__(self, frame, box):
        self.shape = frame.shape
        self.gray = self.gray_frame(frame)
        h, w = frame.shape[:2]
        self.scale = np.array([320/w, 240/h]*2)
        # A real detector may include the cropped forehead above the image. Use
        # the visible face for flow, instead of rejecting every projected result.
        box = (max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3]))
        self.box = np.array(box, dtype=float)*self.scale
        self.origin_box = self.box.copy()
        mask = np.zeros((240,320),np.uint8)
        x1,y1,x2,y2 = self.box.astype(int)
        mask[max(0,y1):min(240,y2),max(0,x1):min(320,x2)] = 255
        self.points = cv2.goodFeaturesToTrack(self.gray,40,.02,5,mask=mask)

    @staticmethod
    def gray_frame(frame):
        return cv2.cvtColor(cv2.resize(frame,(320,240)),cv2.COLOR_BGR2GRAY)

    @property
    def offset(self):
        return tuple((self.box[:2]-self.origin_box[:2])/self.scale[:2])

    def advance(self, frame, gray=None):
        if frame.shape != self.shape or self.points is None or len(self.points)<6:
            return None
        gray = self.gray_frame(frame) if gray is None else gray
        if np.array_equal(gray,self.gray):
            return tuple(self.box/self.scale)
        forward,good,_ = cv2.calcOpticalFlowPyrLK(self.gray,gray,self.points,None)
        if forward is None:
            self.points = None
            return None
        backward,backgood,_ = cv2.calcOpticalFlowPyrLK(gray,self.gray,forward,None)
        if backward is None:
            self.points = None
            return None
        keep = ((good.ravel()==1)&(backgood.ravel()==1)&
                (np.linalg.norm(backward-self.points,axis=2).ravel()<1.5))
        if keep.sum()<6 or keep.mean()<.65:
            self.points=None
            return None
        movements=(forward[keep]-self.points[keep]).reshape(-1,2)
        delta=np.median(movements,axis=0)
        coherent=np.linalg.norm(movements-delta,axis=1)<3
        if coherent.sum()<6 or coherent.mean()<.75:
            self.points=None
            return None
        self.points=forward[keep][coherent]
        self.gray=gray
        self.box+=np.tile(delta,2)
        box=self.box/self.scale
        h,w=frame.shape[:2]
        visible = (max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3]))
        area = max(1., (box[2]-box[0])*(box[3]-box[1]))
        if (min(visible[2]-visible[0], visible[3]-visible[1]) < 16 or
                (visible[2]-visible[0])*(visible[3]-visible[1])/area < .8):
            self.points = None
            return None
        return tuple(visible)


class LiveProcessor:
    def __init__(self, database, recognizer, person_detector, trainer, uniform_matcher,
                 notifier, *, latest_sample=lambda:None, state=None):
        self.database=database
        self.recognizer=recognizer
        self.person_detector=person_detector
        self.trainer=trainer
        self.uniform_matcher=uniform_matcher
        self.notifier=notifier
        self.latest_sample=latest_sample
        self.state=state or LiveState()
        self.cooldowns={}
        self.attendance_cooldowns={}
        self.suspension_cooldowns={}
        self.write_count=0
        self._analyzed_frames = deque(maxlen=128)
        self.readiness = None
        self.publish_identity = None
        self.identity_state = LiveState()
        self._assessment_cursor = 0

    def analyze(self,task):
        if not task.valid():
            event("frame_rejected", stage="analysis_start", reason=task.rejection())
            return None
        key = (task.context.generation, task.context.frame_id)
        if key in self._analyzed_frames:
            return None
        self._analyzed_frames.append(key)
        frame=task.frame
        # Snapshot dictionaries before enrichment; no dictionary is ever queued.
        started = time.monotonic()
        if task.observations is None:
            rows=[dict(row) for row in self.recognizer.recognize_faces(frame)]
        else:
            rows=[dict(row) for row in self.recognizer.recognize_faces(
                frame, detections=[o.detection() for o in task.observations])]
            if len(rows) != len(task.observations):
                raise RuntimeError('Recognition did not preserve the same-frame tracked faces')
            for row, observation in zip(rows, task.observations):
                if tuple(row['box']) != observation.box:
                    raise RuntimeError('Recognition changed the tracked face ownership')
                row['owner_token'] = observation.presence_id
        event("stage_complete", stage="recognition", frame_id=task.context.frame_id,
              elapsed=time.monotonic()-started, faces=len(rows))
        error = getattr(self.recognizer, "last_error", None)
        if isinstance(error, str) and error:
            raise RuntimeError(f"Face recognition unavailable: {error}")
        if not task.valid():
            event("frame_rejected", stage="recognition", reason=task.rejection())
            return None
        for row in rows:
            row.update(student_status="Unknown person",discipline_eligible=False,suspension_tag="")
            if row.get('matched'):
                student=self.database.get_student_by_student_id(row.get('student_id'))
                if student is None:
                    row.update(matched=False,identity_uncertain=True,student_id="",name="Identity uncertain")
                    continue
                row['name'] = student.get('name') or row.get('name', '')
                row['gender'] = student.get('gender') or row.get('gender', '')
                row['student_status']=standing_label(student)
                row['discipline_eligible']=(student['student_status']=='Enrolled' and not student['registration_pending'])
                suspension=self.database.get_active_suspension(student['student_id'])
                row['suspension_tag']=suspension_label(suspension) if suspension else ''
        if task.observations is None and self.publish_identity is not None and task.valid():
            identities = self.identity_state.update(task.context, rows, now=time.monotonic())
            self.publish_identity(MonitorResult(task, identities, time.monotonic(),
                "Face detected · Identity uncertain" if rows and not any(a.reliable_identity for a in identities)
                else "Recognition ready · Uniform not assessed" if rows else "No faces detected"))
        uniform_available=(task.uniform_enabled and self.person_detector is not None and
                           (self.readiness is None or self.readiness.ready('uniform')) and
                           self.trainer.is_trained('uniform'))
        bodies=[]
        started = time.monotonic()
        if (task.observations is None and uniform_available
                and any(r.get('matched') and r['discipline_eligible'] for r in rows)):
            bodies=self.person_detector.detect_persons(frame)
        event("stage_complete", stage="body", elapsed=time.monotonic()-started, bodies=len(bodies))
        associations=associate_faces_to_bodies([r['box'] for r in rows],bodies)
        # Rotate who is assessed first if a crowded frame exhausts its freshness
        # budget. Never let a slow first crop starve the same later person forever.
        indices = list(range(len(rows)))
        if indices:
            start = self._assessment_cursor % len(indices)
            indices = indices[start:] + indices[:start]
            self._assessment_cursor += 1
        for index in indices:
            row, association = rows[index], associations[index]
            if not task.valid():
                event("frame_rejected", stage="assessment", reason=task.rejection())
                return None
            crop_started = time.monotonic()
            row.update(uniform_available=False,uniform_label=None,uniform_confidence=0.0,
                       torso_box=None,torso_valid=False,association_valid=False,
                       body_index=None,body_box=None,reason="Uniform checking disabled" if not task.uniform_enabled else "Uniform model unavailable")
            if not row.get('matched') or not row['discipline_eligible']:
                row['reason']='Identity or enrollment is not eligible for uniform assessment'
                continue
            if uniform_available:
                row['reason']=association.reason
                body_error = getattr(self.person_detector, 'last_error', None)
                if not bodies and isinstance(body_error, str) and body_error:
                    row['reason']='Body detection failed; uniform not assessed'
                observation = task.observations[index] if task.observations is not None else None
                body = observation.body_box if observation is not None else (
                    bodies[association.index] if association.index is not None else None)
                if observation is not None:
                    row['reason'] = observation.reason
                if body is not None:
                    body_index = observation.body_index if observation is not None else association.index
                    row.update(body_index=body_index,body_box=tuple(body),association_valid=True)
                    try:
                        region,method=(observation.torso_box, 'tracked') if observation is not None else self.person_detector.chest_region(frame,row['box'],body)
                        valid,reason=validate_torso(
                            row['box'], body, region, frame.shape,
                            other_faces=[r['box'] for i,r in enumerate(rows) if i!=index],
                            other_bodies=[o.body_box for i,o in enumerate(task.observations)
                                          if i!=index and o.body_box is not None] if task.observations is not None
                                         else [b for i,b in enumerate(bodies) if i!=association.index])
                        row['reason']=reason if region is not None else (
                            observation.reason if observation is not None else 'Move back to show more of your shirt')
                        if valid:
                            x1,y1,x2,y2=map(int,region)
                            crop=frame[y1:y2,x1:x2]
                            # Keep validated geometry visible even if the classifier abstains.
                            # Evidence still requires uniform_available and repeated verdicts.
                            row.update(torso_valid=True,torso_box=tuple(region))
                            skin_ratio=skin_fraction(crop)
                            event('torso_crop', frame_id=task.context.frame_id, face_index=index,
                                  method=method, width=x2-x1, height=y2-y1, skin_fraction=skin_ratio)
                            if skin_ratio>MAX_TORSO_SKIN_FRACTION:
                                row['reason']='Shirt obscured or mostly skin; show more of your shirt'
                            else:
                                p_cls=p_col=None
                                if self.trainer.is_trained('uniform'):
                                    proba=self.trainer.predict_proba('uniform',crop)
                                    p_cls=proba.get('correct_uniform') if proba else None
                                if self.uniform_matcher.is_loaded():
                                    verdict,p=self.uniform_matcher.is_uniform(crop)
                                    p_col=p if verdict is not None else None
                                # A colour match is supplementary evidence; it cannot
                                # classify a garment when the trained classifier failed.
                                fused=fuse_uniform_prob(p_cls,p_col) if p_cls is not None else None
                                if fused is not None and np.isfinite(fused):
                                    confidence=max(fused,1-fused)
                                    row.update(uniform_available=True,
                                               uniform_label=('correct_uniform' if fused>=.5 else 'wrong_uniform') if confidence>=.60 else None,
                                               uniform_confidence=float(confidence),reason='Checking uniform')
                                else:
                                    row['reason']='Uniform classifier could not assess this crop'
                    except Exception as exc:
                        row['reason']='Uniform assessment unavailable'
                        event("uniform_assessment_failed", error=str(exc))
            # Existing earring rule: male and eligible, independent category.
            row['earring_violation']=False
            if (task.earring_enabled and (self.readiness is None or self.readiness.ready('earring'))
                    and row.get('gender','').lower()=='male' and self.trainer.is_trained('earring')):
                try:
                    x1,y1,x2,y2=map(int,row['box'])
                    h,w=frame.shape[:2]
                    crop=frame[max(0,y1):min(h,y2),max(0,x1):min(w,x2)]
                    label,conf=self.trainer.predict('earring',crop) if crop.size else (None,0)
                    row['earring_violation']=label=='with_earring' and conf>=.65
                    row['earring_confidence']=float(conf)
                except Exception:
                    row['earring_violation']=False
            event("stage_complete", stage="uniform", face_index=index,
                  elapsed=time.monotonic()-crop_started, reason=row['reason'])
        if not task.valid():
            event("frame_rejected", stage="uniform", reason=task.rejection())
            return None
        assessments=self.state.update(task.context,rows,now=time.monotonic())
        if task.observations is not None:
            assessments=tuple(replace(a, track_id=o.track_id, presence_id=o.presence_id)
                              for a,o in zip(assessments,task.observations))
        for a in assessments:
            event('person_assessed', frame_id=task.context.frame_id, presence_id=a.presence_id,
                  state=a.state, reason=a.reason, reliable_identity=a.reliable_identity,
                  torso_valid=a.torso_box is not None, accepted=a.accepted_categories)
        event("frame_analyzed", frame_id=task.context.frame_id, faces=len(rows),
              reliable_identities=sum(a.reliable_identity for a in assessments),
              valid_torsos=sum(bool(r.get('torso_valid')) for r in rows),
              accepted_assessments=sum(bool(a.accepted_categories) for a in assessments),
              assessment_elapsed=time.monotonic()-started)
        return MonitorResult(task,assessments,time.monotonic(),
                             "No faces detected" if not rows else
                             "Face detected · Identity uncertain" if not any(a.reliable_identity for a in assessments) else
                             "Uniform not assessed" if not any(r.get('uniform_available') for r in rows) else "")

    def assessment_failed(self, task):
        """An unobserved frame breaks evidence without recycling presence IDs."""
        if task.valid():
            self.state.update(task.context, (), now=time.monotonic())

    def persist(self,result):
        """Side effects consume the very same accepted assessment as the UI.

        Revalidate immediately before each write, inside DB transactions, and before
        notifications. Failed writes do not consume cooldowns.
        """
        task=result.task
        if not task.valid():
            return
        observed=datetime.fromtimestamp(task.observed_at,timezone.utc)
        latest=self.latest_sample()
        if latest is None or latest.frame_id[0]!=task.context.frame_id[0]:
            return
        now=time.monotonic()
        if not 0<=now-latest.captured_at<=1:
            return
        for assessment in result.assessments:
            if assessment.context != task.context:
                continue  # Never persist an assessment against a different captured frame.
            if not task.valid():
                return
            box=assessment.face_box
            projection=MotionProjection(task.frame,box)
            if latest.frame_id!=task.context.frame_id and projection.advance(latest.frame) is None:
                event('database_write_withheld', presence_id=assessment.presence_id,
                      frame_id=task.context.frame_id, reason='motion_not_verified')
                continue
            def guard(assessment=assessment, task_ref=weakref.ref(task)):
                task = task_ref()
                if task is None or not task.valid():
                    return False
                current = self.latest_sample()
                if (current is None or current.frame_id[0] != task.context.frame_id[0]
                        or not 0 <= time.monotonic()-current.captured_at <= 1):
                    return False
                if current.frame_id == task.context.frame_id:
                    return True
                return MotionProjection(task.frame, assessment.face_box).advance(current.frame) is not None
            sid=assessment.student_id
            if assessment.reliable_identity:
                key=(sid,observed.astimezone().date().isoformat())
                if now-self.attendance_cooldowns.get(key,-30)>=30 and guard():
                    try:
                        if assessment.student_status in ('Graduate','Unenrolled'):
                            ok=self.database.record_premises_entry(sid,observed_at=observed,valid_if=guard)
                        elif assessment.discipline_eligible:
                            ok=self.database.record_attendance(sid,observed_at=observed,valid_if=guard)
                        else:
                            ok=False
                        if ok:
                            self.attendance_cooldowns[key]=now
                        event("database_presence_outcome", student_id=sid, committed=bool(ok))
                    except Exception as exc:
                        event("database_presence_failed", student_id=sid, error=str(exc))
                tag=assessment.suspension_tag
                suspension_key=(sid,tag)
                if tag and guard() and now-self.suspension_cooldowns.get(suspension_key,-30)>=30:
                    notification = self.notifier.notify(assessment.name, tag, valid_if=guard, observed_at=task.observed_at)
                    if notification is not None:
                        self.suspension_cooldowns[suspension_key]=now
            categories=assessment.accepted_categories
            if assessment.state=='Unknown person':
                categories=('unknown_person',)
            for code in categories:
                if not guard():
                    event('database_write_withheld', presence_id=assessment.presence_id,
                          frame_id=task.context.frame_id, reason='freshness_or_motion_changed')
                    return
                key=(sid if assessment.reliable_identity else assessment.presence_id,code)
                if now-self.cooldowns.get(key,-300)<300:
                    continue
                if code!='unknown_person' and not (assessment.reliable_identity and assessment.discipline_eligible):
                    continue
                box=assessment.body_box if code=='wrong_uniform' else assessment.face_box
                x1,y1,x2,y2=map(int,box or assessment.face_box)
                h,w=task.frame.shape[:2]
                crop=task.frame[max(0,y1):min(h,y2),max(0,x1):min(w,x2)]
                if not crop.size:
                    continue
                ok,jpeg=cv2.imencode('.jpg',crop,[cv2.IMWRITE_JPEG_QUALITY,85])
                if not ok:
                    continue
                display=('Suspected uniform violation' if code=='wrong_uniform' else
                         'Earring detected' if code=='earring' else 'Unknown person')
                try:
                    if code=='unknown_person':
                        written=self.database.log_security_event(assessment.presence_id,
                            observed_at=observed,snapshot_jpeg=jpeg.tobytes(),valid_if=guard)
                    else:
                        written=self.database.log_violation(student_id=sid,student_name=assessment.name,
                            violation_type=display,violation_code=code,snapshot_jpeg=jpeg.tobytes(),
                            detected_at=observed,status='pending_review',valid_if=guard,
                            evidence_provenance=dict(
                                captured_at=observed.isoformat(),
                                camera_session=str(task.context.frame_id[0]),
                                frame_id=list(task.context.frame_id),
                                monitor_generation=task.context.generation,
                                camera_generation=task.camera_generation,
                                presence_id=assessment.presence_id, track_id=assessment.track_id,
                                face_box=list(assessment.face_box),
                                body_box=list(assessment.body_box) if assessment.body_box else None,
                                torso_box=list(assessment.torso_box) if assessment.torso_box else None,
                                crop_box=[max(0,x1),max(0,y1),min(w,x2),min(h,y2)],
                                frame_size=[w,h]))
                    if written is None:
                        event("database_write_rejected", category=code, student_id=sid)
                        continue
                except Exception as exc:
                    event("database_write_failed", category=code, student_id=sid, error=str(exc))
                    continue
                event("database_write_committed", category=code, student_id=sid, record_id=written)
                self.cooldowns[key]=now
                self.write_count+=1
                if guard():
                    self.notifier.notify(assessment.name, display, valid_if=guard, observed_at=task.observed_at)


class FaceTrackingProcessor:
    """Owns presence IDs and localizes anonymous torsos on the same captured frame."""
    def __init__(self, recognizer, person_detector=None, readiness=None, on_localized=None):
        self.recognizer = recognizer
        self.person_detector = person_detector
        self.readiness = readiness
        self.on_localized = on_localized
        self.state = LiveState()

    def analyze(self, task):
        started = time.monotonic()
        rows = [dict(row, identity_uncertain=True) for row in self.recognizer.detect_faces(task.frame)]
        bodies=[]
        ready = self.person_detector is not None and (self.readiness is None or self.readiness.ready('body'))
        if rows and ready:
            bodies=self.person_detector.detect_persons(task.frame)
        associations=associate_faces_to_bodies([r['box'] for r in rows],bodies)
        for index,(row,association) in enumerate(zip(rows,associations)):
            row['reason'] = association.reason if ready else 'Loading torso detector'
            error = getattr(self.person_detector, 'last_error', None)
            if isinstance(error, str) and error:
                row['reason'] = 'Body detection failed; retrying on the next frame'
            if association.index is None:
                continue
            body=bodies[association.index]
            region,method=self.person_detector.chest_region(task.frame,row['box'],body)
            valid,reason=validate_torso(row['box'],body,region,task.frame.shape,
                other_faces=[r['box'] for i,r in enumerate(rows) if i!=index],
                other_bodies=[b for i,b in enumerate(bodies) if i!=association.index])
            row.update(body_index=association.index,body_box=tuple(body),association_valid=True,
                       torso_valid=valid,torso_box=tuple(region) if valid else None,
                       reason='Checking uniform' if valid else reason)
            event('torso_localized', frame_id=task.context.frame_id, face_index=index,
                  valid=valid, reason=row['reason'], method=method)
        if not task.valid():
            event("frame_rejected", stage="tracking", reason=task.rejection())
            return None
        tracks = self.state.update(task.context, rows, now=time.monotonic())
        uniform_ready=task.uniform_enabled and (self.readiness is None or self.readiness.ready('uniform'))
        unavailable='Uniform checking disabled' if not task.uniform_enabled else 'Uniform model loading or unavailable'
        tracks = tuple(replace(a, state=('Checking uniform' if uniform_ready else 'Uniform not assessed') if a.torso_box else
                               'Locating torso' if ready else 'Identifying', name='Identifying',
                               reason=r['reason'] if not a.torso_box or uniform_ready else unavailable)
                       for a,r in zip(tracks,rows))
        observations=tuple(TrackedFace(tuple(r['box']),float(r.get('score',1.)),tuple(r.get('keypoints',())),
            a.track_id,a.presence_id,a.body_index,a.body_box,a.torso_box,r['reason'])
            for r,a in zip(rows,tracks))
        event("stage_complete", stage="tracking", frame_id=task.context.frame_id,
              elapsed=time.monotonic()-started, faces=len(tracks))
        result=MonitorResult(task, tracks, time.monotonic(),
                             'Identifying · Checking uniform' if any(a.torso_box for a in tracks) else
                             'Locating torso' if tracks and ready else
                             "Face detected · Identifying" if tracks else "No faces detected", observations)
        if self.on_localized is not None and task.valid():
            self.on_localized(result)
        return result

    def persist(self, result):
        pass


class LiveWorker:
    """One stoppable worker with single-slot input/output queues."""
    def __init__(self,processor, *, persistence_worker=False):
        self.processor=processor
        self.requests=queue.Queue(maxsize=1)
        self.results=queue.Queue(maxsize=1)
        self.stop_event=threading.Event()
        self.done=threading.Event()
        self.active_task = None
        self.last_error = ""
        self.active_since = 0.
        self.writes = queue.Queue(maxsize=1)
        self.active_write = None
        self.writes_done = threading.Event()
        self.persistence_worker = persistence_worker
        if not persistence_worker:
            self.writes_done.set()
        if persistence_worker:
            processor.publish_identity = lambda result: put_latest(self.results, result)
        self.thread=threading.Thread(target=self._run,daemon=True,name='live-monitor-analysis')

    def start(self):
        self.thread.start()
        if self.persistence_worker:
            threading.Thread(target=self._persist, daemon=True, name="live-monitor-database").start()

    def _persist(self):
        try:
            while not self.stop_event.is_set():
                try:
                    result = self.writes.get(timeout=.2)
                except queue.Empty:
                    continue
                self.active_write = result.task
                try:
                    if not self.stop_event.is_set() and result.task.valid():
                        self.processor.persist(result)
                    else:
                        event("frame_rejected", stage="persistence", reason="stopped_or_expired")
                except Exception as exc:
                    event("database_worker_failed", error=str(exc))
                finally:
                    self.active_write = None
        finally:
            self.writes_done.set()

    def offer(self,task):
        if not self.stop_event.is_set():
            event("frame_submitted", worker=type(self.processor).__name__, frame_id=task.context.frame_id)
            put_latest(self.requests,task)

    def stop(self):
        self.stop_event.set()
        if self.active_task is not None:
            self.active_task.cancelled.set()
        if self.active_write is not None:
            self.active_write.cancelled.set()
        try:
            self.writes.get_nowait().task.cancelled.set()
        except queue.Empty:
            pass
        put_latest(self.requests,None)

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    task=self.requests.get(timeout=.2)
                except queue.Empty:
                    continue
                if task is None or not task.valid():
                    if task is not None:
                        event("frame_rejected", stage="queue", reason=task.rejection())
                    continue
                try:
                    self.active_task = task
                    self.active_since = time.monotonic()
                    result=self.processor.analyze(task)
                    if result is not None and task.valid() and not self.stop_event.is_set():
                        self.last_error = ""
                        if self.persistence_worker:
                            put_latest(self.writes, result)
                        else:
                            self.processor.persist(result)
                        if task.valid() and not self.stop_event.is_set():
                            put_latest(self.results,result)
                except Exception as exc:
                    self.last_error = str(exc)
                    event("analysis_failed", error=str(exc), frame_id=task.context.frame_id)
                    print(f"[LiveMonitor] assessment unavailable: {exc}")
                    if task.valid():
                        failed = getattr(self.processor, "assessment_failed", None)
                        if failed is not None:
                            failed(task)
                        put_latest(self.results,MonitorResult(task,(),time.monotonic(),
                                                           'Assessment unavailable'))
                finally:
                    self.active_task = None
        finally:
            self.done.set()
