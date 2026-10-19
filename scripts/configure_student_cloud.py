"""Accept the local sync configuration with terminal echo disabled."""
import getpass
import json
from pathlib import Path

if __name__ == '__main__':
    value = json.loads(getpass.getpass('Cloud sync JSON (hidden): '))
    if set(value) != {'origin', 'sync_key', 'service_token'}:
        raise ValueError('Expected Site origin and two sync credentials.')
    root = Path(__file__).resolve().parents[1]
    target = root / 'data' / 'cloud-sync.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value), encoding='utf-8')
    print('Local cloud sync configuration saved in the ignored data directory.')
