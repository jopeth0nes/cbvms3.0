"""Instrument main.main's real login/dashboard path with a disposable database."""
from contextlib import ExitStack
import faulthandler
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def run_normal(folder, smoke=False):
    os.environ['CBVMS_ASSESSMENT_OBSERVATION_ONLY']='1'
    import main
    from database.db_manager import CBVMSDatabase
    from ui.window_lifecycle import WorkspaceWindow
    from ui.dashboard import CBVMSDashboard
    from monitor_handoff_diagnostics import provenance, instrument
    folder.mkdir(parents=True,exist_ok=True)
    provenance(folder)
    log=(folder/'events.jsonl').open('w',buffering=1)
    stacks=(folder/'python-stacks.txt').open('w')
    faulthandler.register(signal.SIGUSR1,file=stacks,all_threads=True)
    (folder/'heartbeat').write_text(str(time.monotonic()))
    watchdog=subprocess.Popen([sys.executable,'-c',
        'import sys; from pathlib import Path; from camera_freeze_watchdog import supervise; supervise(int(sys.argv[1]),Path(sys.argv[2]),threshold=2,lifetime=650)',
        str(os.getpid()),str(folder)],cwd=Path(__file__).parent)
    gaps=[]
    def record(event,**fields):
        log.write(json.dumps(dict(event=event,at=time.monotonic(),**fields))+'\n')
    original_init=WorkspaceWindow.__init__
    original_dashboard_init=CBVMSDashboard.__init__
    with tempfile.TemporaryDirectory() as temp, ExitStack() as changes:
        database=CBVMSDatabase(Path(temp)/'normal-diagnostic.db')
        changes.enter_context(patch.object(main,'CBVMSDatabase',lambda:database))
        changes.enter_context(patch('core.cloud_sync.start_cloud_sync',lambda *_:None))
        changes.enter_context(patch('api.camera_store.save_camera_preference'))
        changes.enter_context(patch('api.camera_store.save_camera_view_preference'))
        changes.enter_context(patch('api.camera_store.get_camera_view_preference',return_value={}))
        changes.enter_context(patch('api.camera_store.get_camera_preference',lambda:dict(id='usb_0',type='usb',index=0,label='MacBook Camera')))
        # Logout normally execs a fresh process. End this bounded diagnostic instead.
        changes.enter_context(patch.object(main.os,'execv',lambda *_:record('logout_restart_suppressed')))
        def init(root,*args,**kwargs):
            original_init(root,*args,**kwargs)
            last=[time.monotonic()]
            def beat():
                now=time.monotonic();gap=now-last[0];last[0]=now;gaps.append(gap)
                (folder/'heartbeat').write_text(str(now))
                if gap>.25:record('heartbeat_gap',ms=gap*1000,window=type(root).__name__)
                root.after(50,beat)
            root.after(50,beat)
            def close():
                root.tk.call(root.protocol('WM_DELETE_WINDOW'))
            root.after(1500 if smoke else 600000,close)
        def dashboard_init(root,*args,**kwargs):
            original_dashboard_init(root,*args,**kwargs)
            changes.enter_context(instrument(root,root._monitor_workspace,record))
            faulthandler.register(signal.SIGUSR1,file=stacks,all_threads=True)
            record('dashboard_ready')
        changes.enter_context(patch.object(WorkspaceWindow,'__init__',init))
        changes.enter_context(patch.object(CBVMSDashboard,'__init__',dashboard_init))
        record('normal_main_started',disposable_database=True,cloud_sync=False,observation_only=True)
        try:
            main.main()
        finally:
            (folder/'finished').touch()
            watchdog.wait(timeout=5)
            faulthandler.unregister(signal.SIGUSR1)
            (folder/'summary.json').write_text(json.dumps(dict(mode='normal-login-dashboard',max_gap_ms=max(gaps,default=0)*1000,heartbeats=len(gaps)),indent=2))
            stacks.close();log.close()
