"""Two-process native workflow using real controls and disposable synthetic photos.
Run `... review ROOT` then `... reopen ROOT`. No trainer worker is launched.
"""
import sys,json,time,gc
from pathlib import Path
from unittest.mock import patch
import customtkinter as ctk
import numpy as np
from PIL import Image
REPO=Path(__file__).resolve().parents[2];sys.path.insert(0,str(REPO))
from core.uniform_dataset import UniformDataset
from core.uniform_candidate import CandidateService
from core.trainer import ViolationTrainer
from ui.training_panel import TrainingPanel
from ui.uniform_workspace import UniformWorkspace
from ui.window_lifecycle import WorkspaceWindow

phase, directory=sys.argv[1:]
base=Path(directory);base.mkdir(parents=True,exist_ok=True)
ds=UniformDataset(base/'catalog')
weights=base/'fixture-base.pt'
weights.write_bytes(b'Fixture only; manifest freeze does not execute model weights.')
service=CandidateService(ds,candidates_root=base/'candidates',base_weights=weights)
if phase=='review':
    folder=base/'photos';folder.mkdir(exist_ok=True)
    for i in range(3):
        Image.fromarray(np.random.default_rng(1000+i).integers(0,256,(180,220,3),dtype=np.uint8)).save(folder/f'{i}.png')
    # Pre-existing approved synthetic fixtures meet the unchanged 10/class minimum.
    for category_index,category in enumerate(('campus_uniform','organizational_shirt','other_clothing')):
        for i in range(9 if category=='other_clothing' else 10):
            p=base/f'seed-{category_index}-{i}.png'
            Image.fromarray(np.random.default_rng(category_index*100+i).integers(0,256,(180,220,3),dtype=np.uint8)).save(p)
            iid=ds.import_paths([p],category,capture_group=f'fixture-session-{category_index}')[0]
            ds.review(iid,category=category,crop=[10,10,210,170],status='approved',flags=[])
class FixtureWorkspace(UniformWorkspace):
    def __init__(self,*a,**kw):super().__init__(*a,dataset=ds,candidate_service=service,**kw)
app=WorkspaceWindow();app.geometry('1040x760');errors=[]
app.report_callback_exception=lambda *a:errors.append(str(a[1]))
with patch('ui.uniform_workspace.UniformWorkspace',FixtureWorkspace), patch('ui.training_panel.list_references',return_value=[]):
    panel=TrainingPanel(app,trainer=ViolationTrainer(),get_frame=lambda:None)
panel.pack(fill='both',expand=True);view=panel._uniform_workspace

def pump():
    app.after(35,app.quit);app.mainloop()
def wait(task=None):
    task=task or view.task;deadline=time.monotonic()+15
    while task.busy and time.monotonic()<deadline:pump()
    assert not task.busy,'Background task timeout'
    pump()
def widgets(parent):
    for w in parent.winfo_children():
        yield w;yield from widgets(w)
def button(parent,text):
    return next(w for w in widgets(parent) if isinstance(w,ctk.CTkButton) and w.cget('text')==text)
def review_record(record,status):
    view.open_review(record);r=view._review;wait(r.task)
    r.set_crop([20,25,200,160])
    button(r,'Preview corrected crop').invoke();wait(r.task)
    assert r.crop()==[20,25,200,160]
    assert ds.image(record['image_id'],crop=False).size==(220,180)
    for check in r.flags.values():check.deselect()
    r.confirm.select()
    button(r,'Approve example' if status=='approved' else 'Exclude example').invoke()
    wait(r.task);wait()
try:
    wait();view.tabs._segmented_button._buttons_dict['Wrong Uniform'].invoke();wait()
    assert view.category()=='other_clothing'
    if phase=='review':
        with patch('ui.uniform_workspace.filedialog.askdirectory',return_value=str(base/'photos')):
            button(view.tabs.tab('Wrong Uniform'),'Import Folder').invoke();wait()
        assert 'Imported: 3' in view.status.cget('text')
        imported=[r for r in ds.snapshot()['records'] if r['status']=='pending_review']
        assert len(imported)==3
        review_record(imported[0],'approved');review_record(imported[2],'excluded')
        expected={'approved':imported[0]['image_id'],'pending':imported[1]['image_id'],'excluded':imported[2]['image_id']}
        (base/'expected.json').write_text(json.dumps(expected,indent=2))
        with patch('ui.uniform_workspace.filedialog.askdirectory',return_value=str(base/'photos')):
            button(view.tabs.tab('Wrong Uniform'),'Import Folder').invoke();wait()
        assert 'Imported: 0; skipped as duplicates: 3; left unprocessed: 0' in view.status.cget('text')
        assert '12 unique images • 10 approved • 1 pending review • 1 excluded' in view.views['other_clothing'][1].cget('text')
    else:
        expected=json.loads((base/'expected.json').read_text())
        records={r['image_id']:r for r in ds.snapshot()['records']}
        for key,status in [('approved','approved'),('pending','pending_review'),('excluded','excluded')]:
            assert records[expected[key]]['status']==status
        assert records[expected['approved']]['crop']==[20,25,200,160]
        assert '12 unique images • 10 approved • 1 pending review • 1 excluded' in view.views['other_clothing'][1].cget('text')
        button(view,'Train candidate with approved datasets').invoke();wait();window=view._candidate_window;wait(window.task)
        assert 'Wrong Uniform: 10 approved' in window.coverage.cget('text')
        # Freeze is exactly the shared trainer's production manifest path; no training process.
        run=service._run_dir('f'*32);run.mkdir()
        frozen=service._freeze(run,service.validate_config({'epochs':1}))
        manifest=json.loads((run/'dataset_manifest.json').read_text())
        entries={r['image_id']:r for r in manifest['entries']}
        entry=entries[expected['approved']]
        assert entry['category']=='other_clothing' and entry['class_index']==2 and entry['split']=='train'
        assert expected['pending'] not in entries and expected['excluded'] not in entries
        assert len(entries)==30 and manifest['split_policy']=='experimental_train_only_no_validation_or_evaluation'
        assert json.loads((run/'class_map.json').read_text())['classes'][2]=={'index':2,'category':'other_clothing'}
        service._verify_bundle(run)
        report={'passed':True,'separate_process_reopen':True,'class_id':2,'category':'other_clothing','split':entry['split'],
                'approved_id':expected['approved'],'pending_excluded_absent':True,'unique_wrong':12,'approved_wrong':10,
                'pending_wrong':1,'excluded_wrong':1,'total_manifest_entries':30,'training_started':False,
                'manifest':str(run/'dataset_manifest.json'),'synthetic_fixture_only':True}
        (REPO/'output/wrong-uniform/restart-workflow-result.json').write_text(json.dumps(report,indent=2))
    assert not errors,errors
    print(json.dumps({'phase':phase,'passed':True,'directory':str(base),'errors':errors}),flush=True)
finally:
    app.destroy();gc.collect()
