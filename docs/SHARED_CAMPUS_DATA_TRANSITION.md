# Shared campus data transition

Status: architecture preparation; not deployed or enabled. The owner requires
Windows and MacBook to work with the same students. Independent whole-database
snapshot publishing is not a supported implementation of that requirement.

## Confirmed starting point

The MacBook's intended campus database is `data/cbvms.db`, with five students,
82 violations, eight appeals and seven appeal evidence files. The production
student portal is a separate D1 projection. At the last live read, it contained
two different accounts, protocol 2, generation 3, and one pending cloud-only
password action. Source registration belongs to the existing Windows publisher.
Neither source ownership nor production records have been changed.

The earlier two-cycle isolated rehearsal proved additive student portal
publication, evidence verification and retention of cloud-only records under one
writer. It did not prove shared editing or independent Windows/MacBook safety.

## Authority and responsibilities

A single campus service owns shared non-biometric identity, credentials, academic
calendar/enrollment, cases, discipline, notifications, appeals and reporting facts.
Both desktop installations authenticate to that service and submit bounded domain
commands. Only that authority publishes the existing student-safe portal projection
and imports/acknowledges cloud actions. Windows can keep making updates through
that service; it must no longer independently replace shared records with a local
snapshot. MacBook uses the same controlled update path.

Student usernames are global permanent-identity mappings. Administrative roles,
student sessions and device transport credentials are distinct. A trusted device
connection alone does not authorize an admin/superadmin action. Actor identity is
server-derived; request bodies cannot choose an arbitrary authorized actor.

Face encodings, enrollment captures and recognition processing remain on campus
devices. Shared identity/enrollment metadata needs explicit links to device-local
capture records without requiring capture bytes at a cloud host. Existing
`enrollment_captures`/enrollment relationships must be preserved during that split;
blank captures or reconstructed biometric records are unacceptable shortcuts.

## Existing code that must change

`CBVMSDatabase` currently opens SQLite directly. Numerous desktop domain methods
and several UI/report/camera callers also issue SQL directly or resolve `db_path`.
Changing a connection string or adding a remote configuration cannot redirect
these callers safely.

- `auth/auth_manager.py`, `database/student_credentials.py`: shared staff/student
  authentication and persisted authorization; retain student username isolation,
  password setup, revision checks and session revocation.
- `database/db_manager.py`, `database/student_management.py`,
  `database/term_enrollment.py`: expose bounded domain transactions for shared
  records; allocate event IDs centrally; validate record versions before edits.
- `core/report_snapshot.py`, `core/report_csv.py`,
  `core/suspension_analytics.py`, `ui/reports_panel.py`: retrieve consistent shared
  report snapshots, preserving semester ownership and historical academic labels.
- `core/live_pipeline.py`, `core/attendance_writer.py`, enrollment UI: retain local
  camera/ML/capture work and submit occurrence-time commands on a background worker.
  Use durable device command IDs and backpressure; never run network or inference
  work on the CustomTkinter UI thread.
- Direct SQL UI callers in settings, account management, academic terms,
  enrollment, student records and violation review need named service operations.
  Do not expose arbitrary SQL or arbitrary Python method invocation over HTTP.
- `core/cloud_sync.py`, `main.py`: authority-only portal synchronization; other
  desktops do not run the old whole-snapshot publisher for the shared source.
- Existing Cloudflare API/PasswordCrypto: preserve portal authentication and
  student isolation. Portal snapshots remain a projection, not a campus recovery
  source or a replacement for missing local historical facts.

## Transaction and retry contract

Every mutation has a durable UUID command ID, authenticated device/user, bounded
payload, operation name, and expected entity version where applicable. The service
commits the domain mutation, audit and command receipt together in SQLite.
A repeated command with the same payload returns its original result. Reusing an
ID with a different payload is a conflict. Lost responses are resolved by receipt
lookup; clients do not replay with a fresh ID. Stale edits return a conflict and
current safe metadata, never timestamp-based silent overwrite.

Discipline, suspension rollover, enrollment revisions and deadline effects execute
once at the authority using existing domain rules. Occurrence/submission times
remain distinct from receipt time. Device observations retain their device event
identity and verified parent mapping. No numeric ID from an independent old
SQLite file establishes global case/term ownership.

Read responses are scoped to authenticated roles and owners, paginated where
needed, and exclude credentials/biometrics. Evidence endpoints verify parent
ownership and SHA-256. Request body limits, HTTPS, explicit origins, CSRF for
cookie sessions, transport authentication, rate limits and safe errors apply.
Remote mode must fail closed; it must not silently start writing an unrelated
local database when the service is offline. Any offline observation outbox needs
explicit pending status and durable retry semantics.

## Hosting prerequisite

The campus service location is not yet selected. A cloud Python host needs secure
access, durable storage and a non-biometric schema boundary. A Windows or MacBook
campus host needs to stay online while the other desktop submits updates. The
existing Cloudflare Worker/D1/R2 portal does not run the Python/SQLite campus
service as currently implemented. No new hosting resources have been created.

A pending owner question asks which existing host can run continuously. Do not
invent infrastructure credentials, billing commitments or an always-on host.

## Migration and acceptance

1. Preserve and inspect a consistent full Windows campus backup as well as the
   verified MacBook copy. The portal cannot prove which Windows-only campus facts
   exist. Compare permanent identities, all event relationships, academic term
   ownership, credential revisions and evidence; stop only for real conflicts.
2. Select the shared service host; implement and verify its authentication,
   transactions, client integration and local biometric boundary using isolated
   fixtures. Use two devices against one test authority and test racing edits,
   retries, disconnects, unauthorized roles, repeated observations and stale data.
3. Rehearse the reconciled full campus dataset and both clients with the existing
   portal projection and queued actions. Verify Reports history and evidence.
4. Coordinate publisher transition, take a fresh SQLite/D1/R2 recovery set,
   verify restoration and repeat the comparison. Retain previous backups.
5. Enable the shared service and controlled publisher transition, without copying
   writer IDs into independent databases or resetting generations/receipts.
   Verify actual publication twice, account/profile/credential mappings, original
   cloud-only records, queued action ownership, evidence, and a background cycle.
6. Verify actual student login securely when possible. Keep a separate statement
   for synchronized credentials versus an observed successful student login.

If a transition fails, preserve copies, receipts, generations and commands;
prefer forward repair. Do not restore a stale local SQLite file over advanced
shared/cloud data, erase a queue, or enable the unsafe old snapshot writers.

## Selected hosting and verified biometric constraint

The owner selected an **existing cloud server** and will provide secure access.
Provider/hostname/SSH alias and the authorized connection are still pending; this
MacBook currently has no `~/.ssh/config` aliases to identify that host. Do not
create or bill a replacement server while those details are unresolved.

Read-only schema inspection confirms `enrollment_captures.encoding` and `.photo`
are NOT NULL, and confirmed enrollment records reference capture IDs with
ownership triggers. Therefore a cloud copy cannot simply zero/delete capture
rows or strip BLOB columns while claiming that enrollment history is preserved.
A deliberate non-biometric shared-enrollment schema and device capture mapping
are required before cloud activation. This finding does not alter the five local
students or their existing history.
