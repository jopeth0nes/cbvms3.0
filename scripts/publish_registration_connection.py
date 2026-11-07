"""Publish a reviewed additive Worker module and private campus connection.

Requires prepared release modules, verified backups, and explicit --apply.
Preserves Worker configuration/assets and all existing student credentials.
"""
import argparse
from email.parser import BytesParser
from email.policy import default
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tomllib
import urllib.request
import urllib.error
import uuid

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / '.runtime/registration-release'
BASE = 'https://api.cloudflare.com/client/v4/accounts/88e619a8bc929cbec00901fdadd3edc1/workers/scripts/cbvms-student-portal'


def api(path, data=None, content_type='application/json', method=None):
    token = tomllib.loads((ROOT / '.runtime/cloud-config/.wrangler/config/default.toml').read_text())['oauth_token']
    request = urllib.request.Request(BASE + path, data=data,
        headers={'Authorization':'Bearer '+token,'Content-Type':content_type}, method=method)
    return urllib.request.urlopen(request,timeout=45)


def upload_parts(parts, boundary):
    output = []
    for name, mime, content in parts:
        output.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode(), content, b'\r\n'])
    return b''.join(output) + f'--{boundary}--\r\n'.encode()


def enable_connection(config_path):
    config = json.loads(config_path.read_text())
    if config['origin'] != 'https://studentportal.bond' or not (RELEASE/'deployment-result.json').exists():
        raise ValueError('Reviewed deployment required before enablement.')
    for authorization, expected in ((None,401), ('Bearer '+config['key'],405)):
        headers = {'User-Agent':'CBVMS-Campus-Registration/1'}
        if authorization:
            headers['Authorization'] = authorization
        try:
            urllib.request.urlopen(urllib.request.Request(config['origin']+'/api/campus/registrations',headers=headers),timeout=20)
            raise RuntimeError('Expected authentication/method denial')
        except urllib.error.HTTPError as error:
            if error.code != expected:
                raise
    config['enabled'] = True
    temporary = config_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config,indent=2),encoding='utf-8')
    temporary.replace(config_path)
    print('Registration endpoint authentication verified; campus connection enabled.')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--enable',action='store_true',help='Recover enablement after a verified content deployment')
    args = parser.parse_args()
    config_path = ROOT / 'data/registration-sync.json'
    if args.enable:
        enable_connection(config_path)
        return
    if config_path.exists():
        raise ValueError('Existing registration configuration preserved; review separately.')
    original = (RELEASE / 'original.mjs').read_bytes()
    if hashlib.sha256(original).hexdigest() != (RELEASE/'original.sha256').read_text():
        raise ValueError('Prepared deployed module changed.')
    # Refuse to replace a Worker edited since its backup was captured.
    with api('') as response:
        content_type = response.headers['Content-Type']
        live = response.read()
    message = BytesParser(policy=default).parsebytes(('Content-Type: '+content_type+'\r\n\r\n').encode()+live)
    modules = list(message.iter_parts())
    if len(modules) != 1 or modules[0].get_payload(decode=True) != original:
        raise ValueError('Live Worker changed since preparation; refresh the backup and review.')
    if not args.apply:
        print('Preflight passed. New registration endpoint only; no existing account updates. Use --apply after tests and backups.')
        return
    config = {'enabled':False,'origin':'https://studentportal.bond','key':secrets.token_urlsafe(48),'source_id':str(uuid.uuid4())}
    # Exclusive local secret creation ensures a lost deployment response is recoverable.
    fd = os.open(config_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as out:
        json.dump(config,out,indent=2)
    with api('/secrets',json.dumps({'name':'CAMPUS_REGISTRATION_KEY','type':'secret_text','text':config['key']}).encode(),method='PUT') as response:
        if not json.load(response).get('success'):
            raise RuntimeError('Registration secret installation failed; local connection remains disabled.')
    parts = [('metadata','application/json',json.dumps({'main_module':'worker.js'}).encode())]
    parts += [(name,'application/javascript+module',(RELEASE/name).read_bytes()) for name in ('worker.js','original.mjs','registration.mjs','records.mjs','decision-display.mjs')]
    boundary = 'cbvms-' + secrets.token_hex(16)
    with api('/content',upload_parts(parts,boundary),'multipart/form-data; boundary='+boundary,method='PUT') as response:
        result = json.load(response)
        if not result.get('success'):
            raise RuntimeError('Registration deployment not verified; local connection remains disabled.')
    (RELEASE/'deployment-result.json').write_text(json.dumps(result))
    enable_connection(config_path)
    print('Registration endpoint deployed and authenticated connection enabled. Existing cloud accounts were not replaced.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Connection publication stopped: '+type(error).__name__+'. No credentials are displayed; inspect local release state before retry.',file=sys.stderr)
        sys.exit(1)
