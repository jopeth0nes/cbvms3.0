import os
os.environ['CBVMS_ASSESSMENT_OBSERVATION_ONLY']='1'
import sys
sys.path.insert(0, '/Users/jopethones/Documents/GitHub/cbvms3.0')
import json, time, tempfile, threading
from pathlib import Path
from unittest.mock import patch
import psutil
from database.db_manager import CBVMSDatabase
from ui.dashboard import CBVMSDashboard
from core.preview_session import PreviewSession
from api.camera_store import get_saved_ip_cameras
report={'physical':True,'observation_only':True,'temporary_database':True,'phases':[],'errors':[]}
proc=psutil.Process()
sessions=[]
def make_session(source):
 s=PreviewSession(source); sessions.append(s); return s
with tempfile.TemporaryDirectory() as folder:
 db=CBVMSDatabase(Path(folder)/'verify.db'); db.initialize()
 with patch('api.camera_store.save_camera_preference'), patch('ui.monitor_workspace.PreviewSession',side_effect=make_session):
  app=CBVMSDashboard(database=db)
  app.report_callback_exception=lambda *a: report['errors'].append(str(a[0].__name__))
  ws=app._monitor_workspace
  def pump(seconds):
   app.after(int(seconds*1000), app.quit); app.mainloop()
  def wait_for(predicate, seconds=22):
   end=time.monotonic()+seconds
   while time.monotonic()<end:
    pump(.1)
    if predicate(): return True
   return False
  def measure(name,seconds=8):
   pump(2)
   proc.cpu_percent()
   begin=time.monotonic(); ids=set(); companion_ids=set()
   while time.monotonic()-begin<seconds:
    pump(.05)
    s=app._latest_monitor_sample()
    if s: ids.add(s.frame_id)
    s=ws.session.sample() if ws.session else None
    if s: companion_ids.add(s.frame_id)
   row=dict(name=name,seconds=round(time.monotonic()-begin,2),primary_samples=len(ids),companion_samples=len(companion_ids),cpu_percent=proc.cpu_percent(),rss_mb=round(proc.memory_info().rss/1e6,1),status=app._status_fps.cget('text'),primary=ws.primary_label.cget('text'),secondary=ws.secondary_label.cget('text'),size=[app.winfo_width(),app.winfo_height()])
   report['phases'].append(row); print(json.dumps(row),flush=True)
  try:
   app._refresh_camera_switcher()
   ip=next(s for s in app._cam_sources.values() if s['type'] in ('ip','rj45'))
   usb=next(s for s in app._cam_sources.values() if s['type']=='usb')
   for source in (ip,usb):
    app._on_camera_source_connected(source)
    assert wait_for(lambda:app._camera_state=='STREAMING'), 'primary failed to connect'
    ws.tabs.set('Focus'); ws._layout('Focus'); measure(source['label']+' Focus')
    ws.tabs.set('Split'); ws._layout('Split')
    assert wait_for(lambda:ws.session is not None and ws.session.sample() is not None), 'companion failed'
    measure(source['label']+' Split')
    owner=ws.session; count=len(sessions)
    ws.pop_out(); pump(1)
    assert ws.session is owner and len(sessions)==count
    assert ws.selector.get().lower() in ws._popup.title().lower()
    ws.tabs.set('Other camera'); ws._layout('Other camera'); pump(1)
    assert not ws.primary.winfo_ismapped() and ws.secondary.winfo_ismapped()
    ws._popup.event_generate('<F11>'); pump(.3)
    ws._popup.event_generate('<Escape>'); pump(.3)
    ws.close_popup(); pump(.2)
    assert ws.session is owner
    ws.retry()
    assert wait_for(lambda:ws.session is not None and ws.session is not owner and ws.session.sample() is not None)
    assert owner.done.is_set()
    ws.tabs.set('Split'); ws._layout('Split'); pump(.2)
   owner=ws.session; ws.pop_out(); app._on_nav_select('settings'); pump(.2)
   assert ws._popup is None
   assert wait_for(owner.done.is_set,5)
   app._on_nav_select('live')
   assert wait_for(lambda:app._camera_state=='STREAMING' and ws.session is not None and ws.session.sample() is not None)
   app._on_nav_select('enrollment'); pump(.2)
   app._on_nav_select('live'); pump(2)
   report['navigation']='settings/enrollment/live passed'
   report['popup']='shared session, source title, Other camera, F11/Escape events passed'
   report['retry']='explicit retry with owner released passed'
  except Exception as exc:
   report['failure']=str(exc)
  finally:
   app._logout()
   report['shutdown']={'primary':app._camera_worker_done.wait(6),'companions':all(s.done.wait(6) for s in sessions),'live':app._live_worker.done.wait(6),'tracking':app._tracking_worker.done.wait(6),'logout':app._logout_requested}
   app._monitor_models.wait(10)
   with db.connect() as c:
    tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'") if any(k in r[0] for k in ('attendance','violation','alert','unknown'))]
    report['record_counts']={t:c.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0] for t in tables}
Path('/private/tmp/monitor-workspace-report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
