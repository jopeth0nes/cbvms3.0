"""Frame-bound Live Monitor analysis and guarded side effects; no Tk calls.

One serial analysis owner handles every face in a submitted frame. Latest-only
queues keep preview independent and prevent a growing inference backlog.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import deque
import queue
import threading
import time
import weakref

import cv2
import numpy as np

from core.live_state import FrameContext, LiveState, associate_faces_to_bodies, validate_torso
from core.person_detector import skin_fraction
from core.student_status import standing_label, suspension_label
from core.uniform_matcher import fuse_uniform_prob


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
class MonitorTask:
    context: FrameContext
    frame: np.ndarray
    observed_at: float
    cancelled: threading.Event
    uniform_enabled: bool = True
    earring_enabled: bool = True
    camera_generation: int = 0

    def __post_init__(self):
        frame = self.frame.copy()
        frame.setflags(write=False)
        object.__setattr__(self, 'frame', frame)

    def valid(self, now=None):
        now = time.monotonic() if now is None else now
        return not self.cancelled.is_set() and 0 <= now-self.context.captured_at <= 3.0


@dataclass(frozen=True)
class MonitorResult:
    task: MonitorTask
    assessments: tuple
    finished_at: float
    detail: str = ""


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
        self.box = np.array(box, dtype=float)*self.scale
        mask = np.zeros((240,320),np.uint8)
        x1,y1,x2,y2 = self.box.astype(int)
        mask[max(0,y1):min(240,y2),max(0,x1):min(320,x2)] = 255
        self.points = cv2.goodFeaturesToTrack(self.gray,40,.02,5,mask=mask)

    @staticmethod
    def gray_frame(frame):
        return cv2.cvtColor(cv2.resize(frame,(320,240)),cv2.COLOR_BGR2GRAY)

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
        if box[0]<0 or box[1]<0 or box[2]>w or box[3]>h:
            self.points = None
            return None
        return tuple(box)


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

    def analyze(self,task):
        if not task.valid():
            return None
        key = (task.context.generation, task.context.frame_id)
        if key in self._analyzed_frames:
            return None
        self._analyzed_frames.append(key)
        frame=task.frame
        # Snapshot dictionaries before enrichment; no dictionary is ever queued.
        rows=[dict(row) for row in self.recognizer.recognize_faces(frame)]
        error = getattr(self.recognizer, "last_error", None)
        if isinstance(error, str) and error:
            raise RuntimeError(f"Face recognition unavailable: {error}")
        if not task.valid():
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
        uniform_available=(task.uniform_enabled and self.person_detector is not None and
                           (self.trainer.is_trained('uniform') or self.uniform_matcher.is_loaded()))
        bodies=[]
        if uniform_available and any(r.get('matched') and r['discipline_eligible'] for r in rows):
            bodies=self.person_detector.detect_persons(frame)
        associations=associate_faces_to_bodies([r['box'] for r in rows],bodies)
        for index,(row,association) in enumerate(zip(rows,associations)):
            if not task.valid():
                return None
            row.update(uniform_available=False,uniform_label=None,uniform_confidence=0.0,
                       torso_box=None,torso_valid=False,association_valid=False,
                       body_index=None,body_box=None,reason="Uniform checking disabled" if not task.uniform_enabled else "Uniform model unavailable")
            if not row.get('matched') or not row['discipline_eligible']:
                row['reason']='Identity or enrollment is not eligible for uniform assessment'
                continue
            if uniform_available:
                row['reason']=association.reason
                if association.index is not None:
                    body=bodies[association.index]
                    row.update(body_index=association.index,body_box=tuple(body),association_valid=True)
                    try:
                        region,method=self.person_detector.chest_region(frame,row['box'],body)
                        valid,reason=validate_torso(
                            row['box'], body, region, frame.shape,
                            other_faces=[r['box'] for i,r in enumerate(rows) if i!=index],
                            other_bodies=[b for i,b in enumerate(bodies) if i!=association.index])
                        row['reason']=reason
                        if valid:
                            x1,y1,x2,y2=map(int,region)
                            crop=frame[y1:y2,x1:x2]
                            if skin_fraction(crop)>.4:
                                row['reason']='Torso obscured or mostly skin'
                            else:
                                p_cls=p_col=None
                                if self.trainer.is_trained('uniform'):
                                    proba=self.trainer.predict_proba('uniform',crop)
                                    p_cls=proba.get('correct_uniform') if proba else None
                                if self.uniform_matcher.is_loaded():
                                    verdict,p=self.uniform_matcher.is_uniform(crop)
                                    p_col=p if verdict is not None else None
                                fused=fuse_uniform_prob(p_cls,p_col)
                                if fused is not None and np.isfinite(fused):
                                    confidence=max(fused,1-fused)
                                    row.update(uniform_available=True,torso_valid=True,torso_box=tuple(region),
                                               uniform_label=('correct_uniform' if fused>=.5 else 'wrong_uniform') if confidence>=.60 else None,
                                               uniform_confidence=float(confidence),reason='Checking uniform')
                                else:
                                    row['reason']='Uniform classifier could not assess this crop'
                    except Exception as exc:
                        row['reason']='Uniform assessment unavailable'
            # Existing earring rule: male and eligible, independent category.
            row['earring_violation']=False
            if task.earring_enabled and row.get('gender','').lower()=='male' and self.trainer.is_trained('earring'):
                try:
                    x1,y1,x2,y2=map(int,row['box'])
                    h,w=frame.shape[:2]
                    crop=frame[max(0,y1):min(h,y2),max(0,x1):min(w,x2)]
                    label,conf=self.trainer.predict('earring',crop) if crop.size else (None,0)
                    row['earring_violation']=label=='with_earring' and conf>=.65
                    row['earring_confidence']=float(conf)
                except Exception:
                    row['earring_violation']=False
        if not task.valid():
            return None
        assessments=self.state.update(task.context,rows,now=time.monotonic())
        return MonitorResult(task,assessments,time.monotonic(),
                             "No faces detected" if not rows else "")

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
            if not task.valid():
                return
            box=assessment.face_box
            projection=MotionProjection(task.frame,box)
            if latest.frame_id!=task.context.frame_id and projection.advance(latest.frame) is None:
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
                    except Exception:
                        pass
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
                            detected_at=observed,status='pending_review',valid_if=guard)
                    if written is None:
                        continue
                except Exception:
                    continue
                self.cooldowns[key]=now
                self.write_count+=1
                if guard():
                    self.notifier.notify(assessment.name, display, valid_if=guard, observed_at=task.observed_at)


class LiveWorker:
    """One stoppable worker with single-slot input/output queues."""
    def __init__(self,processor):
        self.processor=processor
        self.requests=queue.Queue(maxsize=1)
        self.results=queue.Queue(maxsize=1)
        self.stop_event=threading.Event()
        self.done=threading.Event()
        self.active_task = None
        self.thread=threading.Thread(target=self._run,daemon=True,name='live-monitor-analysis')

    def start(self):
        self.thread.start()

    def offer(self,task):
        if not self.stop_event.is_set():
            put_latest(self.requests,task)

    def stop(self):
        self.stop_event.set()
        if self.active_task is not None:
            self.active_task.cancelled.set()
        put_latest(self.requests,None)

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    task=self.requests.get(timeout=.2)
                except queue.Empty:
                    continue
                if task is None or not task.valid():
                    continue
                try:
                    self.active_task = task
                    result=self.processor.analyze(task)
                    if result is not None and task.valid() and not self.stop_event.is_set():
                        self.processor.persist(result)
                        if task.valid() and not self.stop_event.is_set():
                            put_latest(self.results,result)
                except Exception as exc:
                    print(f"[LiveMonitor] assessment unavailable: {exc}")
                    if task.valid():
                        failed = getattr(self.processor, "assessment_failed", None)
                        if failed is not None:
                            failed(task)
                        put_latest(self.results,MonitorResult(task,(),time.monotonic(),
                                                           'Assessment unavailable'))
        finally:
            self.done.set()
