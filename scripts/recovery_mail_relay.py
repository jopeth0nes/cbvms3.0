#!/usr/bin/env python3
"""Run the CBVMS mail relay using existing Settings > Email SMTP credentials."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.recovery_relay import DEFAULT_CONFIG, run_relay, load_relay_config, RecoveryRelayClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--status', action='store_true', help='Read sanitized cloud mail-relay health')
    args = parser.parse_args()
    if args.status:
        try:
            config = load_relay_config(args.config)
            print(json.dumps(RecoveryRelayClient(config).call('mail/status')))
            return 0
        except Exception:
            print('Relay status unavailable; check the restricted configuration and internet connection.', file=sys.stderr)
            return 1
    result = run_relay(args.config, once=args.once)
    if args.once:
        print(result)
        return 0 if result in {'empty', 'accepted'} else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
