"""Add scoped record sync to the existing registration Worker, preserving live code."""
import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.publish_registration_connection import api, upload_parts, ROOT, RELEASE
from scripts.revoke_imported_student import Provider


def modules():
    with api('') as response:
        raw=response.read()
        mime=response.headers['Content-Type']
    message=BytesParser(policy=default).parsebytes(('Content-Type: '+mime+'\r\n\r\n').encode()+raw)
    return {part.get_filename() or part.get_param('name',header='Content-Disposition'):part.get_payload(decode=True) for part in message.iter_parts()},raw,mime


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    live,raw,mime=modules()
    if set(live) not in ({'worker.js','original.mjs','registration.mjs'}, {'worker.js','original.mjs','registration.mjs','records.mjs'}, {'worker.js','original.mjs','registration.mjs','records.mjs','decision-display.mjs'}):
        raise ValueError('Unexpected live modules; preserve and review deployment')
    for name in ('original.mjs','registration.mjs'):
        if live[name]!=(RELEASE/name).read_bytes():
            raise ValueError('Live original or registration module changed; review required')
    if not args.apply:
        print('Preflight passed: existing production modules match; record sync is additive.')
        return
    backup=ROOT/'data/backups'/('record-sync-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True)
    (backup/'worker.multipart').write_bytes(raw)
    (backup/'worker-content-type.txt').write_text(mime)
    with api('/settings') as response:
        (backup/'worker-settings.json').write_bytes(response.read())
    env=dict(os.environ,XDG_CONFIG_HOME=str(ROOT/'.runtime/cloud-config'),WRANGLER_SEND_METRICS='false',CLOUDFLARE_ACCOUNT_ID='88e619a8bc929cbec00901fdadd3edc1')
    exported=subprocess.run(['node',str(ROOT/'.runtime/cloud-tools/node_modules/wrangler/bin/wrangler.js'),
        'd1','export','640d2258-ca44-45b3-b83a-5e1f0caec663','--remote','--output',str(backup/'cloud.sql')],
        env=env,cwd=ROOT,capture_output=True)
    if exported.returncode or not (backup/'cloud.sql').exists():
        raise RuntimeError('Cloud backup failed; deployment cancelled')
    import sqlite3
    with sqlite3.connect(backup/'cloud-restore.db') as restored:
        restored.executescript((backup/'cloud.sql').read_text(encoding='utf-8'))
        if restored.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or restored.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Cloud backup validation failed')
    with sqlite3.connect(ROOT/'data/cbvms.db') as source, sqlite3.connect(backup/'campus.db') as dest:
        source.backup(dest)
    if modules()[0]!=live:
        raise ValueError('Worker changed during backup; cancelled')
    provider=Provider('88e619a8bc929cbec00901fdadd3edc1','640d2258-ca44-45b3-b83a-5e1f0caec663',ROOT/'.runtime/cloud-config/.wrangler/config/default.toml')
    provider.query((ROOT/'sites/student-portal/drizzle/0017_campus_record_cursors.sql').read_text(),[])
    parts=[('metadata','application/json',json.dumps({'main_module':'worker.js'}).encode())]
    parts += [(name,'application/javascript+module',(RELEASE/name).read_bytes()) for name in ('worker.js','original.mjs','registration.mjs','records.mjs','decision-display.mjs')]
    boundary='cbvms-'+secrets.token_hex(16)
    with api('/content',upload_parts(parts,boundary),'multipart/form-data; boundary='+boundary,method='PUT') as response:
        if not json.load(response).get('success'): raise RuntimeError('Deployment not confirmed')
    deployed=modules()[0]
    if any(deployed[name]!=(RELEASE/name).read_bytes() for name in deployed):
        raise RuntimeError('Deployed content verification failed')
    print('Scoped record sync deployed and content verified. Backups:',backup.relative_to(ROOT))

if __name__=='__main__':
    main()
