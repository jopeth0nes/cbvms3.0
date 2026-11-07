"""Disposable normal-dashboard UI verification; never writes real review data."""
import os, sys, json, time, tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
os.environ['CBVMS_ASSESSMENT_OBSERVATION_ONLY']='1'
os.environ.pop('CBVMS_UNIFORM_OBSERVATION_CONFIG',None)
from PIL import Image, ImageDraw
from core.uniform_dataset import UniformDataset
from core.uniform_candidate import CandidateService
from database.db_manager import CBVMSDatabase
from ui import uniform_workspace
from ui.dashboard import CBVMSDashboard

receipt=ROOT/'output/wrong-uniform/native-session.json'
if '--reopen' in sys.argv:
    temp=Path(json.loads(receipt.read_text())['temp'])
else:
    temp=Path(tempfile.mkdtemp(prefix='cbvms-wrong-uniform-'))
    folder=temp/'photos';folder.mkdir()
    for i,color in enumerate(['#c34268','#376a97','#986731']):
        im=Image.new('RGB',(480,640),'#bbbfc5');d=ImageDraw.Draw(im)
        d.ellipse((185,35,295,145),fill='#ba8765')
        d.polygon([(180,150),(300,150),(390,250),(335,300),(310,260),(320,550),(160,550),(170,260),(145,300),(90,250)],fill=color,outline='black',width=3)
        for y in range(180,540,12): d.line((180,y,300,y),fill='#eceaea',width=2)
        d.rectangle((210,285,265,345),fill='white',outline='black',width=3)
        d.text((20,600),f'SYNTHETIC UI FIXTURE {i+1} - NOT REAL TRAINING DATA',fill='black')
        im.save(folder/f'fixture-{i+1}.png')
    receipt.write_text(json.dumps({'temp':str(temp),'pid':os.getpid(),'fixture_only':True},indent=2))
dataset=UniformDataset(temp/'review')
service=CandidateService(dataset,candidates_root=temp/'candidates')
original=uniform_workspace.UniformWorkspace
class IsolatedWorkspace(original):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,dataset=dataset,candidate_service=service,**kwargs)
db=CBVMSDatabase(temp/'desktop.sqlite');db.initialize()
# Override only storage/device preferences: no saved camera configuration writes.
with patch.object(uniform_workspace,'UniformWorkspace',IsolatedWorkspace), \
     patch('api.camera_store.get_camera_preference',return_value={'id':'usb_0','type':'usb','index':0,'label':'MacBook Camera'}), \
     patch('api.camera_store.get_saved_ip_cameras',return_value=[]), \
     patch('api.camera_store.save_camera_preference'):
    app=CBVMSDashboard(username='disposable-ui-review',database=db)
    app.title('CBVMS — Disposable Wrong Uniform Verification')
    def report():
        snapshot={k:{'state':v.state,'error':getattr(v,'error',None)} for k,v in app._readiness.snapshot().items()}
        (ROOT/'output/wrong-uniform/native-state.json').write_text(json.dumps({'temp':str(temp),'pid':os.getpid(),'window':[app.winfo_width(),app.winfo_height()], 'readiness':snapshot,'camera':app._camera_state,'observation_only':app._observation_only},indent=2))
        app.after(2000,report)
    app.after(2000,report)
    app.mainloop()
print('DISPOSABLE_NATIVE_CLOSED',flush=True)
