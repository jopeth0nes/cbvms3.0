"""Headless timing of the real registration loop with a synthetic 30 FPS source.

Includes OpenCV/PIL rendering and real worker scheduling; substitutes only camera,
model inference (200 ms), Tk image upload, and widgets. NOT a physical-camera or
Tk compositor benchmark. Optional --before-source accepts an earlier register.py.
"""
import argparse
from collections import deque
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from auth import register
from core.camera import CameraSample
from core.registration_camera import RegistrationCamera
from tests.test_registration_preview import fake_window


class SyntheticCamera:
    def __init__(self, **kwargs):
        self.frame = np.random.default_rng(4).integers(0,255,(720,1280,3),dtype=np.uint8)
        self.seq=0
        self.next_frame=time.monotonic()
        self.frame_times=deque(maxlen=300)
    def open(self): return True
    def get_settings(self): return dict(width=1280,height=720,fps=30,backend='synthetic')
    def read(self):
        self.next_frame=max(self.next_frame+1/30,time.monotonic())
        time.sleep(max(0,self.next_frame-time.monotonic()))
        self.seq+=1
        at=time.monotonic()
        self.frame_times.append(at)
        self.sample=CameraSample(self.frame,('synthetic',self.seq),at)
        return self.frame
    def get_latest_sample(self): return self.sample
    def release(self): pass


def run(seconds, processing, before=None):
    source=RegistrationCamera(camera_factory=SyntheticCamera)
    win=fake_window(source)
    module=register
    if before:
        spec=importlib.util.spec_from_file_location('registration_baseline',before)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        win._live_tick=module.StudentRegistrationWindow._live_tick.__get__(win)
        camera=SyntheticCamera()
        win._get_frame=camera.read
        win._frame_sequence=0
    calls=[];active=[0];peak=[0]
    def detect(frame):
        calls.append(time.monotonic());active[0]+=1;peak[0]=max(peak[0],active[0])
        time.sleep(.2)
        active[0]-=1
        emb=np.zeros(512,np.float32);emb[0]=1
        return [([480,180,780,525],emb,.9,None)]
    win._recognizer=SimpleNamespace(enrollment_faces=detect) if processing else None
    jobs=[];max_jobs=[0];rendered=[];tick_costs=[]
    def after(delay,callback):
        jobs.append((time.monotonic()+delay/1000,callback))
        max_jobs[0]=max(max_jobs[0],len(jobs))
        return len(jobs)
    win.after=after
    renderer=module.StudentRegistrationWindow._render_frame.__get__(win)
    def render(*args,**kwargs):
        renderer(*args,**kwargs)
        if not kwargs.get('frozen'): rendered.append(time.monotonic())
    win._render_frame=render
    started=time.monotonic()
    if not before:source.start()
    with patch.object(module.ImageTk,'PhotoImage',side_effect=lambda **kw:object()):
        win._live_tick()
        while time.monotonic()-started<seconds:
            if jobs and jobs[0][0]<=time.monotonic():
                _,callback=jobs.pop(0)
                t=time.monotonic();callback();tick_costs.append(time.monotonic()-t)
            else:time.sleep(.001)
    elapsed=time.monotonic()-started
    win._alive=False;win._stop_event.set();source.stop()
    if not before:source.done.wait(2)
    times=list(camera.frame_times if before else source.frame_times)
    gaps=np.diff(rendered)*1000
    return dict(version='before' if before else 'after',mode='validation' if processing else 'preview_only',
                elapsed_seconds=round(elapsed,2),source_fps=round(win._metrics.rate(times),2),
                rendered_frames=len(rendered),preview_fps=round(len(rendered)/elapsed,2),
                preview_gap_p95_ms=round(float(np.percentile(gaps,95)),2) if len(gaps) else None,
                ui_tick_p95_ms=round(float(np.percentile(tick_costs,95))*1000,2),
                inference_calls=len(calls),max_inference_inflight=peak[0],max_queued_ui_ticks=max_jobs[0])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=5)
    parser.add_argument('--before-source',type=Path)
    args=parser.parse_args()
    for enabled in (False,True):
        print(json.dumps(run(args.seconds,enabled,args.before_source)),flush=True)
