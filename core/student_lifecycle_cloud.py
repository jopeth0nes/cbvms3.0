"""Versioned provider-admin delivery from the trusted campus superadmin outbox."""
import hashlib
import uuid
from core.portal_import import _digest

FIELDS=('operation_id','student_id','import_id','source_checkpoint','payload_sha256',
        'expected_username','expected_version','version','operation_type','authority_id','reason')

def authority_id(source_checkpoint, actor_id, actor):
    return hashlib.sha256(f'{source_checkpoint}\0{actor_id}\0{actor}'.encode()).hexdigest()

def prepare_command(operation, bundle):
    if bundle.get('payload_sha256') != _digest({k:v for k,v in bundle.items() if k!='payload_sha256'}):
        raise ValueError('Import bundle integrity failed')
    if operation['actor_role']!='superadmin':
        raise PermissionError('Only authenticated superadmin operations may be delivered')
    sid=operation['student_id']
    account=next((a for a in bundle['accounts'] if a['student_id']==sid),None)
    if not account or sid not in bundle['mapping']: raise ValueError('Verified import ownership required')
    if operation['operation_type'] not in ('recycle','restore') or operation['lifecycle_version']!=operation['expected_version']+1:
        raise ValueError('Invalid lifecycle transition')
    return dict(zip(FIELDS,(str(uuid.UUID(operation['operation_id'])),sid,bundle['import_id'],
        bundle['source_checkpoint'],bundle['payload_sha256'],account['username'],
        operation['expected_version'],operation['lifecycle_version'],operation['operation_type'],
        authority_id(bundle['source_checkpoint'],operation['actor_id'],operation['actor']),operation['reason'])))

def recover(transport, command):
    rows=transport.query('SELECT * FROM portal_lifecycle_operations WHERE operation_id=?',[command['operation_id']])
    if not rows: return False
    if len(rows)!=1 or any(rows[0][k]!=command[k] for k in FIELDS):
        raise ValueError('Operation UUID payload conflict')
    state=transport.query('''SELECT l.*,s.active FROM portal_student_lifecycle l
        JOIN portal_students s ON s.id=l.student_id WHERE l.student_id=?''',[command['student_id']])
    if len(state)!=1 or state[0]['version']<command['version']:
        raise ValueError('Lifecycle receipt/state conflict')
    current=state[0]
    if current['active'] != int(not current['recycled'] and not current['access_blocked']):
        raise ValueError('Lifecycle access state requires repair')
    # Older successful operations acknowledge their own receipt, never reapply.
    return True

def deliver(transport,command):
    if set(command)!=set(FIELDS): raise ValueError('Invalid command')
    if recover(transport,command): return True
    try:
        transport.query('INSERT INTO portal_lifecycle_operations('+','.join(FIELDS)+') VALUES('+','.join('?' for _ in FIELDS)+')',[command[k] for k in FIELDS])
    except Exception:
        if recover(transport,command): return True
        raise
    if not recover(transport,command): raise RuntimeError('Lifecycle outcome unverified')
    return True

def dispatch_pending(database,transport,bundle,limit=100):
    """Drain durable intents in order; independent students may progress on failure."""
    pending=database.list_pending_lifecycle_operations(limit=limit)
    failed_students=set();results={'verified':0,'failed':0,'deferred':0}
    for item in sorted(pending,key=lambda r:(r['student_id'],r['lifecycle_version'])):
        if item['student_id'] in failed_students:
            results['deferred']+=1;continue
        try:
            # Revalidate trusted local audit and current role for every delivery.
            operation=database.get_lifecycle_operation_for_provider(item['operation_id'])
            if not operation: raise PermissionError('Operation authority unavailable')
            command=prepare_command(operation,bundle)
            deliver(transport,command)
            database.acknowledge_lifecycle(item['operation_id'],'verified','')
            results['verified']+=1
        except Exception:
            database.acknowledge_lifecycle(item['operation_id'],'failed','Cloud outcome unverified; retry retains the same operation ID.')
            failed_students.add(item['student_id']);results['failed']+=1
    return results
