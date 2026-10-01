"""Read the source only; migrate a SQLite backup in a temporary directory."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.db_manager import CBVMSDatabase
from database.appeal_migration import preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        before = preview(source)
        with tempfile.TemporaryDirectory(prefix='cbvms-migration-') as directory:
            path = Path(directory) / 'copy.db'
            with sqlite3.connect(path) as target:
                source.backup(target)
            db = CBVMSDatabase(path)
            db.initialize(process_deadlines=False)
            with db.connect() as conn:
                once = list(map(tuple, conn.execute('SELECT * FROM violations ORDER BY id')))
                after = preview(conn)
                origins = dict(conn.execute('SELECT lifecycle_origin,COUNT(*) FROM violations GROUP BY lifecycle_origin'))
            db.initialize(process_deadlines=False)
            with db.connect() as conn:
                repeatable = once == list(map(tuple, conn.execute('SELECT * FROM violations ORDER BY id')))
            proposed = []
            for conflict in before['conflicts']:
                if conflict['appeal_status'] == 'pending':
                    db.reconcile_pending_appeal_strike(conflict['violation_id'], username='admin',
                        reason='Disposable preview: remove strike while historical appeal is pending')
                    proposed.append(conflict['violation_id'])
            with db.connect() as conn:
                reconciled = preview(conn)
            print(json.dumps(dict(before=before, after=after, origins=origins, repeatable=repeatable,
                simulated_reconciliation_ids=proposed, after_simulated_reconciliation=reconciled,
                source_modified=False, reconciliation='Deactivate conflicting strikes with an audited reason; '
                'recompute threshold eligibility, preserve decisions and explicit office-clearance suspensions. '
                'Pending appeals then remain protected until decision. Requires historical reconciliation approval.'), indent=2))

if __name__ == '__main__':
    main()
