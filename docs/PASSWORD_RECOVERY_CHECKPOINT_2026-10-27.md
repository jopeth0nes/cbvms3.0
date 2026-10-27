# Password recovery checkpoint — 27 October 2026

## Explicit owner-approved destination — 27 October 2026

The user clarified no student login/session is available and explicitly supplied a
replacement recovery mailbox for permanent student `2023-00117`. Added the audited
provider-admin assisted-recovery path, with no public approval issuer. The exact
owner-approved destination is stored only in the intended student's recovery
record; no automatic legacy-email enrollment occurred. It remains unverified until
successful emailed-code proof and reset completion.

Migration 0011 adds immutable 30-minute, credential/email-version-bound approvals
and their challenge bindings. Issuance validates active/unrestricted identity,
checks versions and destination uniqueness, advances recovery-email version,
invalidates prior challenges and revokes stale cloud sessions. It does not change
the password. Verification grants only password-reset permission; completion
atomically verifies the mailbox, consumes approval, changes credentials and queues
the existing durable password event/security notice. Existing rolling limits,
expired/recycled denial, session revocation and stale-writer fences remain in place.

Worker version `8fdf623f-9d26-4c79-a41a-97e2590b8128` deployed with issued-code links
using a URL fragment containing only the opaque challenge ID. There is no reset
grant or code in the link, and opening it sends no email. Fragment-only navigation
on an already-open recovery page is handled. This browser behavior initially
failed its test and was fixed before deployment.

Tests: isolated D1 recovery suite passed with approved-but-unverified destination,
wrong/right code, concurrent completion, mailbox verification only on completion,
expired approval, stale version denial, immutable audit row and no public issuer.
Existing full recovery/sync tests remained passing. Chrome mobile/keyboard/saved-
contact and issued-link tests passed, as did TypeScript, targeted ESLint and diff
checks. No real student's password was entered/read/changed by the agent.

Production: managed rollback bookmark/previous deployment and per-operation
receipts are under ignored `.runtime/assisted-recovery-2023-00117/`. Migration
applied, exact requested destination approved, and one normal rate-limited recovery
code request queued through the existing SMTP service. Its receipt subsequently
reported `accepted` (SMTP acceptance, not confirmed inbox delivery). No code was
read or logged.
The student's prior code screen referred to an invalidated challenge; they must
use the new issued-code link or request a fresh code. Delivery acceptance status
and the issued URL are in the private operation receipts; inbox delivery must be
confirmed by the mailbox owner. The approval lasts 30 minutes and code at most ten.

## Saved-contact verification — 27 October 2026

Deployed Worker `391c9725-6881-4176-8441-6c2cf2acbaeb`; live static assets
match the tested local file. SMTP relay remains online. The student still needs
to perform authenticated contact verification before a reset code can be sent.

Read-only account-specific diagnosis found student `2023-00117` active, unrestricted,
with no pending password action, **no verified recovery address**, and an existing
unambiguous contact record. Recent reset requests had `delivery=none`, no destination
and no outbox row. Thus SMTP was healthy but had no eligible message to send. No
address, code, token or password/hash was printed. The provider session expired
on the first read and was refreshed using the existing Wrangler session.

The user explained the student can still sign in but forgot the password. Added a
safe path for a **still-signed-in full student session**: Profile → Recovery email
→ Verify my saved email. It sends a verification code only to the unambiguous
existing contact chosen by the server; no current password required for that saved
address. Choosing a new/different destination still requires the current password.
The emailed code must be confirmed in the same student's authenticated session.
Nothing was automatically enrolled/verified or reset through provider access.

Additive migration 0010 stores saved-contact proof bindings and invalidates them
when the source contact changes. Duplicate/changed contacts and cross-student
verification fail closed. Migration applied with managed recovery bookmark and
prior Worker version recorded under ignored `.runtime/recovery-contact-release/`;
existing account/session/recovery-address counts were preserved. No migration 0008
or campus writer changes were made.

The UI supplies Verify my saved email and then Reset your forgotten password.
Direct `/?recovery=1` now opens recovery even with an existing session.
Email verification and reset use distinct 60-second resend cooldowns while keeping
the shared account request/attempt budgets. The backend test verifies the immediate
verification → reset transition without clearing cooldown state. Added a
No code received? disclosure with verification/admin guidance; neutral resend
messages now use a status element instead of the red error element. Generic public
messages still reveal no account or destination eligibility.

Verification: isolated `node tests/recovery.mjs` passed including no-password
saved-contact proof, substituted destination ignored, changed/duplicate contact,
missing/other-student session denial, and eligibility for reset only after successful
proof. Existing recovery/race/sync fixture still passed. TypeScript, targeted ESLint
and diff checks passed. Chrome fixture passed desktop/mobile keyboard recovery and
new signed-in saved-contact → verification → reset navigation. Real student's
browser could not be operated because Computer Use permissions are not granted.
The student must confirm their own code and enter their own new password; neither
was read, fabricated, or submitted by the agent. If no valid session remains,
identity-checked administrator recovery is still required.

## SMTP offline fix — 27 October 2026, Asia/Manila

**The independent SMTP recovery relay is installed, running and online.** Deployed
Worker version `84a8ae5c-de85-4025-974a-aef1aed16a0b`. This supersedes earlier
notes below that the relay was unavailable.

Root cause: the first implementation gated the mail thread on the existence of
`data/cloud-sync.json` and `enabled=true`. This Mac had working existing Gmail SMTP
settings but no campus publication configuration; therefore no relay thread or
cloud heartbeat ever existed. Read-only diagnosis confirmed zero heartbeats and
zero verified recovery addresses. Existing Gmail TLS and SMTP authentication passed.

Fix:

- `core/recovery_relay.py` and `scripts/recovery_mail_relay.py` provide a standalone
  relay with no student database, UI/ML or snapshot-publication dependency.
- Provisioned `RECOVERY_RELAY_KEY` in Worker runtime secrets and a restricted
  mode-0600 `data/recovery-relay.json`. SMTP credentials remain in the existing
  Settings → Email configuration; no new provider, no SMTP secret copied to cloud.
- Mail-only `/api/relay/mail/{heartbeat,status,claim,receipt}` rejects absent/wrong
  credentials. The relay key cannot use `/api/sync/audit`, publication or the
  device/staff incremental protocol. Existing HMAC and SYNC_KEY were preserved.
- SMTP TLS/login is checked at least every 45 seconds before heartbeat renewal.
  Claiming does not independently refresh health. Failed SMTP health prevents
  claiming; send failures/uncertain outcomes mark the relay unavailable.
- Installed user LaunchAgent `com.cbvms.recovery-mail` with RunAtLoad/KeepAlive;
  polls every ten seconds independently of the dashboard. Sanitized status is in
  `.runtime/recovery-relay/status.json`. Service remains dependent on this Mac being
  logged in, awake, online, and able to authenticate to Gmail.
- No new migration, no campus writer registration/copy/publication, and no account
  credential or lifecycle changes occurred. Managed recovery bookmark and prior
  deployment metadata recorded privately under `.runtime/recovery-relay/`.

Observed verification:

- `.venv/bin/python -m unittest tests.test_recovery_relay tests.test_recovery_mail -q`:
  **19 passed**. Covers mail-only route allowlist, HTTPS/no-redirect client,
  config permissions/symlink refusal, SMTP health without sending, failed health
  prevents claim, failure redaction, standalone startup with no cloud-sync config,
  bounded mail delivery and ambiguous send safety.
- `node tests/recovery.mjs`: **passed**, real isolated Worker/D1/PasswordCrypto
  with dedicated-key isolation, readiness true/false, receipt replay, the complete
  synthetic password-reset flow and actual Python desktop reconciliation fixture.
- TypeScript no-emit, Worker routing regression, Python compilation, diff whitespace:
  passed. UI was unchanged by this fix, so existing browser acceptance was reused.
- With the user's explicit approval, sent **one diagnostic email to the configured
  sender address** using existing SMTP. Gmail accepted it. Inbox delivery was not
  independently observed. It contained no student data, password or recovery code.
- First production cycle was blocked by auto-review because it might send queued
  authentication email. Read-only checks then proved queue/challenges/verified
  addresses all zero. The subsequent empty health cycle and service installation
  were approved. No workaround bypassed approval; no outstanding blocked action.
- Production `--status` returns `online:true`; a later check confirmed the heartbeat
  renewed beyond initial startup. Launchd reports the service running, no exit.
- A public live recovery request using an invented diagnostic username returned
  **HTTP 200** and the mandated neutral message; no real student was used or emailed.

No recovery address was automatically verified. Students must sign in, open
Profile → Recovery email, and prove address ownership. Students already locked out
without a verified address need the existing identity-checked administrator reset
first. The previously identified recycled account was not restored.

Read health without revealing credentials:
`.venv/bin/python scripts/recovery_mail_relay.py --status`.

## Deployment update — 27 October 2026, 11:44 AM Manila

**Website and recovery backend deployed** to `studentportal.bond` and the existing
migration domain. Worker version: `78d27edc-cd42-4f87-b453-0a5cf41a2abd`.
Wrangler completed successfully. Live `/`, `/app.js`, `/style.css` return 200 and
match local tested files byte-for-byte. Unauthenticated `/api/me` and
`/api/recovery/email` return 401; GET recovery request returns 405.

Migration 0009 was applied and recorded; existing student/account/session/action/
recycled counts were unchanged. Dormant 0008 was not applied. The recovery HMAC
secret was generated directly into runtime secrets; no value was logged or saved.

The rejected full local export was NOT retried. A safer alternative recorded
Cloudflare's managed Time Travel bookmark and prior Worker deployment metadata,
without downloading student records/hashes. Managed rollback metadata and release
receipts are in ignored `.runtime/password-recovery-release/`. No destructive
restore was rehearsed on production. The prior version was
`bfd906ea-1f18-4642-a7be-09c8ec227b7a`; leave additive schema and credential events
intact if rolling back the Worker. Time Travel retention is provider-plan bounded.

**SMTP activation remains incomplete:** the registered campus desktop must run the
updated mail poller with its existing SMTP and sync configuration. This Mac still
has no cloud-sync config; no writer identity was copied. No real email, password
reset, contact-email auto-verification, or account restoration was performed.
The screenshot account remains recycled. The historical pre-deployment notes
below describe the earlier state, superseded by this update.

## Status and preserved work

Implementation is local and uncommitted. **No production migration, secret change,
Worker deployment, password reset, account restoration, or real email occurred.**
No uncertain production write needs replay. The previous admin/superadmin sidebar
fix (`ui/dashboard.py`, `tests/test_dashboard_sidebar.py`) remains intact and is
separate from this work. Existing Reports, camera, semester, Recycle Bin and dormant
incremental-profile work was preserved.

Read `PASSWORD_RECOVERY.md` for screens, endpoint contracts, security decisions,
SMTP transport, sync behavior and rollout. The user's latest instruction chooses
existing desktop SMTP; no Resend account, subscription or new provider was created.

## Implementation

- Hosted recovery UI plus local web/native links; authenticated profile email
  verification. One configured `crack_builder` owned UI and SMTP implementation;
  lead retained backend/security/sync decisions and reviewed its changes.
- `lib/password-recovery.ts`, `lib/recovery-mail.ts`, additive/repeat-safe 0009:
  purpose-bound codes, HMAC, persistent rolling budgets/cooldown, atomic one-use
  grants/reset, version fencing, session revocation, encrypted SMTP outbox.
- Existing strong PasswordCrypto/hash/policy reused; only helper exports were
  changed in student-auth. Recovery uses the existing durable password action
  protocol. Older pending credential changes must reconcile before recovery.
- `core/recovery_mail.py` plus independent mail poller in `core/cloud_sync.py`:
  existing local SMTP, mandatory STARTTLS, bounded timeout, no plaintext-code
  persistence/logging; accepted/failed/uncertain receipts; at-most-once handoff.
- Desktop password ingestion was not replaced. A synthetic actual Worker event
  was passed into Python with a disposable offline desktop, then applied twice:
  one credential revision and receipt, local sessions revoked.

## Read-only production findings

The existing Cloudflare provider session was recovered through the repository's
`.runtime/cloud-config` location. Secret-name-only inspection found `SYNC_KEY`;
`RECOVERY_HMAC_KEY` is not installed. Production migration ledger ends at 0007.
The registered protocol-v2 campus writer exists. This Mac has SMTP configuration
fields populated but **no `data/cloud-sync.json`**; no SMTP connection or sender
verification was attempted. Do not copy the registered writer identity here or
turn on publication merely to obtain mail polling. Registered Windows client
update/SMTP availability remains unverified.

The account in the user's screenshot exists once in production, has completed
first-password setup and has no pending password action. It is inactive because
its lifecycle record is **recycled=1, access_blocked=0**. This was inspected through
authorized provider queries; no login was attempted and no password/hash/address
was output. Account restoration requires the existing authorized superadmin
workflow and is separate from password recovery. Never restore it automatically.

## Verification

- `node tests/recovery.mjs`: isolated real Miniflare D1/PasswordCrypto, production
  schema through 0007 directly to 0009, repeat migration with populated fixtures,
  no legacy email enrollment, SMTP outbox/authorization/receipt replay, email
  proof/conflict/isolation, unknown/missing/unverified/recycled/disabled neutrality,
  code expiry/exhaustion/resend and rolling/network limits, atomic concurrent
  verification/reset, grant expiry/replay, credential/email/access invalidation,
  ordinary password change, cloud session revocation, fresh login, stale publication
  denial and acknowledged credential reconciliation. Invokes the actual Python
  desktop ingestion fixture as described above. No real email.
- `node tests/sync.mjs`, `node tests/archive.mjs`, `node tests/incremental.mjs`,
  `node cloudflare/worker.test.mjs`: passed; includes existing real Python-to-
  Miniflare two-desktop incremental fixture and lifecycle protections.
- Worker evidence: SMTP and existing cloud-sync tests **40 passed** before three
  added SMTP cases; final SMTP-only **10 passed** including security notice content,
  24-hour notification/10-minute code expiry, connection/TLS failures and ambiguous
  send timeout without automatic retransmission. Existing cloud-sync tests were
  unchanged by the latter corrections.
- Lead: password-setup + SMTP tests **24 passed** in the initial combined run;
  **25 web/password-setup HTTP tests passed** after rerunning with loopback access.
  The initial sandbox-only run failed those 25 on bind permission; this was not an
  application failure. Native password-setup/login **11 passed** with isolated Tk.
- Builder browser test `tests/recovery-ui.mjs`: installed Chrome + cached Playwright,
  **one test passed**, mocked API fixtures, desktop 1440×900/mobile 390×844,
  Enter-key flow, actual clipboard paste, countdown, field bounds, show/hide,
  confirmation mismatch, back-to-login, HTML 503 error and no horizontal overflow.
  It found/fixed mixed-code paste truncation. Browser API is mocked; real backend
  verification is the separate Worker suite. No physical phone or live inbox claim.
- TypeScript no-emit, JavaScript syntax/targeted ESLint, Python compilation and diff
  whitespace checks passed. Final recovery/desktop fixture, TypeScript no-emit and production build passed
  after the final email-delete invalidation trigger and UI refinements. The final
  password-setup + SMTP rerun also passed all 24 tests. No physical-camera tests were
  needed for this feature, and no camera performance claim is made.
- Standalone `wrangler deploy --dry-run --config cloudflare/wrangler.jsonc`
  completed with exit 0: bundled Worker and seven static assets; no upload or
  production change. Sandbox denied only its optional external debug-log write.

## Backup approval blocker and next action

Automatic approval review **rejected** the proposed full production D1 and Worker
backup to `data/backups/password-recovery-<UTC timestamp>/`. Its reason: copying
student data and credential hashes to local files requires explicit authorization
of that payload/destination. The tool call was rejected before execution; no export,
backup artifact, rehearsal or production change occurred from that attempt. Do not
retry through another tool or route without approval.

Request explicit approval to export the production D1 database (including student
records and credential hashes) and Worker metadata/source into that ignored,
restricted local backup directory. Explain the data sensitivity and automatic
review block. This is separate from the missing registered SMTP-client access.

Once approved and the registered desktop/sender is available: take and restore-test
a fresh restricted backup, rehearse 0009 twice preserving every preexisting table,
install the strong HMAC secret securely, apply/record only 0009 (not dormant 0008),
deploy tested Worker/assets, run the upgraded registered SMTP poller, and verify
using an explicitly authorized synthetic account/recipient. No real-student email
or password tests without explicit instruction. Verify the next desktop credential
receipt/publication and distinguish SMTP acceptance from actual inbox receipt.

If production cannot be activated, leave recovery unavailable with the safe service
message and preserve these files. Do not claim the production Forgot password link
is live, the SMTP relay is connected, or the screenshot's account is restored.
