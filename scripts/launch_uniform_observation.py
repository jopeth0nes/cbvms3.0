"""Launch the normal app in a disposable, private observation-only workspace.

Copies code/model files, never production DB, datasets, activation pointers or
cloud/email configuration. Camera definitions are private copies; original
preferences are never edited. No symlinks/hardlinks to writable production data.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare_workspace(destination, config_path, primary, *, source=ROOT):
    from core.uniform_observation import ObservationConfig
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == source or source in destination.parents:
        raise ValueError('Private application workspace must be outside the source checkout')
    if any(destination.iterdir()):
        raise ValueError('Private application workspace must be empty')
    config = ObservationConfig.load(config_path)
    config_data = {'schema':1,'mode':'observation_only',**asdict(config)}
    config_data['candidate_bundle'] = str(Path(config.candidate_bundle).resolve())
    config_data['logo_verification'] = {'enabled':False}
    os.chmod(destination,0o700)
    for package in ('core','ui','auth','database','api','web','assets','models'):
        folder=source/package
        if folder.exists():
            if folder.is_symlink():raise ValueError('Application packages must be regular directories')
            for path in folder.rglob('*'):
                if path.is_symlink():
                    raise ValueError('Symlink in copied application package: '+str(path.relative_to(source)))
            shutil.copytree(folder,destination/package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for filename in ('main.py','web_portal.py'):
        if (source/filename).is_symlink():raise ValueError('Application entry points must be regular files')
        shutil.copy2(source/filename,destination/filename)
    for path in source.glob('yolo*.pt'):
        if path.is_symlink():raise ValueError('Model assets must be regular files')
        shutil.copy2(path,destination/path.name)
    data=destination/'data';data.mkdir(mode=0o700)
    if primary.casefold() == 'macbook camera':
        preference={'id':'usb_0','type':'usb','index':0,'label':'MacBook Camera'}
    else:
        camera_file=source/'data/ip_cameras.json'
        cameras=json.loads(camera_file.read_text()).get('cameras',[])
        matches=[c for c in cameras if str(c.get('label','')).casefold()==primary.casefold()]
        if len(matches)!=1:raise ValueError('Primary camera label must match exactly one saved camera')
        camera=matches[0]
        # A copied secret_ref may resolve through the existing authorized Keychain.
        # Do not print or resolve stream URLs/credentials during setup/preflight.
        (data/'ip_cameras.json').write_text(json.dumps({'cameras':[camera]}))
        os.chmod(data/'ip_cameras.json',0o600)
        preference={'id':'ip_'+str(camera['id']).removeprefix('ip_'),'type':'rj45','label':camera['label']}
    (data/'camera_preference.json').write_text(json.dumps(preference))
    # Empty preview selection prevents an extra preview-camera workload by default.
    (data/'camera_view_preference.json').write_text(json.dumps({'sources':[],'destination':'Separate window'}))
    private_config=destination/'observation.json'
    private_config.write_text(json.dumps(config_data,indent=2))
    return private_config, preference


def child_environment(private, config, backend):
    env={k:v for k,v in os.environ.items() if not k.startswith('CBVMS_') and k!='PYTHONPATH'}
    env.update(CBVMS_ASSESSMENT_OBSERVATION_ONLY='1',CBVMS_UNIFORM_OBSERVATION_CONFIG=str(config),
        CBVMS_BODY_YOLO_BACKEND=backend,CBVMS_PERF='1',CBVMS_TRACE='1',
        MPLCONFIGDIR=str(private/'cache/mpl'),YOLO_CONFIG_DIR=str(private/'cache/yolo'))
    if os.environ.get('CBVMS_FACE_MODEL_DIR'):
        env['CBVMS_FACE_MODEL_DIR']=str(Path(os.environ['CBVMS_FACE_MODEL_DIR']).expanduser().resolve())
    return env


PREFLIGHT = '''
import json, os, sys
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root))
from database.db_manager import CBVMSDatabase
from core.uniform_observation import ObservationUniformSession
from core.uniform_model_registry import candidate_descriptor
from core.assessment_candidates import OBSERVATION_ONLY
from api.camera_store import get_camera_preference
import main
from core.cloud_sync import start_cloud_sync
db=CBVMSDatabase();assert db.db_path==root/'data/cbvms.db'
db.initialize(process_deadlines=False)
session=ObservationUniformSession.from_environment(observation_only=OBSERVATION_ONLY)
descriptor=candidate_descriptor(session.config.candidate_bundle)
assert descriptor['id']==session.config.candidate_sha256
assert not (root/'data/cloud-sync.json').exists()
assert not (root/'data/uniform_models/active.json').exists()
assert not (root/'data/uniform_stage1').exists()
assert not (root/'data/recovery-relay.json').exists()
assert start_cloud_sync(db) is None
with db.connect() as connection:
    assert connection.execute('SELECT count(*) FROM students').fetchone()[0]==0
print(json.dumps({'observation_only':OBSERVATION_ONLY,'isolated_database':True,
    'empty_gallery':True,'candidate_synthetic':descriptor.get('synthetic'),
    'candidate_sha256':descriptor['id'],'primary_camera':get_camera_preference(),
    'logo_enabled':False,'camera_opened':False,'cloud_config_copied':False}))
'''


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--primary',default='front gate')
    parser.add_argument('--body-backend',choices=('cpu','mps'),default='cpu')
    parser.add_argument('--check',action='store_true',help='Verify isolation/bundle/primary selection; no camera or UI opens')
    args=parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='cbvms-observation-') as tmp:
        private=Path(tmp)
        config,preference=prepare_workspace(private,args.config,args.primary)
        env=child_environment(private,config,args.body_backend)
        print('Isolated observation workspace; primary='+preference['label']+
              '. Empty evaluation roster. No production datasets or activation pointer copied.',flush=True)
        command=([sys.executable,'-I','-c',PREFLIGHT] if args.check else
                 [sys.executable,'-I',str(private/'main.py')])
        # Normal app authentication and camera ownership are preserved. Only the
        # storage root and explicit observation configuration differ.
        return subprocess.call(command,cwd=private,env=env)


if __name__=='__main__':raise SystemExit(main())
