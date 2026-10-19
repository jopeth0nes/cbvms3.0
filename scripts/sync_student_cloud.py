"""Run this without the desktop app, or use --once for a manual sync."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.cloud_sync import CONFIG, ROOT, CloudClient, sync_once
from database.db_manager import CBVMSDatabase

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    database = CBVMSDatabase()
    database.initialize()
    while True:
        try:
            client = CloudClient(json.loads(CONFIG.read_text(encoding='utf-8')))
            count = sync_once(database, client, ROOT / 'data' / 'cloud-sync-state.json')
            print(f'Synced {count} student records.', flush=True)
        except Exception as exc:
            print(f'Cloud sync failed ({type(exc).__name__}); retrying in 60 seconds.', flush=True)
            if args.once:
                raise
        if args.once:
            break
        time.sleep(60)
