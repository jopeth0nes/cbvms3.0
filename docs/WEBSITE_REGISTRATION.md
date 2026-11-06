Automatic registration on studentportal.bond
============================================

The desktop and local web portal start a background registration worker when
`data/registration-sync.json` contains the enabled connection. This private file
contains a limited registration/owned-record key and source UUID. It is ignored by Git.
Restart the application after connecting it. Keep the PC online and the application
open; queued registrations retry every 5 seconds. No network requests or face
inference run on the UI thread.

New student IDs and usernames create website accounts with the campus password
hash and first-login password-change requirement. Photos, face encodings, phone
numbers and evidence are excluded. Registration does not confirm academic
enrollment. Student Management shows website readiness after refreshing its list.
Credential emails describe whether the website account is ready, already exists,
or is awaiting publication.

An existing website identity or username is never replaced or claimed. Its password
and records remain unchanged; the new desktop temporary password will not sign in
to that existing website account. Use its existing website password or the website's
recovery flow. This also applies to the already-existing student in this session,
whose website password the owner explicitly chose to preserve.

For accounts created by this connection, recycling/restoring a student forwards the
versioned access state and revokes old website sessions. A source cannot change an
account created by a different source, and independent website access blocks are
preserved. Full deletion is blocked while a cloud connection is configured.

This connection publishes registration and managed account lifecycle only.
It does not publish subsequent violations, appeal decisions, profile changes,
campus password resets, or academic records. Full protocol-v2 record synchronization
still requires reconciliation of this PC's smaller roster with the hosted roster.
Existing campus-sync configuration, generation and account password changes are
left intact.

Release and recovery
--------------------

The production Worker contains recovery features newer than this checkout's
standalone source. Do not deploy the older standalone Worker over production.
The release uses a saved copy of the live module and an additive wrapper:
`cloudflare/registration-wrapper.ts`. The original module, settings, database
backups and deployment response are retained in ignored `.runtime/registration-release/`.
That directory contains sensitive backups; do not commit or share it.

`scripts/prepare_registration_release.mjs` compiles the new handler and wrapper.
`scripts/publish_registration_connection.py` defaults to read-only preflight;
`--apply` publishes content while preserving Worker settings/assets and refuses
to overwrite an existing connection or a concurrently changed Worker. `--enable`
recovers an interrupted verification step using the existing secret. Do not
generate a replacement key just because the client timed out.

Disable the local connection by setting `enabled` to false and restarting the app.
The additive ownership table can remain for recovery. Roll back code using the
saved exact live Worker module through the content API; preserve its settings and
bindings. No rollback should reset or delete student credentials.

Verification includes disposable Python databases and isolated D1 tests for
registration, retry, existing-password preservation, authentication, first-login
password setup, student isolation and access revocation on recycling.

## Automatic report updates

The registration background worker now also publishes student-safe record snapshots
for accounts this connection created (`registered`), every 5 seconds while the
desktop runs online. The hosted portal checks for updates every 5 seconds on its
dashboard, violations, notifications and appeals pages, except while a form is open.
Allow about 10 seconds plus network/processing time; this is polling, not an instant stream.
The browser quietly redraws changed records only, discards stale navigation replies,
prevents overlapping polls, pauses hidden tabs, and checks immediately on focus.
Open forms receive badge updates without replacing their contents. The desktop
skips unchanged record uploads (excluding countdown changes from comparison) with
a full publication heartbeat every 60 seconds. Camera inference rates are unchanged.

`core/registration_records.py` uses a private SQLite backup to read consistent
records away from the camera/UI thread. It sends published student-visible cases,
notifications and campus appeal decisions. Evidence is checksum-verified in private
R2 storage before a snapshot can reference it; acknowledged uploads are cached.
Credentials and enrollment face photos/embeddings are excluded. Each account has
an increasing publication sequence; stale updates and archived/foreign accounts
are rejected. Existing website passwords and unrelated accounts remain intact.

This connection imports website appeals for its owned active accounts before
publishing the latest records. It retrieves bounded, checksum-verified supporting
pictures and preserves the server-recorded submission time. The local appeal and
immutable replay receipt commit together; the website is acknowledged only after
that commit. A lost acknowledgement retries without creating duplicate appeals.
Exact student/account/lifecycle ownership and violation identity are checked again
inside the local transaction. Administrator decisions return in later record updates.
An `applied` transport receipt means received by campus, not approved by an admin.
Student violation cards include the saved category, administrator reason, reviewer
and decision time after approval or rejection. The release wrapper patches only
the deployed violation renderer using `portal-decision-display.ts`, preserving
existing account recovery code and assets. JavaScript responses bypass conditional
asset caching so updated cards are available after reloading the page.
The visible desktop inbox checks for updates every five seconds, leaving open case
reviews and drafts alone. Profile requests, password changes and other online
submissions still need the full reconciled campus sync connection described elsewhere.
Accounts marked `existing_account` require explicit
identity reconciliation before this scoped connection can publish their records.

Production release: compile using `node scripts/prepare_registration_release.mjs`,
run `node tests/registration-release.mjs` from `sites/student-portal`, then run
`scripts/publish_registration_records.py` for preflight and `--apply` to deploy.
The release tool backs up and restores/checks the D1 export, backs up campus data
and Worker content/settings, applies additive migration 0017, and preserves the
original deployed Worker module. Backups are under ignored `data/backups/record-sync-*`.
Rollback can restore the saved Worker multipart body/content type via its content
API; leave the additive cursor table in place. Restart the desktop after updating
its worker. Restoring an older campus database may require operator review of
publication sequences; do not reset cloud cursors to bypass stale-writer protection.
