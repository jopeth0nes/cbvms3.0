#!/usr/bin/env python3
"""Trusted provider-admin outbox dispatcher, separate from the desktop UI.

Run periodically via the supplied launchd installer. Never initializes the campus
schema, accepts student HTTP requests, or copies Windows publisher credentials.
"""
import argparse,json,os,sqlite3,subprocess,sys,urllib.error
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database.db_manager import CBVMSDatabase
from scripts.revoke_imported_student import Provider
from core.student_lifecycle_cloud import dispatch_pending

class RefreshingProvider(Provider):
    def __init__(self,config):
        super().__init__(config['account'],config['cloud_database'],config['provider_credentials'])
        self.config=config
    def query(self,sql,params):
        try:return super().query(sql,params)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401,403):raise
        # Wrangler owns refresh-token rotation. Capture output; it may identify
        # the provider account and must never reach student UI or public logs.
        env=dict(os.environ,XDG_CONFIG_HOME=str(Path(self.credentials).parents[2]))
        subprocess.run([self.config['node'],self.config['wrangler'],'whoami'],env=env,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,check=True)
        return super().query(sql,params)

def run(config):
    path=Path(config['local_db']).resolve()
    if not path.is_file():raise ValueError('Configured campus database missing')
    # The desktop owns its additive schema upgrade; a service must not silently
    # initialize the live database merely to discover an empty queue.
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        columns={r[1] for r in conn.execute('PRAGMA table_info(student_archive_operations)')}
    if 'lifecycle_version' not in columns:
        return {'status':'waiting_for_desktop_schema','verified':0,'failed':0,'deferred':0}
    bundle=json.loads(Path(config['import_bundle']).read_text())
    database=CBVMSDatabase(path)
    result=dispatch_pending(database,RefreshingProvider(config),bundle)
    return {'status':'ready' if not result['failed'] else 'retry_pending',**result}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);a=p.parse_args()
    config=json.loads(Path(a.config).read_text())
    try:result=run(config)
    except Exception as exc:result={'status':'retry_pending','error_type':type(exc).__name__}
    result['checked_at']=datetime.now(timezone.utc).isoformat()
    status=Path(config['status_file']);status.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temporary=status.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f);f.flush();os.fsync(f.fileno())
    os.replace(temporary,status)
    print(json.dumps(result))
    return int(result['status']=='retry_pending')
if __name__=='__main__':sys.exit(main())
