"""Sanitized operational state; disposable cache, never a sync checkpoint."""
import json
from pathlib import Path
import threading
_lock = threading.Lock()

def read_status(database_path):
    try:
        data = json.loads(Path(database_path).with_suffix('.sync-status.json').read_text())
        return data if isinstance(data, dict) else {'state': 'paused'}
    except (OSError, ValueError):
        return {'state': 'paused', 'message': 'Run a read-only audit and review source registration.'}

def write_status(database_path, **values):
    with _lock:
        data = read_status(database_path)
        data.update(values)
        path = Path(database_path).with_suffix('.sync-status.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data), encoding='utf-8')
        temp.replace(path)
