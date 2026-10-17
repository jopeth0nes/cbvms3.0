"""Opt-in real-camera diagnostic with installed models and a disposable database.

Close other camera readers first. Run .venv/bin/python scripts/verify_camera_switch.py.
--manual leaves the native source menu available for physical click verification.
The automatic path invokes the real menu callback, not a physical macOS click.
No source settings, preferences, credentials, student records or pixels are saved.
JSONL timings and watchdog thread stacks are retained in --output-dir.
"""
import os, sys, time, threading, faulthandler, json, tempfile, argparse
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Native FFmpeg may print credentials. Do not forward its stderr.
os.dup2(os.open(os.devnull, os.O_WRONLY), 2)
from ui.dashboard import CBVMSDashboard
from core.camera import CameraCapture
from database.db_manager import CBVMSDatabase
from api.camera_store import get_saved_ip_cameras
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manual',action='store_true')
parser.add_argument('--output-dir',type=Path,default=None)
args=parser.parse_args()
folder=args.output_dir or Path(tempfile.mkdtemp(prefix='cbvms-switch-report-'))
folder.mkdir(parents=True,exist_ok=True)
log=open(folder/'timings.jsonl','w',buffering=1)
stacks=open(folder/'stacks.txt','w',buffering=1)
print('Diagnostic output:',folder,flush=True)
start=time.monotonic(); finished=threading.Event(); heartbeat=[start]; gaps=[]
def record(name,**data):
    item=dict(t=round(time.monotonic()-start,6),event=name,thread=threading.current_thread().name,**data)
    log.write(json.dumps(item)+'\n')
    if name not in ('heartbeat','read','render'): print(json.dumps(item),flush=True)
def watchdog():
    while not finished.wait(1):
        if time.monotonic()-heartbeat[0]>8:
            record('heartbeat_hang',gap=time.monotonic()-heartbeat[0]);faulthandler.dump_traceback(file=stacks,all_threads=True);os._exit(3)
threading.Thread(target=watchdog,daemon=True).start()
def trace(cls,name):
    original=getattr(cls,name)
    def wrapped(self,*a,**kw):
        t=time.monotonic();record(name+'_enter')
        try:return original(self,*a,**kw)
        finally:record(name+'_return',ms=round((time.monotonic()-t)*1000,3))
    return patch.object(cls,name,wrapped)
with tempfile.TemporaryDirectory(prefix='cbvms-switch-') as tmp, ExitStack() as ctx:
    db=CBVMSDatabase(Path(tmp)/'audit.db');db.initialize()
    ctx.enter_context(patch('api.camera_store.save_camera_preference',lambda pref:record('preference_saved',label=pref.get('label'))))
    ctx.enter_context(patch.object(CBVMSDashboard,'_load_camera_preference',lambda self:None))
    for name in ('_on_switch_camera','_commit_camera_switch','_on_camera_source_connected','_deferred_start_camera','_stop_camera','_start_camera_worker','_on_camera_opened','_on_nav_select','_on_close'):
        ctx.enter_context(trace(CBVMSDashboard,name))
    for name in ('open','release'):
        ctx.enter_context(trace(CameraCapture,name))
    app=CBVMSDashboard(database=db);app.title('CBVMS CAMERA DIAGNOSTIC — disposable database')
    app.report_callback_exception=lambda *a:record('callback_error',kind=a[0].__name__)
    sequences={}; rendered={}; previous=[time.monotonic(),time.process_time()];active=[None]
    def tick():
        now=time.monotonic();gaps.append(now-heartbeat[0]);heartbeat[0]=now;record('heartbeat',gap_ms=gaps[-1]*1000)
        sample=app._latest_monitor_sample()
        if sample:
            token,seq=sample.frame_id
            if token not in sequences:
                sequences[token]=[now,seq,now,seq];record('first_frame',generation=app._camera_generation,label=app._cam_source_var.get(),shape=list(sample.frame.shape))
            sequences[token][2:]=[now,seq]
            if app._monitor_last_rendered and token not in rendered:
                rendered[token]=True;record('first_render',generation=app._camera_generation)
        if now-previous[0]>=1:
            record('metrics',gap_max_ms=round(max(gaps)*1000,2),preview=app._measured_rate(app._preview_times,now),tracking=app._measured_rate(app._tracking_times,now),analysis=app._measured_rate(app._analysis_times,now),parent_cpu_percent=100*(time.process_time()-previous[1])/(now-previous[0]),nav=app._active_nav)
            previous[:]=[now,time.process_time()]
        app.after(50,tick)
    def switch(label):
        record('selection_requested',label=label)
        app._cam_switcher._dropdown_callback(label)
    def close():
        record('summary',max_heartbeat_gap_ms=max(gaps)*1000,sources=[{'seconds':v[2]-v[0],'fps':(v[3]-v[1])/max(.001,v[2]-v[0])} for v in sequences.values()])
        app._on_close();finished.set()
    heartbeat[0]=time.monotonic()
    app.after(50,tick)
    if not args.manual:
        for ms,label in [(15000,'front gate'),(27000,'MacBook Camera'),(39000,'front gate'),(51000,'MacBook Camera')]: app.after(ms,lambda label=label:switch(label))
        # Cancel before the IP first frame, then return and navigate during connect.
        app.after(56000,lambda:switch('front gate'))
        app.after(56100,lambda:switch('MacBook Camera'))
        app.after(59000,lambda:switch('front gate'))
        app.after(59100,lambda:app._on_nav_select('enrollment'))
        app.after(60000,lambda:app._on_nav_select('live'))
        app.after(68000,close)  # Close while the IP stream owns its decoder.

    else:
        app.after(120000,close)
    app.mainloop()
    done=app._camera_worker_done.wait(5);record('owner_cleanup',done=done)
    finished.set()
