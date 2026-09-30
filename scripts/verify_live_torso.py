"""Native dashboard + real camera/models; all records go to an isolated DB copy.

Measures *rendered* face/torso/verdict milestones, continuity and original-frame
snapshot ownership. Parent timeout bounds native stalls. No live DB mutations.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(seconds,capture_debug=False):
    import hashlib
    import json
    import logging
    import sqlite3
    import tempfile
    import time
    from datetime import datetime,timezone
    import cv2
    from database.db_manager import CBVMSDatabase
    from ui.dashboard import CBVMSDashboard

    logging.basicConfig(level=logging.INFO,format='%(message)s')
    with tempfile.TemporaryDirectory(prefix='cbvms_live_torso_') as folder:
        target=Path(folder)/'isolated.db'
        source=sqlite3.connect(f'{(ROOT/"data/cbvms.db").as_uri()}?mode=ro',uri=True)
        copy=sqlite3.connect(target);source.backup(copy);copy.close();source.close()
        db=CBVMSDatabase(target);db.initialize(process_deadlines=False)
        started=time.monotonic();app=CBVMSDashboard(database=db)
        app.title('CBVMS — camera verification (isolated test records)')
        app._notifier.sound_enabled=False
        first={'face':None,'torso':None,'uniform':None}
        presence=set();states=set();displayed=0;torso_frames=0;errors=[];saved=[];evidence={};saved_at=-10
        app.report_callback_exception=lambda *args: errors.append(str(args[1]))
        original_persist=app._processor.persist
        original_log=db.log_violation

        def persist(result):
            for a in result.assessments:
                if 'wrong_uniform' in a.accepted_categories:
                    x1,y1,x2,y2=a.body_box
                    crop=result.task.frame[max(0,y1):max(0,y2),max(0,x1):max(0,x2)]
                    ok,jpg=cv2.imencode('.jpg',crop,[cv2.IMWRITE_JPEG_QUALITY,85])
                    if ok:
                        stamp=datetime.fromtimestamp(result.task.observed_at,timezone.utc)
                        evidence[a.student_id,stamp]=(hashlib.sha256(jpg.tobytes()).hexdigest(),result.task.context.frame_id)
            original_persist(result)

        def log_violation(**kwargs):
            key=(kwargs['student_id'],kwargs['detected_at'])
            digest,frame_id=evidence[key]
            assert digest==hashlib.sha256(kwargs['snapshot_jpeg']).hexdigest(),'Snapshot was rebound to another frame'
            record=original_log(**kwargs)
            if record is not None:
                with db.connect() as connection:
                    row=connection.execute('SELECT student_id,status,snapshot FROM violations WHERE id=?',(record,)).fetchone()
                    assert row['student_id']==key[0] and row['status']=='pending_review'
                    assert hashlib.sha256(row['snapshot']).hexdigest()==digest
                saved.append(dict(student_id=key[0],record_id=record,original_frame=frame_id,snapshot_verified=True))
            return record

        app._processor.persist=persist;db.log_violation=log_violation
        def poll():
            nonlocal displayed,torso_frames,saved_at
            if app._closed.is_set():return
            elapsed=time.monotonic()-started
            rows=getattr(app,'_display_rows',())
            if capture_debug and rows and any(r['torso_box'] for r in rows) and elapsed-saved_at>5:
                saved_at=elapsed
                sample=app._latest_monitor_sample()
                if sample is not None:
                    # Local diagnostic only; never persisted with a student record.
                    cv2.imwrite('/tmp/cbvms-torso-live-frame.jpg',sample.frame)
                    Path('/tmp/cbvms-torso-live-regions.json').write_text(json.dumps([
                        dict(face=r['face_box'],torso=r['torso_box'],state=r['state'],reason=r['reason']) for r in rows]))
            if rows:
                displayed+=1
                if first['face'] is None:first['face']=round(elapsed,3)
            for row in rows:
                presence.add(row['presence_id']);states.add(row['state'])
                if row['torso_box']:
                    torso_frames+=1
                    if first['torso'] is None:first['torso']=round(elapsed,3)
                if row['state'] in ('Uniform compliant','Suspected uniform violation') and first['uniform'] is None:
                    first['uniform']=round(elapsed,3)
            if elapsed>=seconds:
                app._on_close()
            else:app.after(50,poll)
        app.after(50,poll);app.mainloop()
        app._tracking_worker.done.wait(5);app._live_worker.done.wait(5);app._live_worker.writes_done.wait(5)
        app._camera_worker_done.wait(5)
        print(json.dumps(dict(first_display_seconds=first,display_samples=displayed,torso_samples=torso_frames,
              presence_ids=sorted(presence),states=sorted(states),saved_in_test_database=saved,
              ui_errors=errors,live_database_writes=0)),flush=True)
        return int(bool(errors) or any(value is None for value in first.values()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=35)
    parser.add_argument('--capture-debug-frame',action='store_true',help='Save a diagnostic frame and boxes locally under /tmp')
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.child:return run(args.seconds,args.capture_debug_frame)
    try:return subprocess.run([sys.executable,__file__,'--child','--seconds',str(args.seconds)]+
                              (['--capture-debug-frame'] if args.capture_debug_frame else []),
                              timeout=args.seconds+35).returncode
    except subprocess.TimeoutExpired:
        print('Native verification process exceeded its deadline; terminated.')
        return 2


if __name__=='__main__':raise SystemExit(main())
