"""Bounded native pop-out diagnostic; synthetic default; optional physical observation-only temporary database."""
import argparse
import faulthandler
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(folder, physical=None, cycles=0, view="Focus", manual=False, handoffs=0, synthetic_dashboard=False):
    os.environ["CBVMS_ASSESSMENT_OBSERVATION_ONLY"]="1"
    import customtkinter as ctk
    import numpy as np
    from core.camera import CameraSample
    from core.preview_session import PreviewSession
    from ui.camera_feed import CameraFeed
    from ui.monitor_workspace import MonitorWorkspace
    from ui.window_lifecycle import WorkspaceWindow
    folder.mkdir(parents=True, exist_ok=True)
    stacks = (folder/'python-stacks.txt').open('w')
    faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True)
    (folder/'heartbeat').write_text(str(time.monotonic()))
    watchdog = subprocess.Popen([sys.executable, '-c', 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from camera_freeze_watchdog import supervise; supervise(int(sys.argv[2]), Path(sys.argv[3]), threshold=2, lifetime=650)', str(ROOT/'scripts'), str(os.getpid()), str(folder)])
    events = (folder/'events.jsonl').open('w', buffering=1)
    def record(event, **fields):
        events.write(json.dumps(dict(event=event, at=time.monotonic(), **fields))+'\n')
    frame = np.zeros((1080,1920,3), np.uint8)
    class Capture:
        def __init__(self, **kw):
            self.stop=kw['cancel_event']; self.is_open=False; self.latest=None; self.seq=0
            self.last_error=None; self.camera_index=kw.get('camera_index',0)
        def open(self):
            self.is_open=True
            self.latest=CameraSample(frame, (id(self),self.seq),time.monotonic())
            return True
        def read(self):
            self.stop.wait(1/30); self.seq+=1
            self.latest=CameraSample(frame, (id(self),self.seq),time.monotonic())
            return frame
        def get_latest_sample(self): return self.latest
        def release(self): self.is_open=False
    sessions=[]
    def factory(source):
        session=PreviewSession(source) if physical or synthetic_dashboard else PreviewSession(source,capture_factory=Capture)
        sessions.append(session); return session
    phase = ['startup']
    stats={'cycles_completed':0, 'renders':0,'render_ms':0.,'max_render_ms':0.,'errors':[], 'phases':{}}
    render=CameraFeed.render
    def timed_render(self, frame):
        start=time.monotonic()
        record('render_begin', phase=phase[0], widget=str(self))
        result=render(self,frame)
        record('render_end', phase=phase[0], widget=str(self), ok=result)
        elapsed=(time.monotonic()-start)*1000
        stats['renders']+=1; stats['render_ms']+=elapsed
        bucket=stats['phases'].setdefault(phase[0], {'renders':0,'render_ms':0.,'configure_events':0})
        bucket['renders']+=1; bucket['render_ms']+=elapsed
        stats['max_render_ms']=max(stats['max_render_ms'],elapsed)
        return result
    temporary = None
    from contextlib import ExitStack
    capture_patches = ExitStack()
    integrated = bool(physical or synthetic_dashboard)
    preference_patch = patch('api.camera_store.save_camera_preference')
    preference_patch.start()
    if integrated:
        import tempfile
        from database.db_manager import CBVMSDatabase
        from ui.dashboard import CBVMSDashboard
        if synthetic_dashboard:
            capture_patches.enter_context(patch('ui.dashboard.CameraCapture',Capture))
            capture_patches.enter_context(patch('core.camera.CameraCapture',Capture))
            capture_patches.enter_context(patch('api.camera_store.resolve_camera_source',lambda src,**kw:dict(src,url='synthetic')))
        temporary = tempfile.TemporaryDirectory()
        db = CBVMSDatabase(Path(temporary.name)/'popup.db'); db.initialize()
        root = CBVMSDashboard(database=db)
        dashboard = root
        root._refresh_camera_switcher()
        usb = next(s for s in root._cam_sources.values() if s['type']=='usb')
        ip = next(s for s in root._cam_sources.values() if s['type'] in ('ip','rj45'))
        first, other = (usb,ip) if physical=='usb' or handoffs or synthetic_dashboard else (ip,usb)
        root._on_camera_source_connected(first)
    else:
        root=WorkspaceWindow(); root.geometry('1100x700')
        root.grid_rowconfigure(0,weight=1); root.grid_columnconfigure(0,weight=1)
        usb=dict(type='usb',index=0,label='Primary'); other=dict(type='usb',index=1,label='Companion')
        dashboard=SimpleNamespace(_active_nav='live',_closed=threading.Event(),_camera_requested_source=usb,
            _cam_sources={'Primary':usb,'Companion':other},_camera_state='STREAMING',_observation_only=True)
    root.report_callback_exception=lambda typ,*_: stats['errors'].append(typ.__name__)
    # Some model libraries install signal handlers during initialization.
    faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True)
    gaps=[]; last=time.monotonic()
    def beat():
        nonlocal last
        now=time.monotonic(); gaps.append((now-last)*1000); last=now
        (folder/'heartbeat').write_text(str(now))
        if gaps[-1]>250: record('heartbeat_gap',ms=gaps[-1])
        root.after(50,beat)
    with patch('ui.monitor_workspace.PreviewSession',side_effect=factory), patch.object(CameraFeed,'render',timed_render):
        ws=root._monitor_workspace if integrated else MonitorWorkspace(root,dashboard)
        if not integrated: ws.grid(sticky='nsew')
        def mode(value): ws.tabs.set(value); ws._layout(value)
        def configured(_event):
            bucket=stats['phases'].setdefault(phase[0], {'renders':0,'render_ms':0.,'configure_events':0})
            bucket['configure_events']+=1
        ws.bind('<Configure>', configured, add='+')
        def finish():
            record('finishing')
            if integrated: root._logout()
            else: ws.close(); root.destroy()
        actions=[('focus_pop',ws.pop_out),('resize',lambda:ws._popup.geometry('1000x650')),
          ('minimize',lambda:ws._popup.iconify()),('restore',lambda:ws._popup.deiconify()),
          ('fullscreen',lambda:ws._popup.attributes('-fullscreen',True)),
          ('exit_fullscreen',lambda:ws._popup.attributes('-fullscreen',False)),
          ('close',ws.close_popup),('split',lambda:mode('Split')),('split_pop',ws.pop_out),
          ('close_again',ws.close_popup),('reopen',ws.pop_out),('source_switch',lambda:root._on_camera_source_connected(other) if physical else setattr(dashboard,'_camera_requested_source',other)),
          ('navigate',lambda:root._on_nav_select('settings') if physical else setattr(dashboard,'_active_nav','settings')),
          ('return',lambda:root._on_nav_select('live') if physical else setattr(dashboard,'_active_nav','live')),('final_pop',ws.pop_out),('finish',finish)]
        def action(index):
            name, fn=actions[index]; phase[0]=name; record('begin',action=name); fn(); record('end',action=name)
        import psutil
        process = psutil.Process()
        def snapshot(stage, cycle):
            def popups(widget):
                return sum(isinstance(child, ctk.CTkToplevel) + popups(child)
                           for child in widget.winfo_children())
            record('lifecycle', stage=stage, cycle=cycle,
                windows=popups(root), timers=len(root.tk.splitlist(root.tk.call('after','info'))),
                images=len(root.tk.splitlist(root.tk.call('image','names'))),
                sessions=len(sessions), active_sessions=sum(not s.done.is_set() for s in sessions),
                workers=sum(t.name=='companion-preview' for t in threading.enumerate()),
                rss_mb=round(process.memory_info().rss/1e6,2),
                camera_state=dashboard._camera_state)
        def closed_then_reopen(cycle):
            snapshot('rendered',cycle)
            record('close_begin',cycle=cycle)
            # Invoke the actual WM_DELETE_WINDOW command registered by the UI.
            root.tk.call(ws._popup.protocol('WM_DELETE_WINDOW'))
            record('close_end',cycle=cycle)
            # Close before any callback can deliver the first frame, then rapid
            # repeated open requests on the same new window.
            ws.pop_out(); ws.close_popup()
            ws.pop_out(); ws.pop_out(); ws.pop_out()
            snapshot('immediate_reopen',cycle)
            start=time.monotonic()
            def fresh():
                if ws._popup_feed is not None and ws._popup_feed._photo is not None:
                    snapshot('reopened_rendered',cycle)
                    ws.close_popup()
                    # Let the normal poll request capture cancellation in Focus,
                    # then reopen while the old owner may still be stopping.
                    root.after(60,lambda: next_cycle(cycle+1))
                elif time.monotonic()-start>12:
                    stats['errors'].append('ReopenedFrameTimeout'); finish()
                else: root.after(25,fresh)
            root.after(25,fresh)
        def verify_return():
            # A visible embedded consumer must survive popup close/minimize.
            phase[0]='post_cycles'
            owner=ws.session
            ws.pop_out()
            root.after(200,lambda:ws._popup.iconify())
            root.after(600,lambda:ws.pop_out())
            def close_and_navigate():
                ws.close_popup()
                if ws.session is not owner or owner.stop_event.is_set():
                    stats['errors'].append('SplitOwnerChanged')
                snapshot('split_owner_retained',cycles)
                root._on_nav_select('settings')
                root.after(1000,return_live)
            def return_live():
                root._on_nav_select('live')
                started=time.monotonic()
                def resumed():
                    if root._camera_state=='STREAMING' and ws._last.get('tile') is not None:
                        snapshot('navigation_frames_resumed',cycles)
                        stats['navigation_frames_resumed']=True
                        finish()
                    elif time.monotonic()-started>15:
                        stats['errors'].append('NavigationFrameTimeout'); finish()
                    else: root.after(50,resumed)
                root.after(50,resumed)
            root.after(1200,close_and_navigate)
        def next_cycle(cycle):
            stats['cycles_completed']=cycle
            if cycle>=cycles:
                snapshot('completed',cycle)
                if physical and view == 'Split': verify_return()
                else: finish()
                return
            phase[0]=f'cycle-{cycle}'
            snapshot('before_open',cycle)
            record('open_begin',cycle=cycle); ws.pop_out(); record('open_end',cycle=cycle)
            start=time.monotonic()
            def rendered():
                if ws._popup_feed is not None and ws._popup_feed._photo is not None:
                    root.after(100,lambda:closed_then_reopen(cycle))
                elif time.monotonic()-start>12:
                    stats['errors'].append('FirstFrameTimeout'); finish()
                else: root.after(25,rendered)
            root.after(25,rendered)
        from monitor_handoff_diagnostics import provenance, instrument, start_handoffs
        provenance(folder)
        tracing = instrument(root,ws,record) if integrated else ExitStack()
        if handoffs:
            root.after(10000 if physical else 1000,lambda:start_handoffs(root,ws,handoffs,stats,record,snapshot,finish))
        elif manual:
            mode(view)
            root.after(600000,finish)
            def manual_snapshot():
                snapshot('manual',-1); root.after(1000,manual_snapshot)
            root.after(1000,manual_snapshot)
        elif cycles:
            mode(view)
            root.after(10000 if physical else 1000,lambda:next_cycle(0))
        else:
            for index in range(len(actions)): root.after((10000 if physical else 1000)+index*1200,lambda i=index:action(i))
        root.after(50,beat); root.mainloop()
    if integrated:
        stats['primary_stopped']=root._camera_worker_done.wait(6)
        stats['analysis_stopped']=root._live_worker.done.wait(6)
        stats['tracking_stopped']=root._tracking_worker.done.wait(6)
        root._monitor_models.wait(10)
    tracing.close()
    capture_patches.close()
    preference_patch.stop()
    if temporary: temporary.cleanup()
    stats.update(max_gap_ms=max(gaps,default=0),p95_gap_ms=sorted(gaps)[int(len(gaps)*.95)] if gaps else 0,
                 heartbeats=len(gaps),sessions=len(sessions),clean_stop=all(s.done.wait(3) for s in sessions))
    (folder/'summary.json').write_text(json.dumps(stats,indent=2))
    (folder/'finished').touch(); watchdog.wait(timeout=3)
    faulthandler.unregister(signal.SIGUSR1); stacks.close(); events.close()
    if stats['errors'] or not stats['clean_stop'] or any(stats.get(k) is False for k in ('primary_stopped','analysis_stopped','tracking_stopped')):
        raise SystemExit(1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--physical',choices=('usb','ip'))
    parser.add_argument('--cycles',type=int,default=0)
    parser.add_argument('--view',choices=('Focus','Split'),default='Focus')
    parser.add_argument('--manual',action='store_true')
    parser.add_argument('--handoffs',type=int,default=0)
    parser.add_argument('--synthetic-dashboard',action='store_true')
    parser.add_argument('--normal-app',action='store_true')
    parser.add_argument('--login-smoke',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--child',action='store_true')
    args=parser.parse_args(); folder=args.output_dir.resolve()
    if args.child and args.normal_app:
        from monitor_normal_diagnostics import run_normal
        run_normal(folder,args.login_smoke); return
    if args.child: run(folder,args.physical,args.cycles,args.view,args.manual,args.handoffs,args.synthetic_dashboard); return
    folder.mkdir(parents=True,exist_ok=False)
    with (folder/'console.txt').open('w') as log:
        child=subprocess.Popen([sys.executable,__file__,'--child','--output-dir',str(folder),'--handoffs',str(args.handoffs),'--cycles',str(args.cycles),'--view',args.view]+(['--normal-app'] if args.normal_app else [])+(['--login-smoke'] if args.login_smoke else [])+(['--manual'] if args.manual else [])+(['--synthetic-dashboard'] if args.synthetic_dashboard else [])+(['--physical',args.physical] if args.physical else []),stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+(630 if args.manual or args.normal_app else 600 if args.handoffs else 180 if args.cycles else 65)
            while child.poll() is None:
                if time.monotonic()>deadline:
                    raise subprocess.TimeoutExpired(child.args,180)
                try: age=time.monotonic()-float((folder/'heartbeat').read_text())
                except (OSError,ValueError): age=0
                # The external sampler gets time to finish before termination.
                if age>8:
                    raise subprocess.TimeoutExpired(child.args,8)
                time.sleep(.25)
            code=child.returncode
        except subprocess.TimeoutExpired:
            # Independent watchdog has already had time to collect stacks and samples.
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
            (folder/'finished').touch()
            raise SystemExit('Timed out; preserved watchdog evidence before stopping task-owned process')
    print((folder/'summary.json').read_text() if (folder/'summary.json').exists() else 'No summary')
    raise SystemExit(code)

if __name__=='__main__': main()
