#!/usr/bin/env python3
"""Provider-admin reconciliation for an already archived local student.

Default is read-only preflight. --apply explicitly submits one durable operation.
Credentials are read from a provider-managed TOML file, never printed or stored
in the student app. Cloud migration 0006 must already be deployed separately.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tomllib
import urllib.request
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.archive_cloud import command_for_import, process_operation, recover

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Provider redirect refused')

class Provider:
    def __init__(self, account, database, credentials):
        if any(not v or any(c not in '0123456789abcdef-' for c in v) for v in (account, database)):
            raise ValueError('Invalid provider resource identifiers')
        self.url = f'https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query'
        self.credentials = credentials
    def query(self, sql, params):
        token = tomllib.loads(Path(self.credentials).read_text())['oauth_token']
        request = urllib.request.Request(self.url, data=json.dumps({'sql':sql,'params':params}).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            data=json.load(response)
        if not data.get('success') or not all(r.get('success') for r in data['result']):
            raise RuntimeError('Provider query failed; recover the operation before retry')
        return data['result'][0]['results']

class LocalOutbox:
    def __init__(self, path): self.path = Path(path).resolve()
    def get_archive_operation(self, operation_id):
        with sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            row=conn.execute('SELECT o.* FROM student_archive_operations o JOIN students s ON s.student_id=o.student_id WHERE o.operation_id=? AND s.is_archived=1',(operation_id,)).fetchone()
            return dict(row) if row else None
    def set_archive_cloud_result(self, operation_id, state, error=''):
        if state not in ('verified','failed'): raise ValueError('Invalid result')
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE student_archive_operations SET state=?,error=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE operation_id=? AND (state<>'verified' OR ?='verified')",(state,error,operation_id,state))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('local-db','operation-id','import-bundle','command-file','account','cloud-database','provider-credentials'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--apply',action='store_true')
    a=p.parse_args(); str(uuid.UUID(a.operation_id))
    local=LocalOutbox(a.local_db); operation=local.get_archive_operation(a.operation_id)
    if not operation: raise ValueError('Archived local operation not found')
    provider=Provider(a.account,a.cloud_database,a.provider_credentials)
    path=Path(a.command_file)
    if path.exists():
        command=json.loads(path.read_text())
        if command['operation_id']!=a.operation_id or command['student_id']!=operation['student_id']:
            raise ValueError('Stored operation conflict')
    else:
        bundle=json.loads(Path(a.import_bundle).read_text())
        accounts=provider.query('SELECT student_id,username,credential_version FROM portal_accounts WHERE student_id=?',[operation['student_id']])
        if len(accounts)!=1: raise ValueError('Cloud identity missing')
        command=command_for_import(operation,bundle,accounts[0])
        receipts=provider.query('SELECT source_checkpoint,payload_sha256,mapping_json FROM campus_portal_imports WHERE import_id=?',[command['import_id']])
        if len(receipts)!=1 or receipts[0]['source_checkpoint']!=command['source_checkpoint'] or receipts[0]['payload_sha256']!=command['payload_sha256'] or json.loads(receipts[0]['mapping_json']) != bundle['mapping']:
            raise ValueError('Import provenance mismatch')
        if a.apply:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:
                json.dump(command,f);f.flush();os.fsync(f.fileno())
    if a.apply:
        process_operation(local,a.operation_id,provider,command)
        print('Archived — cloud revocation verified.')
    else:
        done=recover(provider,command)
        print('Read-only preflight complete; revocation '+('already verified.' if done else 'not yet applied.'))

if __name__=='__main__':
    print('The archive-only CLI is retired. Use the trusted lifecycle_dispatcher.py service for superadmin recycle/restore operations.', file=sys.stderr)
    sys.exit(2)
