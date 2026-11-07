"""Inspect actual model inventory in isolated read-only UI configuration."""
from pathlib import Path
import sys,os,tempfile,json,time
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from PIL import ImageGrab
from core.uniform_evaluation import EvaluationService
from core.uniform_model_registry import UniformModelRegistry
from core.uniform_models_service import UniformModelsService
from ui.components import apply_cbvms_theme
from ui.scrolling import ScrollableFrame
from ui.window_lifecycle import WorkspaceWindow
from ui.uniform_models_panel import UniformModelsWindow
print(json.dumps({'pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat(),'command':sys.executable+' output/uniform-stage3/native_visual_check.py'}),flush=True)
apply_cbvms_theme()
with tempfile.TemporaryDirectory() as temp:
    root=WorkspaceWindow();root.geometry('200x100+0+0')
    evaluator=EvaluationService(Path(temp)/'evaluations')
    registry=UniformModelRegistry(Path(temp)/'registry')
    service=UniformModelsService(registry=registry,evaluator=evaluator)
    window=UniformModelsWindow(root,service);window.geometry('1000x830+65+55')
    errors=[];root.report_callback_exception=lambda *a:errors.append(str(a[1]))
    began=time.monotonic()
    def capture(name):
        box=(window.winfo_rootx(),window.winfo_rooty(),window.winfo_rootx()+window.winfo_width(),window.winfo_rooty()+window.winfo_height())
        ImageGrab.grab(bbox=box).save(ROOT/'output/uniform-stage3'/name)
    def bottom():
        capture('models-bottom.png')
        assert not (ROOT/'data/uniform_models/active.json').exists()
        assert not errors,errors
        print(json.dumps({'status':'passed','active':window.snapshot['active']['id'],'candidates':len(window.snapshot['candidates']),
            'authorization':'no session in isolated view','camera_opened':False,'errors':errors}),flush=True)
        window.destroy();root.destroy()
    def ready():
        if window.task.busy:
            if time.monotonic()-began>15:raise RuntimeError('Model inventory timed out')
            root.after(100,ready);return
        window.lift();root.after(350,top)
    def top():
        capture('models-top.png')
        body=next(w for w in window.winfo_children() if isinstance(w,ScrollableFrame)) if any(isinstance(w,ScrollableFrame) for w in window.winfo_children()) else None
        # CTkScrollableFrame's visible parent wraps its canvas; locate the owned frame.
        if body is None:
            def walk(w):
                for c in w.winfo_children():
                    yield c;yield from walk(c)
            body=next(w for w in walk(window) if isinstance(w,ScrollableFrame))
        body._parent_canvas.yview_moveto(1)
        root.after(350,bottom)
    root.after(100,ready)
    root.mainloop()
