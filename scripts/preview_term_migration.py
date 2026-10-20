"""Read-only preview, or backup and apply ONLY to a new isolated copy.

Never calls application initialize(), deadline processing, sync or camera startup.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database.term_migration import preview, migrate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True,type=Path)
    parser.add_argument('--apply-to-new-copy',type=Path,help='Optional NEW isolated database path. Source is never migrated.')
    args=parser.parse_args()
    report=preview(args.db)
    if args.apply_to_new_copy:
        target=args.apply_to_new_copy.resolve()
        if target.exists() or target==args.db.resolve():
            parser.error('Destination must be a new isolated file, never the source.')
        with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as source:
            with sqlite3.connect(target) as dest:
                source.backup(dest)  # Consistent SQLite snapshot includes committed WAL pages.
        with sqlite3.connect(target) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            migrate(conn)
        after=preview(target)
        if any(after['evidence_hashes'].get(table,{})!=report['evidence_hashes'].get(table,{})
               for table in after['evidence_hashes'].keys() | report['evidence_hashes'].keys()):
            raise RuntimeError('Evidence mismatch; preserve the copy for investigation.')
        report['isolated_copy']=str(target)
        report['copy_foreign_key_errors']=after['foreign_key_errors']
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
