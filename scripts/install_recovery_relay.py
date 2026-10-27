#!/usr/bin/env python3
"""Install the independent mail-only relay as a per-user macOS background service."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.recovery_relay import DEFAULT_CONFIG, load_relay_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = args.config.resolve()
    load_relay_config(args.config)
    if sys.platform != 'darwin':
        raise ValueError('This installer requires macOS. Run recovery_mail_relay.py with your service manager.')
    label = 'com.cbvms.recovery-mail'
    target = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    runtime = ROOT / '.runtime/recovery-relay'
    payload = {
        'Label': label,
        'ProgramArguments': [str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/recovery_mail_relay.py'), '--config', str(config)],
        'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 15, 'Umask': 63,
        'StandardOutPath': str(runtime / 'relay.log'),
        'StandardErrorPath': str(runtime / 'relay-error.log'),
    }
    if not args.apply:
        print('Prepared ' + str(target))
        return
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError('Launch agent cannot be a symlink.')
    if target.exists():
        old = plistlib.loads(target.read_bytes())
        if old.get('Label') != label or old.get('ProgramArguments', [])[:2] != payload['ProgramArguments'][:2]:
            raise ValueError('Existing unrelated launch agent preserved.')
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{label}'], capture_output=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        plistlib.dump(payload, stream)
    subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)], check=True, capture_output=True)
    print('Installed recovery mail service; reuses existing SMTP while this user is logged in.')


if __name__ == '__main__':
    main()
