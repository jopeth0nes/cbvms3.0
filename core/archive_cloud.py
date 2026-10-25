"""Explicit provider-administrative revocation; never exposes provider auth to UI.

Transport.query(sql, params) is an authenticated D1 provider adapter returning
row dictionaries. No student HTTP endpoint or campus SYNC_KEY can invoke this.
A timed-out INSERT is always recovered by its original durable operation ID.
"""
from __future__ import annotations
import uuid

FIELDS = ('operation_id', 'student_id', 'import_id', 'source_checkpoint',
          'payload_sha256', 'expected_username', 'expected_credential_version',
          'expected_version', 'version')


def command_for_import(operation, bundle, account):
    """Bind a local archived identity to a previously verified immutable import."""
    from core.portal_import import _digest
    if bundle.get('payload_sha256') != _digest({k: v for k, v in bundle.items() if k != 'payload_sha256'}):
        raise ValueError('Import bundle integrity check failed')
    sid = operation['student_id']
    original = next((a for a in bundle['accounts'] if a['student_id'] == sid), None)
    if sid not in bundle['mapping'] or not original or account['student_id'] != sid or account['username'] != original['username']:
        raise ValueError('Imported identity requires reconciliation')
    return dict(zip(FIELDS, (str(uuid.UUID(operation['operation_id'])), sid,
        bundle['import_id'], bundle['source_checkpoint'], bundle['payload_sha256'],
        original['username'], account['credential_version'], 0, 1)))


def recover(transport, command):
    rows = transport.query('SELECT * FROM portal_student_revocations WHERE operation_id=? OR student_id=?',
                           [command['operation_id'], command['student_id']])
    if not rows:
        return False
    if len(rows) != 1 or any(rows[0][k] != command[k] for k in FIELDS):
        raise ValueError('Revocation operation identity/version conflict')
    state = transport.query('''SELECT active,
        (SELECT count(*) FROM portal_sessions WHERE student_id=?) AS sessions
        FROM portal_students WHERE id=?''', [command['student_id'], command['student_id']])
    if len(state) != 1 or state[0]['active'] != 0 or state[0]['sessions'] != 0:
        raise ValueError('Revocation acknowledgment failed verification')
    return True


def revoke(transport, command):
    if set(command) != set(FIELDS) or command['expected_version'] != 0 or command['version'] != 1:
        raise ValueError('Invalid revocation command')
    if recover(transport, command):
        return True
    # One INSERT + database triggers is atomic in D1; no client-side BEGIN.
    try:
        transport.query('INSERT INTO portal_student_revocations (' + ','.join(FIELDS) +
                        ') VALUES (' + ','.join('?' for _ in FIELDS) + ')', [command[k] for k in FIELDS])
    except Exception:
        # A lost response is not evidence of rollback. Resolve the same ID first.
        if recover(transport, command):
            return True
        raise
    if not recover(transport, command):
        raise RuntimeError('Revocation acknowledgment unavailable')
    return True


def process_operation(database, operation_id, transport, command):
    operation = database.get_archive_operation(operation_id)
    if not operation or command['operation_id'] != operation_id or command['student_id'] != operation['student_id']:
        raise ValueError('Local operation does not match cloud command')
    try:
        revoke(transport, command)
    except Exception:
        database.set_archive_cloud_result(operation_id, 'failed', 'Cloud outcome unverified. Retry the same operation ID.')
        raise
    database.set_archive_cloud_result(operation_id, 'verified')
