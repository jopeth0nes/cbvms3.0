"""Create a disabled protocol-v2 configuration through hidden terminal input.

Never replaces an existing configuration or registers a database. Reconciliation,
matching server registration and a read-only audit must precede enablement.
"""
import getpass
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import uuid
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.sync_audit import readonly, identity


def validate(value, database):
    required = {'origin', 'sync_key', 'source_id', 'writer_id'}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - (required | {'service_token', 'enabled'}):
        raise ValueError('Expected protocol-v2 connection fields.')
    origin = value['origin']
    if not isinstance(origin, str):
        raise ValueError('An exact HTTPS origin is required.')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in origin)):
        raise ValueError('An exact HTTPS origin is required.')
    parsed.port  # Reject malformed ports before saving credentials.
    for field in ('sync_key', 'service_token'):
        secret = value.get(field, '')
        if not isinstance(secret, str) or (field == 'sync_key' and not secret.strip()) or any(ord(c) < 32 or ord(c) == 127 for c in secret):
            raise ValueError('Invalid credential field.')
    for field in ('source_id', 'writer_id'):
        if not isinstance(value[field], str):
            raise ValueError('Registration identifiers must be UUID strings.')
        uuid.UUID(value[field])
    if value.get('enabled', False) is not False:
        raise ValueError('Initial configuration must remain disabled until reconciliation succeeds.')
    with readonly(database) as conn:
        registered = identity(conn)
    if not registered or registered['protocol'] != 2 or any(registered[k] != value[k] for k in ('source_id', 'writer_id')):
        raise ValueError('Configuration must match the reviewed local source registration.')
    return {**value, 'enabled': False}


def save_config(value, target, database):
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError('Existing configuration preserved; review it separately.')
    value = validate(value, database)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation refuses symlinks and concurrent configurations too.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(value, out, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())


def main():
    target = ROOT / 'data' / 'cloud-sync.json'
    if target.exists() or target.is_symlink():
        raise FileExistsError('Existing configuration preserved; review it separately.')
    # getpass otherwise falls back to echoed stdin; fail closed instead.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        value = json.loads(getpass.getpass('Protocol-v2 cloud sync JSON (hidden): '))
    save_config(value, target, ROOT / 'data' / 'cbvms.db')
    print('Restricted configuration saved with uploads disabled. Run the read-only audit before reviewed enablement.')


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt):
        # Parse exceptions can contain supplied secret values.
        print('Configuration not completed. Check protocol-v2 fields, local registration, terminal input and existing file; no existing configuration was replaced.', file=sys.stderr)
        sys.exit(1)
