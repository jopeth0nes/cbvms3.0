#!/usr/bin/env python3
"""Bounded native navigation audit on a read-only backup of the actual database.

Run from a desktop session:
    .venv/bin/python scripts/verify_portal_navigation.py --output /tmp/portal-audit.json
The source database is never initialized or written. Deadline transitions happen
only in its disposable copy. The child is terminated if Tk stops processing events.
"""
import argparse
import gc
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_child(args):
    from database.db_manager import CBVMSDatabase
    from ui.student_portal import StudentPortal, _NAV_ITEMS
    with tempfile.TemporaryDirectory(prefix='cbvms-portal-audit-') as folder:
        copy = Path(folder) / 'portal.db'
        source = sqlite3.connect(f'{args.database.resolve().as_uri()}?mode=ro', uri=True)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
            mappings = source.execute('SELECT student_id FROM student_accounts WHERE student_id = ?', (args.student,)).fetchall()
            owner_counts = dict(source.execute('SELECT status, COUNT(*) FROM violations WHERE student_id = ? GROUP BY status', (args.student,)))
        finally:
            source.close()
            target.close()
        db = CBVMSDatabase(copy)
        app = StudentPortal(student_id=args.student, display_name='Isolated portal verification', database=db)
        app.title('CBVMS PORTAL TEST — disposable database copy')
        errors, samples = [], []
        app.report_callback_exception = lambda kind, value, tb: errors.append(f'{kind.__name__}: {value}')
        routes = iter([route for route, _ in _NAV_ITEMS] * 3)
        current = None
        started = time.monotonic()
        route_started = started
        callback_counts = []

        def advance():
            nonlocal current, route_started
            if app._closed:
                return
            if errors or time.monotonic() - started > 65:
                errors.append('Audit failed or exceeded its event-loop deadline')
                app._on_close()
                return
            if app._page_state == 'failed':
                errors.append(app._page_status.cget('text'))
                app._on_close()
                return
            ready = (app._request is None and app._page_state in {'loaded', 'empty'}
                     and app._page_scroll._parent_canvas.winfo_height() > 10)
            if not ready:
                app.after(25, advance)
                return
            if current is not None:
                metric = dict(app._navigation_metrics[-1])
                metric.update(app._timings)
                metric['visible_ms'] = round((time.monotonic() - route_started) * 1000, 2)
                samples.append(metric)
                callback_counts.append(len(app.tk.call('after', 'info')))
            try:
                current = next(routes)
            except StopIteration:
                app._logout_button.invoke()
                return
            route_started = time.monotonic()
            app._nav_btns[current].invoke()
            app.after(25, advance)

        app.after(250, advance)
        app.mainloop()
        stopped = app._refresh.done.wait(3)
        if not stopped:
            errors.append('Portal worker did not exit')
        summary = {
            'source': str(args.database.resolve()), 'student_id': args.student,
            'persisted_account_mapping': bool(mappings),
            'source_violation_counts': owner_counts,
            'samples': samples, 'callback_counts': callback_counts,
            'logout': app.logged_out, 'worker_stopped': stopped,
            'errors': errors, 'passed': not errors and len(samples) == 21 and app.logged_out,
            'live_database_writes': 0,
        }
        args.output.write_text(json.dumps(summary, indent=2))
        print(json.dumps({k: v for k, v in summary.items() if k not in {'samples', 'callback_counts'}}), flush=True)
        gc.collect()
        return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'data' / 'cbvms.db')
    parser.add_argument('--student', default='2023-00883')
    parser.add_argument('--output', type=Path, default=Path('/tmp/cbvms-portal-audit.json'))
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return run_child(args)
    try:
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child',
                               '--database', str(args.database.resolve()), '--student', args.student,
                               '--output', str(args.output.resolve())], timeout=80).returncode
    except subprocess.TimeoutExpired:
        print('FAIL: native portal audit exceeded 80 seconds; child terminated.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
