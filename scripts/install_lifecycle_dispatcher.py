#!/usr/bin/env python3
"""Install the trusted per-user provider dispatcher after reviewing its config."""
import argparse,json,os,plistlib,subprocess,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    config=Path(a.config).resolve();settings=json.loads(config.read_text())
    for key in ('local_db','import_bundle','provider_credentials','node','wrangler'):
        if not Path(settings[key]).is_file():raise ValueError('Missing configured '+key)
    root=Path(__file__).resolve().parents[1]
    label='com.cbvms.student-lifecycle'
    target=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
    payload={'Label':label,'ProgramArguments':[str(root/'.venv/bin/python'),str(root/'scripts/lifecycle_dispatcher.py'),'--config',str(config)],
             'WorkingDirectory':str(root),'RunAtLoad':True,'StartInterval':60,
             'StandardOutPath':str(config.parent/'dispatcher.log'),'StandardErrorPath':str(config.parent/'dispatcher-error.log'),
             'Umask':63}
    if not a.apply:print('Prepared '+str(target));return
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        old=plistlib.loads(target.read_bytes())
        if old.get('Label')!=label:raise ValueError('Unrelated launch agent conflict')
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'wb') as f:plistlib.dump(payload,f)
    subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(target)],check=True,capture_output=True)
    print('Installed provider dispatcher; checks durable operations every 60 seconds while this user is logged in.')
if __name__=='__main__':main()
