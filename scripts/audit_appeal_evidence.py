"""Read-only association audit and additive-schema preview on a disposable backup."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.evidence_integrity import digest, original_evidence, supporting_evidence
from database.db_manager import CBVMSDatabase


def record(row):
    row = dict(row)
    for column in ('snapshot','file_data'):
        if column in row:
            data = row.pop(column)
            row[column+'_bytes'] = len(data or b'')
            row[column+'_actual_sha256'] = digest(data)
    return row


def preserved(conn):
    """Fingerprint every original column, including bytes; exclude only the additive schema."""
    result = {}
    for table in ('violations','appeals','evidence_files','strikes','suspensions','decision_history'):
        columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')
                   if r[1] not in ('snapshot_sha256','snapshot_provenance','file_sha256')]
        if columns:
            result[table] = [tuple(r) for r in conn.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY id')]
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    parser.add_argument('--appeal',type=int,default=3)
    parser.add_argument('--date',default='2026-10-01',help='Candidate UTC detection date')
    parser.add_argument('--compare-backup',type=Path)
    args=parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as source:
        source.row_factory=sqlite3.Row
        # A read transaction makes the audit and copy a coherent point-in-time view.
        source.execute('BEGIN')
        appeal=source.execute('SELECT * FROM appeals WHERE id=?',(args.appeal,)).fetchone()
        if not appeal:raise SystemExit('Appeal not found')
        violation=source.execute('SELECT * FROM violations WHERE id=?',(appeal['violation_id'],)).fetchone()
        fields='id,student_id,timestamp,appeal_opened_at,appeal_deadline,status,lifecycle_origin,snapshot'
        candidates=source.execute(f'SELECT {fields} FROM violations WHERE student_id=? AND date(timestamp)=? ORDER BY id',
                                  (appeal['student_id'],args.date)).fetchall()
        report=dict(source_modified=False,appeal=record(appeal),linked_violation=record(violation),
            candidates=[record(r) for r in candidates],supporting_evidence=[record(r) for r in source.execute(
                'SELECT * FROM evidence_files WHERE appeal_id=? ORDER BY id',(args.appeal,))])
        before=preserved(source)
        with tempfile.TemporaryDirectory(prefix='cbvms-evidence-preview-') as directory:
            path=Path(directory)/'copy.db'
            with sqlite3.connect(path) as target:source.backup(target)
            db=CBVMSDatabase(path);db.initialize(process_deadlines=False)
            with db.connect() as conn:
                report['preview_existing_values_preserved']=before==preserved(conn)
                once={t:[tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')] for t in before}
                report['original_integrity_warnings']=[dict(id=r['id'],warning=e['warning']) for r in conn.execute('SELECT * FROM violations')
                    if (e:=original_evidence(r))['warning']]
                report['supporting_integrity_warnings']=[dict(id=r['id'],warning=e['warning']) for r in conn.execute('SELECT * FROM evidence_files')
                    if (e:=supporting_evidence(r))['warning']]
                report['legacy_originals_without_provenance']=conn.execute('SELECT COUNT(*) FROM violations WHERE snapshot IS NOT NULL AND snapshot_provenance IS NULL').fetchone()[0]
            db.initialize(process_deadlines=False)
            with db.connect() as conn:
                report['preview_idempotent']=all(once[t]==[tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')] for t in before)
        if args.compare_backup:
            with sqlite3.connect(args.compare_backup.resolve().as_uri()+'?mode=ro',uri=True) as old:
                old.row_factory=sqlite3.Row
                prior=old.execute('SELECT violation_id,student_id,submitted_at FROM appeals WHERE id=?',(args.appeal,)).fetchone()
                original=old.execute('SELECT snapshot FROM violations WHERE id=?',(appeal['violation_id'],)).fetchone()
                report['backup_comparison']=dict(appeal=dict(prior) if prior else None,
                    original_sha256=digest(original['snapshot']) if original else None,
                    matches_current_link=bool(prior and prior['violation_id']==appeal['violation_id']),
                    matches_current_bytes=bool(original and original['snapshot']==violation['snapshot']))
        # Never propose a relink from date proximity or a visual guess.
        report['proposed_link_repairs']=[]
        report['historical_limit']='No recorded selection audit or independently stored capture provenance establishes a different historical association.'
        print(json.dumps(report,indent=2))

if __name__=='__main__':main()
