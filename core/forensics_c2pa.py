"""Offline C2PA verification with deliberately minimal persisted assertions."""
import hashlib
import io
import json
import os
import re
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

C2PA_VERSION = '0.38.0'

def _token(value):
    return re.sub(r'[^a-zA-Z0-9_.:/-]', '', str(value))[:160]

def summarize_manifest(store, state, anchors_sha256=None):
    active = store.get('active_manifest')
    if not active:
        return {'status': 'absent', 'summary': 'No embedded Content Credential; this is neutral.'}
    status = {'Trusted': 'valid', 'Valid': 'untrusted', 'Invalid': 'invalid'}.get(state, 'unavailable')
    manifest = store.get('manifests', {}).get(active, {})
    actions, sources = [], []
    # Store identifiers only, never arbitrary assertion bodies, identities, URLs or GPS.
    for assertion in manifest.get('assertions', [])[:64]:
        if assertion.get('label', '').startswith('c2pa.actions'):
            for action in assertion.get('data', {}).get('actions', [])[:64]:
                label = _token(action.get('action', ''))
                if label.startswith('c2pa.') and label not in actions:
                    actions.append(label)
                source = str(action.get('digitalSourceType', '')).rsplit('/', 1)[-1]
                if source and re.fullmatch('[a-zA-Z]{1,80}', source) and source not in sources:
                    sources.append(source)
    ai_types = {'trainedAlgorithmicMedia', 'compositeWithTrainedAlgorithmicMedia'}
    codes=[]
    results=store.get('validation_results', {})
    for section in [results.get('activeManifest', {}), *[x.get('validationResults', {}) for x in results.get('ingredientDeltas', [])[:32]]]:
        for item in section.get('failure', [])[:32]:
            code=_token(item.get('code', ''))
            if code and code not in codes: codes.append(code)
    return {'status':status, 'validation_state':state, 'recorded_actions':actions[:32],
            'recorded_source_types':sources[:32], 'ai_provenance_recorded':bool(ai_types.intersection(sources)),
            'assertions_verified':status == 'valid', 'validation_codes':codes[:32],
            'trust_anchors_sha256':anchors_sha256,
            'scope':'Embedded credentials only; remote manifests and online revocation checks disabled. Editing history does not establish misconduct.'}

def inspect_c2pa(data, image_format):
    try:
        installed=version('c2pa-python')
    except PackageNotFoundError:
        return {'status':'unavailable','reason':'optional_runtime_not_installed'}
    if installed != C2PA_VERSION:
        return {'status':'unavailable','reason':'unsupported_sdk_version','sdk_version':installed[:30]}
    import c2pa
    if image_format not in ('JPEG','PNG'):
        return {'status':'unavailable','reason':'unsupported_container','sdk_version':installed}
    config={'verify':{'verify_after_reading':True,'verify_trust':True,'verify_timestamp_trust':True,
                      'ocsp_fetch':False,'remote_manifest_fetch':False},
            'core':{'allowed_network_hosts':[], 'decode_identity_assertions':False}}
    anchors_hash=None
    path=os.environ.get('CBVMS_FORENSICS_C2PA_TRUST_ANCHORS')
    if path:
        try:
            with Path(path).open('rb') as stream: anchors=stream.read(1024*1024+1)
            if len(anchors)>1024*1024 or b'-----BEGIN CERTIFICATE-----' not in anchors: raise ValueError()
            config['trust']={'trust_anchors':anchors.decode('ascii')}
            anchors_hash=hashlib.sha256(anchors).hexdigest()
        except (OSError, ValueError, UnicodeError):
            return {'status':'error','reason':'trust_configuration_invalid','sdk_version':installed}
    try:
        with c2pa.Context.from_dict(config) as context:
            with c2pa.Reader({'JPEG':'image/jpeg','PNG':'image/png'}[image_format], io.BytesIO(data), context=context) as reader:
                raw=reader.json()
                if len(raw)>2*1024*1024: raise ValueError('manifest_size_limit')
                report=summarize_manifest(json.loads(raw), reader.get_validation_state(), anchors_hash)
        return dict(report, sdk_version=installed)
    except c2pa.C2paError.ManifestNotFound:
        return {'status':'absent','sdk_version':installed,'summary':'No embedded Content Credential; this is neutral.'}
    except Exception as exc:
        # Remote-only or malformed credentials cannot be labeled absent/invalid from an exception.
        return {'status':'error','sdk_version':installed,'reason':'credential_verification_unavailable',
                'error_type':type(exc).__name__[:64]}
