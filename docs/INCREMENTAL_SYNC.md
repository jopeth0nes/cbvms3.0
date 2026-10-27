# Incremental campus synchronization

Implementation in progress, 27 October 2026. See
`INCREMENTAL_SYNC_CHECKPOINT_2026-10-27.md` for exact recovery state. This document defines the staged
contract; it does not claim enabled production synchronization.

## Ownership and identity

D1 is the intended authority for shared non-biometric records. Devices keep local
caches and durable commands. Permanent student IDs are reused only after verified
reconciliation. Independent integer event IDs are never global identities. Existing
negative portal case IDs and immutable import mappings remain unchanged. Recognition
embeddings, photographs and enrollment captures stay local; a downloaded student
identity is not a usable recognition enrollment.

Device transport credentials and authenticated staff authority are separate.
Server-provisioned principals, sessions and explicit student grants determine access;
client role strings confer no authority. Provider OAuth stays in administrative
provisioning/recovery tools. The initial command is a bounded name/email profile
patch. Credentials, discipline, enrollment, evidence, and lifecycle are not generic
JSON patches and require their own domain contracts before activation.

## Transactions and conflicts

Business writes and outbox insertion must share the caller's SQLite transaction.
Commands retain their UUID, device identity, entity, expected version, payload digest
and dependency across all retries. A receipt is acknowledged only after exact
identity/digest/version verification. Server command receipt, accepted mutation and
change journal are one transaction. A stale expected version produces a durable
conflict containing both values; no device-clock ordering or automatic rebase.
Conflicts block only the affected entity/dependants. Inbound changes and cursor
progression commit together and must not generate new upload commands.

## Migration order and compatibility

1. Inspect live local/cloud/Windows state; back up and restore in isolation.
2. Add dormant local queue and cloud authority/receipt/journal schema; verify using
   isolated fixtures. No device is automatically enrolled or student adopted.
3. Reconcile verified identities and imported case maps. Seed authority versions
   from a fresh baseline, preserving newer cloud submissions and independent blocks.
4. Add each domain operation and its desktop integration, permission checks and
   conflict controls. Registration/captures require an intentional metadata model.
5. Rehearse twice, including lost acknowledgments and two-device conflicts.
6. Deploy compatible support; enroll scoped application credentials securely.
   Fence incompatible legacy writes only for explicitly adopted records and only
   when the replacement preserves their full workflows. Keep Windows operational
   until its compatible client is installed and its unprojected history reconciled.
7. Verify actual receipts, inbound cursor, evidence and a later background cycle.

The provider-admin Recycle Bin dispatcher remains the lifecycle authority during
this transition. Its queue/receipts are not migrated or disabled. General patches
must never alter lifecycle flags, independent blocks, credentials or case maps.
The restored-snapshot trigger must only be relaxed for a validated, version-checked
operation; broad removal is unsafe.

D1 `batch()` rolls back the sequence on statement failure. Mutating predicates and
receipt insertion must be guarded in SQL, rather than relying on an earlier HTTP
read. Batches and bodies stay small (initial maximum 10 operations, 64 KiB), below
D1's 100 parameter/statement and 2 MB row limits. References:
https://developers.cloudflare.com/d1/worker-api/d1-database/
https://developers.cloudflare.com/d1/platform/limits/

## Recovery

Never reset receipts/cursors/generations or restore an old database over accepted
commands. Preserve both queues and server journal, restore backups in isolation,
then replay immutable operation IDs and reconcile post-backup accepted changes.
Disable a failing new client without enabling a stale snapshot publisher for
adopted records. Keep PasswordCrypto and the existing lifecycle service intact.
