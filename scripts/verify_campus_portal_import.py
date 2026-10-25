#!/usr/bin/env python3
"""Read-only import row/receipt verification from a current isolated D1 export.

Does not mutate/reimport accounts or verify R2 object bytes. The original campus
file may legitimately change after the one-time import; verification uses the
immutable original bundle and the current cloud export instead.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.portal_import import _digest, verify_imported_rows

def verify(bundle_path, cloud_path):
    bundle=json.loads(Path(bundle_path).read_text())
    if bundle['payload_sha256']!=_digest({k:v for k,v in bundle.items() if k!='payload_sha256'}):
        raise ValueError('Bundle integrity failed')
    with sqlite3.connect(Path(cloud_path).resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('BEGIN')
        def rows(table): return [dict(r) for r in conn.execute('SELECT * FROM '+table)]
        revoked=rows('portal_student_revocations') if conn.execute("SELECT 1 FROM sqlite_master WHERE name='portal_student_revocations'").fetchone() else []
        lifecycle_present = conn.execute("SELECT 1 FROM sqlite_master WHERE name='portal_lifecycle_operations'").fetchone()
        operations = rows('portal_lifecycle_operations') if lifecycle_present else []
        states = rows('portal_student_lifecycle') if lifecycle_present else []
        verify_imported_rows(bundle,rows('campus_portal_imports'),rows('portal_students'),rows('portal_accounts'),revoked,operations,states)
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Restore integrity failed')
    return {'import_rows_receipt_mapping_verified':True,'r2_bytes_verified':False,'writes':0}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',required=True);p.add_argument('--cloud-restore',required=True)
    a=p.parse_args()
    try: print(json.dumps(verify(a.bundle,a.cloud_restore)))
    except Exception as e:
        print('Verification requires reconciliation ('+type(e).__name__+'). Do not reimport.',file=sys.stderr);sys.exit(1)
