#!/usr/bin/env python3
"""Bounded synthetic passage plumbing with real candidate inference.

Scripted face/body localization is a fixture, NOT a physical-camera measurement.
No garment labels or real-world accuracy are asserted. No database is opened.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'cbvms-uniform-smoke-mpl'))
os.environ.setdefault('YOLO_CONFIG_DIR',str(Path(tempfile.gettempdir())/'cbvms-uniform-smoke-yolo'))
import numpy as np
import cv2
import torch
from core.live_pipeline import FaceTrackingProcessor,LiveProcessor,LiveWorker,MonitorTask
from core.live_state import FrameContext
from core.assessment_candidates import ClearFrameRouter
from core.uniform_observation import ObservationConfig,ObservationUniformSession
from core.uniform_model_registry import candidate_descriptor
from core.uniform_latency import UniformPassageMetrics


class FixtureRecognizer:
    last_error=None
    def detect_faces(self,frame):return [{'box':(220,20,280,100),'score':.99}]
    def recognize_faces(self,frame,detections):
        return [dict(d,matched=False,identity_uncertain=True) for d in detections]


class FixtureBody:
    last_error=None
    visible=True
    def detect_persons(self,frame):return [(150,0,370,450)] if self.visible else []
    def chest_region(self,frame,face,body):return (170,120,350,320),'scripted-fixture'


class ReadOnlyFixtureDatabase:
    def attendance_term_snapshot(self,**kw):return {}


def run(candidate,backend,passages):
    torch.set_num_threads(2)
    cv2.setNumThreads(2)
    descriptor=candidate_descriptor(candidate)
    config=ObservationConfig(str(Path(candidate).resolve()),descriptor['id'],.8,backend=backend,
        assessment_zone=(0.,0.,1.,1.)) # explicit smoke confidence, not an operating recommendation
    session=ObservationUniformSession(config)
    session.prepare()
    # First import of Ultralytics can reset OpenCV to serial. Apply the requested
    # settings after loading as well, so CPU-first and MPS-second are comparable.
    torch.set_num_threads(2)
    cv2.setNumThreads(2)
    gray=np.random.default_rng(73).integers(20,240,(480,640,1),dtype=np.uint8)
    clear=np.repeat(gray,3,axis=2)
    blurred=np.full_like(clear,100)
    for _ in range(4):session.predictor.timed_probabilities(clear[120:320,170:350])
    recognizer=FixtureRecognizer();body=FixtureBody()
    processor=LiveProcessor(ReadOnlyFixtureDatabase(),recognizer,body,
        SimpleNamespace(is_trained=lambda _:False),None,SimpleNamespace(),
        observation_only=True,uniform_observation=session)
    tracker=FaceTrackingProcessor(recognizer,body)
    worker=LiveWorker(processor,observation_only=True,persistence_worker=True)
    router=ClearFrameRouter(fresh_selection=True,ttl=config.frame_ttl,refresh_interval=0)
    metrics=UniformPassageMetrics();origin=time.monotonic();sequence=0;failures=[]
    worker.start()
    try:
        for passage in range(passages):
            cancelled=threading.Event();alias='synthetic-'+str(passage)
            entry=None;usable=None
            for index in range(3):
                # One fifth of the simulated passages has no usable torso at all.
                pixels=blurred if passage%5==4 else clear
                body.visible=index>0
                sequence+=1;captured=time.monotonic()
                if entry is None:entry=(captured-origin)*1000
                task=MonitorTask(FrameContext(passage+1,('fixture',sequence),captured),pixels,
                    time.time(),cancelled,earring_enabled=False,source_id='synthetic-camera',
                    camera_generation=1,observation_only=True)
                tracked=tracker.analyze(task)
                tracked=replace(tracked,task=session.observe(tracked.task,tracked.observations))
                if usable is None and any(t.usable for t in tracked.task.uniform_tickets):
                    usable=(captured-origin)*1000
                annotation={'passage_alias':alias,'zone_entry_ms':entry}
                if usable is not None:annotation['first_usable_torso_ms']=usable
                metrics.observe_labels([annotation])
                router.observe(tracked,select=True)
                candidate_task=router.next()
                if candidate_task is None:
                    failures.append('no_dispatch');continue
                from copy import copy
                offered=copy(candidate_task.task)
                object.__setattr__(offered,'candidate_on_complete',router.complete)
                if not worker.offer(offered):failures.append('offer_rejected');continue
                try:result=worker.results.get(timeout=2)
                except queue.Empty:failures.append('no_completed_result');continue
                if result.assessments:
                    metrics.record(alias,result.assessments[0].garment,(result.finished_at-origin)*1000)
                else:failures.append('empty_assessment')
                time.sleep(.035)
            cancelled.set();router.reset();session.invalidate()
    finally:
        worker.stop();worker.done.wait(5)
        if not worker.done.is_set():raise RuntimeError('Observation smoke worker did not stop')
    assert processor.write_count==0 and not worker.persistence_worker and worker.attendance_writer is None
    return {'backend':session.predictor.backend_status(),'candidate_id':descriptor['id'],
        'synthetic_candidate':descriptor['synthetic'],'preprocessing_version':descriptor['preprocessing_version'],
        'config_version':config.version,'fixture_pixel_sha256':hashlib.sha256(clear.tobytes()).hexdigest(),
        'metrics':metrics.report(),'failures':failures,'database_opened':False,'production_writes':0,
        'worker_stopped':worker.done.is_set(),'queue_capacity':worker.requests.maxsize,
        'conditions':{'frames_per_simulated_passage':3,'simulated_passages':passages,
            'blur_only_passages':passages//5,'warmup_calls':4,'fixture_confidence':.8,
            'frame_size':[640,480],'candidate_input_size':224,'wait_between_frames_ms':35,
            'torch_cpu_threads':torch.get_num_threads(),'opencv_threads':cv2.getNumThreads(),
            'opencv_threads_requested':2,
            'pytorch_operator_fallback_env':os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK','unset'),
            'physical_camera':False,'face_and_body_inference':'scripted fixture; real association/tracking code',
            'classification_accuracy':None,'policy_accuracy':None,
            'contention':'sequential backends; no live app/camera/training job; ordinary OS activity uncontrolled'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--backend',choices=('cpu','mps','both'),default='both')
    parser.add_argument('--passages',type=int,default=10)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if not 1<=args.passages<=50:parser.error('Use 1 to 50 simulated passages')
    if args.output.suffix!='.json' or args.output.resolve().is_relative_to(args.candidate.resolve()):
        parser.error('Use a separate JSON artifact outside the candidate bundle')
    reports=[]
    for backend in (('cpu','mps') if args.backend=='both' else (args.backend,)):
        reports.append(run(args.candidate,backend,args.passages))
    report={'kind':'synthetic_passage_integration_smoke','results':reports,
        'real_passage_count':0,'physical_camera_measurement':False,'accuracy_evaluated':False,
        'limitations':['Synthetic pixels and scripted localization; these timings exclude real face/body inference.',
            'No evidence that the 500 ms target is met on real passages. Input size remains 224.',
            'Recognition predictions on these rasters are not clothing labels or accuracy evidence.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print('Wrote',args.output)
    if any(r['failures'] for r in reports):raise SystemExit(1)


if __name__=='__main__':main()
