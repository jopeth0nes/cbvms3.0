"""Same-session dashboard handoff scenario and sanitized lifecycle instrumentation."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch


def provenance(folder):
    import core.camera, core.preview_session, ui.dashboard, ui.monitor_workspace, ui.camera_feed, ui.camera_source_selector
    modules = (core.camera, core.preview_session, ui.dashboard, ui.monitor_workspace, ui.camera_feed, ui.camera_source_selector)
    data = dict(executable=sys.executable, prefix=sys.prefix,
        modules={m.__name__:dict(path=m.__file__, sha256=hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()) for m in modules})
    for name, args in [('revision',['rev-parse','HEAD']), ('branch',['branch','--show-current']), ('status',['status','--short'])]:
        data[name]=subprocess.run(['git',*args],capture_output=True,text=True,check=True,cwd=Path(__file__).resolve().parents[1]).stdout
    (folder/'provenance.json').write_text(json.dumps(data,indent=2))


def instrument(root, workspace, record):
    """Observe boundaries without reading frames, locals, URLs, or credentials."""
    import core.camera
    import ui.dashboard
    stack = ExitStack()
    original_lock = core.camera.CAMERA_DEVICE_LOCK
    class ObservedLock:
        def acquire(self, *args, **kwargs):
            record('device_lock_wait',thread=threading.current_thread().name)
            acquired=original_lock.acquire(*args,**kwargs)
            record('device_lock_result',thread=threading.current_thread().name, acquired=acquired)
            return acquired
        def release(self):
            record('device_lock_release',thread=threading.current_thread().name)
            original_lock.release()
        def locked(self): return original_lock.locked()
    lock=ObservedLock()
    stack.enter_context(patch.object(core.camera,'CAMERA_DEVICE_LOCK',lock))
    stack.enter_context(patch.object(ui.dashboard,'CAMERA_DEVICE_LOCK',lock))
    def source(src):
        return {k:src.get(k) for k in ('id','type','index')} if src else None
    def context():
        session=workspace.session
        return dict(generation=root._camera_generation,
            primary=source(root._camera_requested_source),
            companion=source(session.source) if session else None,
            companion_token=id(session) if session else None,
            popup=str(workspace._popup) if workspace._popup else None)
    for owner,names in ((root,('_on_switch_camera','_commit_camera_switch','_start_camera_worker','_on_camera_opened','_stop_camera')),
                        (workspace,('pop_out','close_popup','_popup_destroyed'))):
        for name in names:
            original=getattr(owner,name)
            def wrapped(*args,_name=name,_original=original,**kwargs):
                record('callback_begin',name=_name,**context())
                result=_original(*args,**kwargs)
                record('callback_end',name=_name,**context())
                return result
            stack.enter_context(patch.object(owner,name,wrapped))
    # Camera operations happen in their owner threads; record only token/thread.
    capture=ui.dashboard.CameraCapture
    for name in ('open','release'):
        original=getattr(capture,name)
        def operation(cap,*args,_name=name,_original=original,**kwargs):
            record('capture_'+_name+'_begin',capture=id(cap),thread=threading.current_thread().name)
            try: return _original(cap,*args,**kwargs)
            finally: record('capture_'+_name+'_end',capture=id(cap),thread=threading.current_thread().name)
        stack.enter_context(patch.object(capture,name,operation))
    for owner in (root,workspace):
        for name in ('after','after_cancel'):
            original=getattr(owner,name)
            def schedule(*args,_original=original,_name=name,_owner=owner,**kwargs):
                result=_original(*args,**kwargs)
                record('timer_'+_name,owner=str(_owner),token=str(result if _name=='after' else args[0]),
                       popup=str(workspace._popup) if workspace._popup else None)
                return result
            stack.enter_context(patch.object(owner,name,schedule))
    return stack


def start_handoffs(root, ws, count, stats, record, snapshot, finish):
    """Each round retains the full USB→IP→USB transition history in one root."""
    usb_name=next(n for n,s in root._cam_sources.items() if s['type']=='usb')
    ip_name=next(n for n,s in root._cam_sources.items() if s['type'] in ('ip','rj45'))
    def mode(value):
        ws.tabs.set(value); ws._layout(value)
    def choose(name):
        # Include the real selector's create/select/destroy path, not only the
        # dashboard callback. A user chooses the primary through this widget.
        root._cam_switcher.open()
        root._cam_switcher.select(name)
    def primary(name):
        return root._camera_state=='STREAMING' and root._camera_requested_source['id']==root._cam_sources[name]['id']
    def frame(): return ws._popup_feed is not None and ws._popup_feed._photo is not None
    def close():
        root.tk.call(ws._popup.protocol('WM_DELETE_WINDOW'))
    def wait_for(predicate, continuation, label):
        start=time.monotonic()
        def poll():
            if predicate():
                root.after(75,continuation)
            elif time.monotonic()-start>22:
                record('scenario_failed',label=label); stats['errors'].append(label+'Timeout'); finish()
            else: root.after(25,poll)
        poll()
    def round_(index):
        if index>=count:
            stats['handoffs_completed']=index; snapshot('handoffs_completed',index); finish(); return
        stats['handoffs_completed']=index
        record('handoff_begin',cycle=index)
        steps=[('usb_ready',lambda:choose(usb_name),lambda:primary(usb_name)),
               ('pre_focus',lambda:mode('Focus'),lambda:True)]
        for attempt in range(3):
            steps += [(f'pre_open_{attempt}',ws.pop_out,frame),(f'pre_close_{attempt}',close,lambda:True)]
        steps += [('split',lambda:mode('Split'),lambda:True),('other',lambda:mode('Other camera'),lambda:True),
                  ('focus',lambda:mode('Focus'),lambda:True)]
        # First round is exactly the reported close-before-switch history;
        # later odd rounds also leave an already-open popup through switching.
        if index%2:
            steps += [('open_before_switch',ws.pop_out,frame)]
        steps += [('ip_primary',lambda:choose(ip_name),lambda:primary(ip_name)),
                  ('ip_open',ws.pop_out,frame),('ip_close',close,lambda:True),
                  ('ip_immediate_open',ws.pop_out,lambda:True),('ip_immediate_close',close,lambda:True),
                  ('ip_split',lambda:mode('Split'),lambda:ws._last.get('tile') is not None),
                  ('ip_split_open',ws.pop_out,frame),('ip_split_close',close,lambda:True),
                  ('back_open',ws.pop_out,frame),('usb_primary',lambda:choose(usb_name),lambda:primary(usb_name)),
                  ('usb_companion',lambda:None,frame),('usb_close',close,lambda:True)]
        def next_step(i):
            if i==len(steps):
                snapshot('handoff_complete',index); root.after(75,lambda:round_(index+1)); return
            name, action, ready=steps[i]
            snapshot(name,index); record('handoff_action_begin',cycle=index,name=name)
            action(); record('handoff_action_end',cycle=index,name=name)
            wait_for(ready,lambda:next_step(i+1),name)
        next_step(0)
    round_(0)
