"""macOS native screenshots using a disposable fixture database, no camera/models.

Run: .venv/bin/python scripts/capture_suspension_workspace.py
Only the application window's region is captured, not the whole desktop.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_suspension_analytics import seed,NOW
from core.model_readiness import ModelReadiness,ComponentStatus
from database.db_manager import CBVMSDatabase
from ui.dashboard import CBVMSDashboard

OUT=ROOT/'docs'/'suspensions'
OUT.mkdir(parents=True,exist_ok=True)

def pump(app,seconds=.3):
    app.after(int(seconds*1000),app.quit); app.mainloop()

def wait(app,condition):
    deadline=time.monotonic()+8
    while not condition() and time.monotonic()<deadline: pump(app,.05)
    if not condition(): raise RuntimeError('Preview did not load')
    pump(app)

def capture(app,name):
    app.lift(); app.focus_force(); pump(app,.5)
    x,y=app.winfo_rootx(),app.winfo_rooty()
    w,h=app.winfo_width(),app.winfo_height()
    subprocess.run(['screencapture','-x','-R',f'{x},{y},{w},{h}',str(OUT/f'{name}.png')],check=True)

with tempfile.TemporaryDirectory() as folder:
    db=CBVMSDatabase(Path(folder)/'preview.db'); seed(db)
    recognizer=MagicMock(); recognizer.readiness=ModelReadiness()
    recognizer.readiness._status={n:ComponentStatus('ready') for n in ('face','recognition')}
    recognizer.detect_faces.return_value=[]; recognizer.recognize_faces.return_value=[]; recognizer.last_error=None
    with patch.object(CBVMSDashboard,'_deferred_start_camera'),patch.object(CBVMSDashboard,'_load_camera_preference'), \
         patch.object(CBVMSDashboard,'_prewarm_models',lambda p:None),patch.object(CBVMSDashboard,'_refresh_stats'), \
         patch.object(db,'process_expired_deadlines'),patch('core.suspension_analytics.utc_now',return_value=NOW):
        app=CBVMSDashboard(database=db,recognizer=recognizer,person_detector=MagicMock())
        try:
            app.geometry('1440x900+0+35'); pump(app)
            app._on_nav_select('suspensions')
            wait(app,lambda:app._suspensions_panel is not None and app._suspensions_panel.report is not None)
            panel=app._suspensions_panel
            panel.period_var.set('All Data'); panel.variables['interval'].set('Day'); panel._changed()
            wait(app,lambda:panel.report is not None and panel.report['filters'].period=='all')
            capture(app,'overview')
            panel.overview._parent_canvas.yview_moveto(panel.chart_frame.winfo_y()/max(1,panel.overview.winfo_height())); pump(app)
            capture(app,'overview-trends')
            panel._navigate('Analytics'); pump(app)
            # Scroll to course comparison using its actual widget location.
            total=panel.overview.winfo_height()
            panel.overview._parent_canvas.yview_moveto(panel.analytics.winfo_y()/max(total,1)); pump(app)
            capture(app,'analytics')
            panel._drill_open('suspensions'); pump(app)
            capture(app,'matching-records')
            panel.select_student('S1'); wait(app,lambda:panel.records._ready)
            capture(app,'student-records')
            panel.records._tabs.set('Suspension History'); pump(app)
            panel.records_canvas.yview_moveto(1); pump(app)
            capture(app,'student-history')
            print(OUT)
        finally:
            app._on_close()
