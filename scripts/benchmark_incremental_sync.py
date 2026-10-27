#!/usr/bin/env python3
"""Synthetic SQLite benchmark for the local incremental sync substrate.

This uses only a disposable database and an injected in-process receipt transport.
It does not open a CBVMS database, camera, or network connection.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.incremental_sync import SyncWorker, acknowledge, eligible_heads, enqueue
from database.incremental_sync import migrate


HISTORY_COUNT = 5000


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * pct))]


def ticks_for(duration, db_path, *, active=None, interval=0.01):
    """Sample main-thread scheduling jitter while doing a small concurrent read."""
    samples = []
    reads = 0
    deadline = time.monotonic() + duration
    next_tick = time.monotonic() + interval
    while time.monotonic() < deadline and (active is None or active()):
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        actual = time.monotonic()
        samples.append(max(0.0, actual - next_tick))
        next_tick += interval
        with sqlite3.connect(db_path, timeout=0.25) as conn:
            conn.execute('SELECT COUNT(*) FROM incremental_outbox').fetchone()
            reads += 1
        # Skip missed intervals instead of trying to catch up in a burst.
        if next_tick < actual:
            next_tick = actual + interval
    return {
        'ticks': len(samples),
        'concurrent_reads': reads,
        'jitter_ms_p50': round(statistics.median(samples) * 1000, 3) if samples else 0.0,
        'jitter_ms_p95': round(percentile(samples, 0.95) * 1000, 3),
        'jitter_ms_max': round(max(samples, default=0.0) * 1000, 3),
    }


def synthetic_receipt(op):
    return {
        'operation_id': op['operation_id'],
        'device_id': op['device_id'],
        'entity_type': op['entity_type'],
        'entity_id': op['entity_id'],
        'operation_type': op['operation_type'],
        'expected_version': op['expected_version'],
        'payload_digest': op['payload_digest'],
        'status': 'accepted',
        'version': op['expected_version'] + 1,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operations', type=int, default=100,
                        help='pending unrelated entities to queue (1..1000; default 100)')
    args = parser.parse_args()
    if not 1 <= args.operations <= 1000:
        parser.error('--operations must be between 1 and 1000')

    with tempfile.TemporaryDirectory(prefix='cbvms-incremental-bench-') as temp_dir:
        db_path = Path(temp_dir) / 'synthetic.db'
        conn = sqlite3.connect(db_path, timeout=0.25)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('BEGIN IMMEDIATE')
        migrate(conn)
        conn.commit()

        tracemalloc.start()
        history_started = time.perf_counter()
        conn.execute('BEGIN IMMEDIATE')
        for index in range(HISTORY_COUNT):
            op_id = enqueue(
                conn, device_id='synthetic-device', entity_type='student',
                entity_id=f'history-{index:05d}', expected_version=0,
                operation_type='student.profile.patch', payload={'name': f'History {index}'},
            )
            row = conn.execute('SELECT * FROM incremental_outbox WHERE operation_id=?', (op_id,)).fetchone()
            acknowledge(conn, synthetic_receipt(row))
        for index in range(args.operations):
            enqueue(
                conn, device_id='synthetic-device', entity_type='student',
                entity_id=f'pending-{index:05d}', expected_version=0,
                operation_type='student.profile.patch', payload={'email': f'user{index}@example.invalid'},
            )
        conn.commit()
        history_and_queue_seconds = time.perf_counter() - history_started

        tracemalloc.reset_peak()
        read_conn = sqlite3.connect(db_path, timeout=0.25)
        read_conn.row_factory = sqlite3.Row
        selection_times = []
        for _ in range(20):
            started = time.perf_counter()
            selected = eligible_heads(read_conn, limit=10)
            selection_times.append(time.perf_counter() - started)
        read_conn.close()
        build_memory_current, build_memory_peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()

        idle_ticks = ticks_for(0.5, db_path)
        server_commits = set()
        transport_calls = 0
        transport_lock = threading.Lock()

        def transport(batch):
            nonlocal transport_calls
            # A small delay keeps multiple bounded batches active during tick sampling.
            time.sleep(0.05)
            with transport_lock:
                transport_calls += 1
                for op in batch:
                    server_commits.add(op['operation_id'])
            return [synthetic_receipt(op) for op in batch]

        statuses = []
        worker = SyncWorker(db_path, transport, batch_size=10,
                            status_callback=lambda state, _detail: statuses.append(state))
        worker.start(interval=0.005)

        def pending_remains():
            with sqlite3.connect(db_path, timeout=0.25) as check:
                return check.execute("SELECT 1 FROM incremental_outbox WHERE state='pending' LIMIT 1").fetchone() is not None

        busy_ticks = ticks_for(max(2.0, args.operations * 0.06 / 10), db_path, active=pending_remains)
        # Ensure bounded worker completion even if the host schedules the ticker heavily.
        completion_deadline = time.monotonic() + 10
        while pending_remains() and time.monotonic() < completion_deadline:
            time.sleep(0.01)
        worker.stop(timeout=3)
        worker_memory_current, worker_memory_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        with sqlite3.connect(db_path, timeout=0.25) as summary:
            accepted = summary.execute("SELECT COUNT(*) FROM incremental_outbox WHERE state='accepted'").fetchone()[0]
            remaining = summary.execute("SELECT COUNT(*) FROM incremental_outbox WHERE state='pending'").fetchone()[0]
            cursor = summary.execute('SELECT cursor FROM incremental_cursor WHERE singleton=1').fetchone()[0]

        # Directly measure the worker-configured SQLite lock timeout while another
        # disposable connection holds a RESERVED writer lock.
        lock_holder = sqlite3.connect(db_path, timeout=0.25)
        lock_holder.execute('BEGIN IMMEDIATE')
        lock_wait_started = time.perf_counter()
        lock_error = None
        try:
            worker._mark_auth([{'operation_id': 'synthetic-lock-probe'}])
        except sqlite3.OperationalError as exc:
            lock_error = type(exc).__name__
        lock_wait_seconds = time.perf_counter() - lock_wait_started
        lock_holder.rollback()
        lock_holder.close()

        output = {
            'benchmark': 'synthetic_incremental_sync',
            'synthetic_not_physical_camera': True,
            'database_disposable': True,
            'history_operations': HISTORY_COUNT,
            'pending_operations_requested': args.operations,
            'accepted_operations_after_worker': accepted,
            'pending_operations_after_worker': remaining,
            'server_commit_ids': len(server_commits),
            'transport_calls': transport_calls,
            'worker_status_counts': {state: statuses.count(state) for state in sorted(set(statuses))},
            'queue_selection_ms': {
                'samples': len(selection_times),
                'median': round(statistics.median(selection_times) * 1000, 3),
                'p95': round(percentile(selection_times, 0.95) * 1000, 3),
                'max': round(max(selection_times) * 1000, 3),
                'selected_per_sample': len(selected),
            },
            'build_history_and_queue_seconds': round(history_and_queue_seconds, 3),
            'idle_main_thread_ticks': idle_ticks,
            'busy_main_thread_ticks_with_worker_and_reads': busy_ticks,
            'tracemalloc_bytes': {
                'build_current': build_memory_current,
                'build_peak': build_memory_peak,
                'worker_current': worker_memory_current,
                'worker_peak': worker_memory_peak,
            },
            'sqlite_lock_probe': {
                'configured_timeout_seconds': 0.25,
                'wait_ms': round(lock_wait_seconds * 1000, 3),
                'error': lock_error,
                'writer_held': True,
            },
            'download_cursor': cursor,
        }
        print(json.dumps(output, sort_keys=True))
        conn.close()


if __name__ == '__main__':
    main()
