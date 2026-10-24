# Student cloud login recovery checkpoint

Inspection date: 24 October 2026, Asia/Manila. **Production merge and login repair
remain blocked, not completed.** No production records, credentials, source
registration, deployment, or synchronization configuration were changed.
Existing camera work was preserved. No application initialization or ordinary
local portal reads were used to inspect campus data.

## Current findings

- The declared Sites project remains
  `appgprj_6ad63eb439e08191b42fdcd1035145c2`. Both `get_site` and
  `read_database_overview` returned `project_not_found` through the current
  connection. `list_sites(include_editable=true, limit=50)` returned no sites and
  no next cursor. This establishes an access/discovery blocker, not deletion of
  the project. No replacement resources were created.
- Both `https://studentportal.bond` and the documented
  `https://cbvms-student-portal.epicmaple8.chatgpt.site` redirected to `/portal`
  and returned HTTP 200 with CBVMS content. GET `/api/login` returned 405 with
  the expected method rejection; unauthenticated GET `/api/me` returned 401
  with the expected authentication rejection. No login POST was attempted.
  These observations do not prove that the origins share a database or version.
- Actual project owner/workspace, live version, D1 resource, R2 bucket, migrations,
  protocol and pending cloud actions remain unverified. `DB` and `BUCKET` are
  local manifest binding names, not verified provider resource identities.
- Current `data/cbvms.db` has five students/accounts, 82 violations, eight appeals,
  seven evidence files and zero cloud receipts. Source registration is absent.
  `data/cloud-sync.json` is absent. This checkout therefore cannot publish the
  local account through its configured synchronization worker. This is a confirmed
  local synchronization blocker, not conclusive evidence of the cloud login cause.
- The requested account exists exactly once locally, joins to the expected
  permanent student ID, is not registration-pending, has credential revision 2,
  and has completed first-login setup. Its hash format is accepted by the current
  cloud verifier. No actual password verification was performed. The name differs
  from the supplied expected name only after accent-insensitive comparison; no
  name or identity was changed and no private record values are reproduced here.
- The current original-evidence audit found zero missing originals with stored
  checksums and zero checksum mismatches. This does not verify cloud evidence.
- Source review confirms protocol 2, academic snapshot schema 3, atomic publication
  and acknowledgments, credential revision conflicts, source/writer fencing,
  server-owned submission times and session/CSRF protections. It does not establish
  that this source is deployed. Snapshot timestamps are exposed by the local API
  implementation; their actual authenticated display remains unverified.
- No relevant provider credential environment variables or standard AWS/Wrangler
  credential files were present at the inspected locations. Plugin discovery found
  no Cloudflare D1/R2 connector. Ruflo/ToolSearch tools were not exposed in this
  session; no substitute swarm was started.

## Local inventory and independent safety backups

Eight `data/cbvms.db` files were found under the local GitHub directory. Searches
also covered Desktop and Downloads. This is not an inventory of other computers,
external disks, or every possible database filename.

| Checkout (relative to Documents/GitHub) | Students | Violations |
| --- | ---: | ---: |
| `cbvms3.0` | 5 | 82 |
| `cbvms3.0 Oct5` | 5 | 81 |
| `cbvms3.0 stable` | 1 | 34 |
| `cbvms2.0` | 2 | 13 |
| `CAPSTONE/cbvmsm30` | 1 | 326 |
| `CAPSTONE/CBVMS-Capstone` | 1 | 0 |
| `CAPSTONE/CBVMS-Capstone copy` | 1 | 0 |
| `CAPSTONE copy/cbvms` | 1 | 0 |

All eight were backed up through SQLite's backup API, including committed WAL,
into the ignored, access-restricted directory:

`data/backups/cloud-login-audit-20261024T033730Z/`

`manifest.json` maps each source to its `campus-NN.db` backup and separate
`campus-NN-restore.db` restore. Directory permissions are 0700 and files were
created with umask 0077. All eight backups and eight restores passed SQLite
integrity and foreign-key checks. Each restore's logical dump matched its backup
using an in-memory checksum comparison; credentials and record contents were not
printed. Originals and older existing backups were preserved.

These are independent local safety copies, **not a coordinated cutover backup**.
Remote writers were not paused, cloud actions could not be inventoried, and D1/R2
backup and isolated restoration remain unavailable. Repeat the final backup and
comparison after verified writer quiescence and remote access.

## Comparison and unresolved decisions

- The October 5 copy shares all five permanent student IDs and their names with
  the current copy. The requested credential matches. Its 81 comparable event
  identities agree with current events under their existing integer IDs; current
  has one additional event. This is not proof all related fields match.
- `cbvms2.0` has one permanent student ID absent from the current database.
  Retain it pending provenance/identity verification; do not silently discard it
  or assume it is a legitimate addition based only on its ID.
- The stable copy's requested credential differs from current. It has no modern
  revision metadata sufficient to resolve authority automatically.
- Older CAPSTONE copies and `cbvms2.0` contain shared-ID name disagreements and
  older schemas. Their event/relationship reconciliation requires provenance;
  integer row IDs must not be treated as global identities. The 326-event copy
  cannot be dismissed as redundant from its smaller student count.
- The user has been asked to identify the owning Sites workspace and independently
  edited campus copies. No response was available when this checkpoint was saved.

Actual records added/updated/deleted: **0/0/0**. All local source records remain
untouched. Proposed local/cloud additions, updates, exact matches, cloud-only
retentions and conflicts cannot be counted without cloud access. No merge plan
has been approved by evidence, and no production apply was attempted.

## Verification completed

- `python -m unittest tests.test_cloud_sync tests.test_password_setup -q`:
  **45 tests passed** (25.686 seconds), synthetic temporary databases only.
- `npm test` in `sites/student-portal`: **passed** with isolated D1/R2 fixtures,
  covering source/protocol fences, stale/racing writers, authentication, CSRF,
  student isolation, queue paging, password resets, atomic acknowledgments,
  missing evidence repair and integer-ID collision rejection. The initial sandbox
  run could not bind localhost; the authorized retry passed.
- `npm run typecheck`: **passed**.
- Actual affected-student login, first-login behavior on production, remote record
  isolation, cloud-only preservation, queued-action reconciliation, evidence
  checksums and a second controlled production sync: **not verified**.

## Exact next step and recovery

Reconnect Sites to the existing project's owning account/workspace or grant the
current connection appropriate access. Re-run discovery and domain inspection;
establish actual project, live version and provider D1/R2 identities before using
any credentials. Obtain backup-capable provider access through secure connection
flows, never by pasting secrets into chat. Resolve which campus copies contain
independent authoritative changes and inspect cloud accounts/queue read-only.

Then prepare a field-specific comparison, pause/account for writers and actions,
take and restore-test a coordinated SQLite/D1/R2 recovery set, rehearse the
existing protocol twice in isolation, and apply only verified additive changes.
The user's request already authorizes routine configuration and additive matches;
ask only about real identity/source conflicts or consequential ambiguity.

No production rollback is needed for this session. Keep sync unconfigured until
the access, source and recovery gates pass. Never copy a backup over a running
database or restore local SQLite alone after cloud generations/actions advance.
For future recovery, preserve failed copies, receipts and generations, prefer
forward repair, and restore coordinated copies in isolation before resuming.
