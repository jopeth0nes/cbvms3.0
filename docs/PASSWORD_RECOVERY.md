# Student password recovery

Website, backend and independent SMTP relay **deployed and online on
27 October 2026** (Asia/Manila). The relay reuses this Mac’s existing SMTP settings. See `PASSWORD_RECOVERY_CHECKPOINT_2026-10-27.md` for verification
and exact rollout prerequisites. Existing dashboard edits are separate and preserved.

## Screens and interfaces

The hosted login links to `/?recovery=1`: username/student ID, neutral confirmation,
six-digit code with paste/autofill, a 60-second resend countdown, new password and
confirmation with show/hide controls, and return to sign-in. The signed-in Profile page can verify the unambiguous contact email already saved
on the student record using the full authenticated session and a code to that
address. Establishing a different address still requires the current password and
a code sent to the proposed address. Local web and native student login links open this
same hosted flow; they do not implement a second reset database.

| Endpoint | Authorization and purpose |
| --- | --- |
| POST `/api/recovery/request` | Same-origin JSON `{username}`; neutral message and opaque challenge |
| POST `/api/recovery/verify` | Same-origin `{challenge_id,code}`; reset-only five-minute grant |
| POST `/api/recovery/complete` | Same-origin `{grant,password,confirmation}`; single-use reset, no login session |
| GET `/api/recovery/email` | Full student session; only that student's verified address/status |
| POST `/api/recovery/email/request` | Full session, CSRF; `{use_registered_email:true}` verifies only the saved contact, or `{email,current_password}` verifies a different address |
| POST `/api/recovery/email/verify` | Full session, CSRF, `{challenge_id,code}`; binds address to session identity |
| POST `/api/relay/mail/heartbeat` | Mail-only Bearer key; SMTP readiness after TLS/login check |
| GET `/api/relay/mail/status` | Mail-only Bearer key; sanitized health and queue counts |
| POST `/api/relay/mail/claim` | Mail-only Bearer key; one encrypted-outbox handoff |
| POST `/api/relay/mail/receipt` | Mail-only Bearer key; idempotent outcome |
| POST `/api/sync/mail/claim` | Existing sync Bearer secret + registered source/writer/protocol; one SMTP handoff |
| POST `/api/sync/mail/receipt` | Same sync authorization; `{id,claim,status}` with accepted/failed/uncertain |

Unauthenticated requests return: “If this account is eligible for recovery, a code
will be sent to its registered email.” Account existence, email address, lifecycle
and account-specific request limits do not change this response. Bad syntax returns
400; network limits return 429; globally unavailable secret/SMTP heartbeat returns
503. Responses are no-store. Request bodies are streamed with an 8-KB bound.

## Recovery-email ownership

`portal_students.email` is NOT a recovery address. The existing publisher can fill
it with a SHA-256 identity plus `@cbvms.invalid`; contact data in `snapshot.profile`
is also not proof of email ownership. Migration 0009 does **no email backfill**.

`portal_recovery_emails` is separate, bound by permanent `student_id`, with unique
normalized address, verification timestamp and monotonically increasing version.
A full student session plus either current-password reauthentication (new address)
or an unambiguous existing contact binding (saved address) authorizes the email
challenge. In both cases only successful code verification marks the destination
verified. For saved-contact verification the server chooses the destination from
the student snapshot and rejects requester destination substitution. Migration
0010 stores the contact proof separately and invalidates it on contact changes;
duplicate contact identities fail closed before issuance and at verification. An address cannot
be verified for two identities. Starting an authenticated email change invalidates
existing challenges/grants. The prior address remains verified until replacement
succeeds; the version still advances to retire earlier recovery attempts.

Students without a verified accessible email must contact the campus administrator.
The administrator must establish identity using the campus's existing verification
procedure, review access restrictions, and use the existing audited account-reset
workflow. The student then signs in, completes any password setup, and verifies a
recovery address. Never accept an unauthenticated replacement destination, manually
mark a legacy email verified, or restore a recycled account as a password repair.

## Security and concurrency

- Cryptographic random six-digit code; ten-minute expiry; five verification attempts
  per challenge. Code digest is HMAC-SHA256 over purpose, opaque random ID and code.
- Worker-only `RECOVERY_HMAC_KEY`: independently generated high-entropy secret of
  at least 32 bytes (store an encoded value, never a real secret in tracked files).
- Challenge binds purpose, permanent identity, email version, credential version
  and current credential hash. Every verification and completion rechecks them,
  active status, recycled status and independent access restrictions in SQL.
- Rolling D1 limits: six requests/account/hour, 60-second account-and-purpose resend cooldown,
  ten verifications/account/hour across resends, ten completions/account/hour,
  five email-change reauth attempts/account/hour, 30 requests/network/hour and
  100 total recovery operations/network/hour. Atomic conditional inserts prevent
  concurrent Workers bypassing limits. Username and ID resolve to the same key;
  ambiguous aliases fail closed. Network uses Cloudflare's trusted connection IP.
- Replacement invalidates older same-purpose challenges. Credential/email/access
  changes also invalidate challenges via triggers. Ordinary sign-in limits are
  independent. Short response padding reduces lookup timing differences; SMTP
  delivery does not occur in the public HTTP response path.
- Successful verification creates a random 256-bit, five-minute, single-use grant;
  only its SHA-256 digest is stored. It cannot create a login session. D1 atomic
  batch uses a per-request consumption claim, so concurrent completions produce
  one password change and one durable event. Current PasswordCrypto derivation,
  PBKDF2-SHA256 v1/600000 iterations, and existing password policy are reused.
- Successful completion increments cloud credential revision, revokes all cloud
  sessions, invalidates challenges/grants, and queues a security notice. No password
  appears in mail or synchronization. No recovery secret is logged.

## Existing SMTP delivery

The Worker does not run Python SMTP. It queues the recipient and an AES-256-GCM
code envelope, using a domain-separated key derived from `RECOVERY_HMAC_KEY` and
message ID as authenticated data. The independent relay obtains the code over authenticated HTTPS using a
mail-only runtime key (`RECOVERY_RELAY_KEY`); the envelope is erased on claim.
Expired envelopes are erased on polling, and expired metadata is retained at most
an additional day before cleanup. Verification stores only the keyed code digest.

`core/recovery_mail.py` uses the existing Settings → Email configuration. SMTP
credentials remain on that desktop. Delivery requires STARTTLS with certificate
verification, SMTP authentication, a 15-second socket timeout, and fixed CBVMS
message content. Codes are never persisted locally. The independent per-user service polls every ten seconds and authenticates to
SMTP at least every 45 seconds before refreshing its cloud heartbeat. Failed SMTP
health clears availability and prevents claims. It does not load the campus
database, UI, camera or ML code, and it does not require cloud sync to be enabled.
Legacy registered-writer mail endpoints remain compatible for existing clients.

Claiming is **at most once**, not an exactly-once inbox guarantee. If the claim HTTP
response is lost, or SMTP acceptance is ambiguous, the same code is not resent
silently. A student requests a replacement after the cooldown; old codes are
invalidated. SMTP `accepted` means the server accepted the message, not inbox
receipt. Receipts are idempotent and report failed/uncertain outcomes without raw
provider errors. Code mail includes expiry, CBVMS identity, do-not-share and
ignore-if-unrequested text. The separate password-changed notice expires after
24 hours. No mass mail or live student tests have been performed.

A recent authenticated SMTP-health heartbeat (within two minutes) is required to
request codes. The independent relay checks TLS and SMTP authentication before
reporting ready. This proves connection/login, not inbox delivery. One diagnostic
email was explicitly authorized to the configured sender and accepted by Gmail;
actual inbox delivery has not been independently confirmed.
Offline relay or delayed mail can prevent recovery; the UI directs students to
retry or contact campus administration.

## Credential synchronization

The reset transaction creates the existing `portal_actions.kind='password'` event:
new hash, expected old hash and expected old credential revision. The verified
registered desktop consumes it through `core.cloud_sync.apply_action`, which
checks identity/old credentials and applies the hash, increments its revision,
revokes local sessions and stores an immutable replay receipt in one transaction.
The next protocol-v2 publication acknowledges it and clears pending state.

A stale desktop snapshot has an older revision and cannot overwrite recovery.
The rejected-password-receipt write also has a revision guard against a concurrent
newer cloud reset. An independently newer campus/admin reset is a conflict requiring
reconciliation, not permission to force the recovered hash onto every computer.

An account with a pending password action is not eligible for another reset until
that action reconciles. This preserves the deployed desktop protocol's one-step
credential revision semantics. Public responses remain neutral; the authenticated
ordinary password-change page already explains pending synchronization.

**Offline desktops retain their current hash/sessions until the verified event is
received.** The new dormant incremental-profile protocol does not carry credentials;
recovery uses the actual registered protocol-v2 action channel. Do not fabricate
source identities, enable dormant domain sync, or assume every offline device is
registered and synchronized.


## Owner-approved recovery without a student session

When a student has no usable session and no verified recovery address, the owner
may explicitly authorize a destination for that permanent student identity through
the authenticated provider-admin workflow. Migration 0011 adds immutable audited
approvals, valid for at most 30 minutes and bound to credential and email versions.
There is no public, student, relay-key or sync-key approval-creation endpoint.

Issuing approval sets only the explicitly authorized recovery destination to
**unverified**, advances its email version, invalidates prior challenges and revokes
old cloud sessions. It does not change the password, reactivate accounts, update
contact/profile data, or enroll unrelated legacy addresses. Access restrictions,
version fences and rolling limits still apply. The existing request/verify/reset
flow sends the code to this approved destination; successful code proof yields
only a reset grant. Completion verifies the address and consumes approval in the
same atomic batch as the password change and its durable sync event.

An issued code-entry link uses `/?recovery=1#challenge=<opaque-id>`. The identifier
is not a reset grant or code, and the fragment is not sent in HTTP requests. The
student must enter the emailed code and their own new password. Opening the link
does not send another message. An old open code screen must use the new link or
request a fresh code, rather than attempting to verify a superseded challenge.

## Installed relay and diagnostics

Root cause of the original offline message: the first mail loop was started only
by `start_cloud_sync`, which returned immediately on this Mac because
`data/cloud-sync.json` was missing. Thus there was no cloud heartbeat despite valid
SMTP credentials. The deployed website’s two-minute heartbeat guard worked as
written, but the required local runtime had never been connected.

The fix uses `core/recovery_relay.py` and the per-user macOS LaunchAgent
`com.cbvms.recovery-mail`. It starts at login and restarts on exit, independently
of the dashboard. Connection metadata and the mail-only token live in ignored
`data/recovery-relay.json` with mode 0600. SMTP credentials remain in the existing
Settings → Email configuration. No campus writer was registered/copied/enabled.

The key can authorize only heartbeat, mail claim, receipt and sanitized status.
It cannot access sync audit, student snapshots, credential publication or the
incremental device protocol. Existing recovery HMAC and SYNC_KEY secrets were
preserved. No additional schema migration was needed for this fix.

Read operational state without exposing a key:

```sh
.venv/bin/python scripts/recovery_mail_relay.py --status
```

Local sanitized state is `.runtime/recovery-relay/status.json`. States distinguish
`empty` (online, nothing queued), `accepted`, `smtp_unavailable`,
`relay_authorization_failed`, `cloud_unavailable` and configuration/connection errors.
Never paste connection files or SMTP credentials into chat or logs.

Service installation/reinstallation after securely provisioning a dedicated key:

```sh
.venv/bin/python scripts/install_recovery_relay.py --apply
```

To stop the service use `launchctl bootout gui/$(id -u)/com.cbvms.recovery-mail`;
the website will report offline within two minutes. Keep this Mac awake, online and
logged in for this desktop SMTP arrangement. For continuous availability the same
mail-only relay can run on an always-on campus/server machine with a securely
provisioned key and SMTP credentials. Do not copy student-data writer identities.

No recovery address was automatically verified. A student signs in, opens
**Profile → Recovery email**. A still-signed-in student who forgot the password
clicks **Verify my saved email**, confirms the code sent to the existing contact,
then clicks **Reset your forgotten password**. A new/different destination still
requires the current password. `/?recovery=1` works while signed in too. A student who already cannot sign in and has no verified
address needs identity-checked administrator recovery first.

## Migration and compatible rollout

0009 is additive, repeat-safe, and independent of dormant 0008. It requires the
already-deployed account, session, sync and lifecycle schemas through 0007. Tests
run directly from 0007 to 0009 and repeat 0009 on populated fixtures. Ordinary
login/password and existing sync routes do not require 0009 until recovery is used.

Before any production mutation:

1. Provision the independent mail-only relay key, install its service and verify
   existing SMTP TLS/login. This does not require ordinary campus publication.
   The registered desktop still needs the existing credential action protocol for
   password reconciliation; do not copy writer identities between computers.
2. Take a fresh restricted D1 export plus Worker source/assets/settings/deployment
   backup. Restore D1 in isolation and check integrity/FKs and account/lifecycle
   preservation. Existing older backups are not a coordinated release backup.
3. Apply only 0009 through the site's reviewed migration ledger process. Do not run
   a blanket migrations-apply that also enables unreviewed 0008. Record 0009 once;
   its CREATE IF NOT EXISTS statements tolerate interrupted/repeated application.
4. Configure `RECOVERY_HMAC_KEY` through Wrangler/Cloudflare runtime secrets using
   secure input. Do not rotate SYNC_KEY, expose SMTP credentials to browser assets,
   or backfill verified emails. Deploy the tested standalone Worker and static UI.
5. Confirm independent SMTP polling, then use an authorized synthetic account and
   recipient for email verification, reset, new login, session revocation and a
   desktop reconciliation/next publication. Record provider acceptance separately
   from observed inbox delivery. Test mobile/keyboard access.

Rollback to earlier Worker/assets is schema-compatible; leave additive tables and
accepted credential events intact. Never roll back credential rows or delete
receipts to undo a release. Rotating/removing the recovery secret invalidates
outstanding codes and queued encrypted envelopes; perform an explicit operational
recovery, not an unannounced rotation.
