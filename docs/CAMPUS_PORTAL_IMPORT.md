# One-time campus portal import — 24 October 2026

The authorized MacBook import completed through authenticated Cloudflare provider
administration. This is an additive data migration, not a campus sync publication
or a multi-master desktop database migration.

## Result

The intended source is this checkout's `data/cbvms.db`. Five student accounts and
student-safe histories were added to the existing D1 database at
`studentportal.bond`, preserving the two cloud-only accounts. The import contains
58 student-owned violations, eight appeals, 58 original evidence objects and seven
supporting appeal evidence objects. All 65 objects were read back and SHA-256
verified twice. Three existing legacy credential hashes and two PBKDF2 credential
hashes were preserved; no password was reset, rehashed or weakened by the import.

Username `2023-00883` now maps to the same permanent identity and credential
revision/hash as the MacBook. Its snapshot includes 45 violations and three
appeals. Actual successful password login is pending the owner's secure website
check; credential synchronization and observed login are distinct evidence.

Twenty-four local violations have no matching current student. They remain in
the unchanged full local database and backups. No identity was invented to make
those records visible to an unrelated account. Staff accounts, face encodings,
profile/enrollment captures and other local-only campus data were not uploaded.

## Guarded application

`core/portal_import.py` and `scripts/prepare_campus_portal_import.py` prepare a new
restricted bundle from a campus backup and cloud restore. They never initialize
or migrate the live campus database. They refuse overlapping identities/usernames,
invalid credentials, broken case relationships and evidence ownership/checksums.
Original academic IDs/snapshots remain student-owned and unchanged.

The import uses a durable map between original campus case IDs and unique negative
portal keys. The website displays original campus numbers. Owned appeal commands
carry server-selected original case IDs and the portal key separately, preserving
identity checks and pending-action/term-filter behavior. Windows numeric case IDs
are not silently treated as globally unique.

Additive migration `0005_campus_portal_imports.sql` records immutable import
receipts. The prepared D1 batch guards the exact original writer control,
publication ledger, student/account/action/preference rows, then inserts only the
receipt and missing student/account rows atomically. It has no UPDATE, DELETE,
REPLACE or absence-based deactivation. Evidence uploads are private, immutable
and checksum-addressed. A conflicting checkpoint aborts all D1 additions.

The completed operation preserved the registered Windows source/writer,
publication generation 3, the publication ledger and published_student_count 1.
No cloud actions were imported, rejected or acknowledged. Existing pending
password changes remain unchanged. The separately authorized provider import
uses the provider OAuth session; it does not impersonate the campus writer or
require copying its source/writer/SYNC_KEY into this MacBook.

## Recovery evidence

Restricted recovery directory:
`data/backups/portal-import-20261024T150535619985Z/`

It includes the consistent SQLite backup and separate restore, D1 SQL export and
isolated restore, R2 pre-inventory (empty), deployed Worker source/settings/version
and domain backups, prepared immutable evidence bytes/bundle/mapping, real-data
rehearsal result, schema verification, production verification, retry state and
code/checksum verification records. Directory mode is 0700; private files are 0600.
The exact import ID/payload fingerprint and operation phase are in
`operation-state.json` and the immutable D1 receipt. Provider orchestration
scripts are private ignored files under `.runtime/`; do not publish credentials
or copy sensitive bundles into source control.

If interrupted, read the import receipt before retry. A matching receipt is an
already-completed import: verify the existing rows and objects, then return a
no-op. Never clear the receipt or modify source generations to force another run.
A changed payload or partially unexpected state requires review, not upserts.

No automatic production rollback deletes imported students or objects. Preserve
current accounts, actions, sessions and evidence before recovery; restore backups
in isolation, reconcile everything accepted after backup, and prefer forward
repair. Restoring only an old campus file or D1 export can discard newer actions.

## Verification and remaining work

Importer/legacy-transfer tests: 10 passed; related sync regression: 33 passed.
Portal typecheck, isolated D1/R2 HTTP integration, standalone Worker routing and
real restored-data rehearsal passed. The duplicate D1 batch rolled back in the
rehearsal. Production retry verified the immutable receipt and a no-op with zero
duplicates. Live root/app.js returned 200, unauthenticated /api/me returned 401,
and the served app.js matched the tested file. PasswordCrypto source and namespace,
SYNC_KEY secret binding, D1/R2 bindings and domain mappings were preserved. R2
managed public access is disabled and has no custom domains.

This operation enables the imported accounts/history on the portal. It does not
enable background synchronization from the MacBook or shared editing of the same
campus records by both desktops. `data/cloud-sync.json` is still absent and the
MacBook is not registered as a publisher. Later desktop updates require the shared
authority/client transition in `SHARED_CAMPUS_DATA_TRANSITION.md`; do not re-enable
independent whole-database writers with copied source identities.

## Recycle Bin deployment — 25 October 2026

Additive migrations 0006 and 0007 are deployed. The source-bound superadmin
provider dispatcher is installed; it awaits normal updated-desktop startup for the
local lifecycle schema. This is a bounded lifecycle delivery channel, not general
MacBook synchronization. No production student was recycled/restored to test it.
The import receipt, negative case mappings and all original domain rows matched
the verified backup after deployment. Use [STUDENT_RECYCLE_BIN.md](STUDENT_RECYCLE_BIN.md)
for versioned delivery, state-aware verification and recovery. Never recreate an
account merely because its current access bit differs from the import snapshot.
