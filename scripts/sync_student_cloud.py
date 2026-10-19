"""Explicit protocol-v2 operations. The default is a genuinely read-only audit."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.sync_audit import audit, backup, readonly


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'data/cbvms.db')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'data/cloud-sync.json')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--audit', '--dry-run', action='store_true')
    group.add_argument('--backup', type=Path)
    group.add_argument('--register-source', metavar='SOURCE_UUID')
    group.add_argument('--once', action='store_true')
    parser.add_argument('--writer-id')
    args = parser.parse_args()
    if args.backup:
        result = backup(args.database, args.backup)
    elif args.register_source:
        import sqlite3
        import uuid
        if not args.writer_id:
            parser.error('--writer-id is required for explicit source registration')
        uuid.UUID(args.register_source)
        uuid.UUID(args.writer_id)
        with readonly(args.database) as check:
            if check.execute('SELECT COUNT(*) FROM students').fetchone()[0] == 0:
                parser.error('Cannot register an empty campus database')
        from database.cloud_sync_migration import migrate
        with sqlite3.connect(args.database) as conn:
            migrate(conn)
            conn.execute('INSERT INTO campus_sync_identity (singleton,source_id,writer_id) VALUES (1,?,?)', (args.register_source, args.writer_id))
        result = {'registered': True, 'uploads_enabled': False}
    else:
        client = None
        if args.config.exists():
            from core.cloud_sync import CloudClient
            config = json.loads(args.config.read_text())
            client = CloudClient(config)
        if args.once:
            if not client or config.get('enabled') is not True:
                parser.error('Apply paused: reviewed source registration and enabled=true are required')
            from core.cloud_sync import sync_once
            from database.db_manager import CBVMSDatabase
            count = sync_once(CBVMSDatabase(args.database), client, args.database.with_suffix('.sync-status.json'))
            result = {'verified_students': count}
        else:
            result = audit(args.database, client)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': 'Operation stopped. Check source registration, connectivity, schema and reconciliation report.'}))
        sys.exit(1)
