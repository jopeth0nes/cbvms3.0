"""Independent mail-only relay; no campus database, UI, or model imports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from core.recovery_mail import process_recovery_mail_once, check_smtp_connection

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'data' / 'recovery-relay.json'


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_relay_config(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Relay configuration cannot be a symlink.')
    info = path.stat()
    if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError('Relay configuration must belong to this user with mode 0600.')
    with path.open(encoding='utf-8') as stream:
        config = json.load(stream)
    if not isinstance(config, dict) or set(config) != {'origin', 'relay_key', 'enabled'} or type(config['enabled']) is not bool:
        raise ValueError('Invalid mail-only configuration.')
    RecoveryRelayClient(config)
    return config


class RecoveryRelayClient:
    def __init__(self, config):
        origin = config.get('origin', '')
        key = config.get('relay_key', '')
        if not isinstance(origin, str) or not isinstance(key, str):
            raise ValueError('Invalid relay connection.')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in origin)):
            raise ValueError('Relay requires an exact HTTPS origin.')
        parsed.port
        if len(key) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError('Invalid relay credential.')
        self.origin = origin
        self.headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                        'User-Agent': 'CBVMS-Recovery-Mail/1'}
        self.opener = build_opener(_NoRedirect)

    def call(self, path, data=None):
        if path not in {'mail/heartbeat', 'mail/claim', 'mail/receipt', 'mail/status'}:
            raise ValueError('Only recovery mail operations are allowed.')
        request = Request(self.origin + '/api/relay/' + path,
                          data=None if data is None else json.dumps(data).encode(), headers=self.headers)
        with self.opener.open(request, timeout=20) as response:
            raw = response.read(32769)
            if len(raw) > 32768:
                raise ValueError('Relay response too large.')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('Invalid relay response.')
            return result


def relay_cycle(client, *, check_health=True):
    if check_health:
        ready = check_smtp_connection()
        client.call('mail/heartbeat', {'ready': ready})
        if not ready:
            return 'smtp_unavailable'
    result = process_recovery_mail_once(client)
    if result in {'failed', 'uncertain', 'unconfigured'}:
        client.call('mail/heartbeat', {'ready': False})
    return result


def write_relay_status(path, state):
    """Write only sanitized operational state; never envelopes or provider errors."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump({'state': state, 'checked_at': int(time.time())}, stream)
    os.replace(temporary, target)


def run_relay(config_path=DEFAULT_CONFIG, *, once=False, stop=None, status_path=None):
    import threading
    stop = stop or threading.Event()
    status_path = status_path or ROOT / '.runtime/recovery-relay/status.json'
    health_at = 0.0
    while not stop.is_set():
        try:
            config = load_relay_config(config_path)
            if not config['enabled']:
                state = 'disabled'
                health_at = 0
            else:
                client = RecoveryRelayClient(config)
                check = time.monotonic() >= health_at
                state = relay_cycle(client, check_health=check)
                if check:
                    health_at = time.monotonic() + 45
                if state in {'smtp_unavailable', 'failed', 'uncertain', 'unconfigured'}:
                    health_at = 0
        except HTTPError as exc:
            state = 'relay_authorization_failed' if exc.code in (401, 403) else 'cloud_unavailable'
            health_at = 0
        except Exception:
            state = 'connection_or_configuration_error'
            health_at = 0
        write_relay_status(status_path, state)
        if once:
            return state
        stop.wait(10)
