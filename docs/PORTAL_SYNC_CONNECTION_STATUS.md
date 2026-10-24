# Student portal connection — 24 October 2026

> Free-plan password checks are now handled by an internal SQLite-backed Durable
> Object. The deployed password check returns the expected JSON 401 for invalid
> credentials, resolving production error 1102 without changing billing or
> weakening the password format. Actual student password login remains for the
> student to verify. See the deployment runbook for evidence.

> Latest state: direct Cloudflare migration is working at
> `https://studentportal.bond`. Both the preserved cloud account and
> the campus account are active. Two campus sync publications passed with zero
> credential/profile conflicts. The owner completed the new domain attachment;
> the old Sites attachment was removed. Authenticated audit and a third campus
> publication passed at the main domain. Desktop sync now uses that origin.
> The sections below record discovery
> and the initial blockers; see `CLOUDFLARE_PORTAL_DEPLOYMENT.md` for current
> resources, backups, verification and operational limits.

Live read-only inspection confirmed that `https://studentportal.bond` is accessible
through the connected Sites account. The local campus database contains one
student and one login account; the cloud contains one different student and one
different login account. Preserve both identities. The cloud action inventory
contains one applied password action and no queued actions at inspection time.
No student identifiers, passwords, hashes or session tokens are included here.

The local source is unregistered and `data/cloud-sync.json` is absent. Live D1
has the six legacy portal tables, without the protocol-v2 control/publication
tables. Its account columns lack `credential_version`. The existing runtime
`SYNC_KEY` is secret and cannot be retrieved through Sites. Consequently, simply
enabling the desktop worker cannot connect these deployments.

A SQLite backup was created at
`data/backups/pre-portal-sync-20261024T052638Z.db`; its integrity and foreign-key
restore checks passed. The campus database was opened read-only. No source
registration, production migration, deployment, credential rotation or upload
was performed.

## Remaining cutover

1. Confirm which campus database owns future registrations and inventory other
   writers. Preserve the existing cloud-only identity.
2. Obtain provider access to the actual D1/R2 resources. Export and restore-check
   cloud backups and inventory evidence, following `CLOUD_SYNC_RECONCILIATION.md`.
3. Verify migration history, apply missing additive migrations, and publish the
   tested protocol-v2 portal. Rotate the sync key with the old workers stopped.
4. Register matching campus source/writer identities on both sides and configure
   the desktop using protected credentials. Reconcile a fresh audit before upload.
5. Publish once; verify the registered campus account, retained cloud account,
   evidence and submission queue. Then enable the background worker and verify a
   subsequent synthetic registration in isolation.

Sites tools available in this session can read live tables and manage deployment
and runtime secrets, but cannot export/migrate D1 or back up R2. Provider access
is required to complete the cutover. Do not bypass source fencing or send student
passwords through chat.

## Cloudflare authorization follow-up

Wrangler OAuth authorization succeeded on 24 October 2026. Read-only provider
inspection returned an empty D1 database list for the accessible account. R2
bucket listing returned provider error 10042: enable R2 in the dashboard. This
authorization therefore does not establish access to the existing Sites-managed
database or bucket. No provider resources were created or migrated.

Migrating to this account requires provisioning a separate D1/R2 deployment,
verifying backups of the Sites data through a supported export path, adapting
the desktop's direct SQLite operations to authenticated cloud APIs where cloud
authority is required, and validating the cutover before changing the public
portal. Keep biometric data local unless a separate storage decision is made.
Enabling R2 may require provider billing setup; the account owner must complete
that step in Cloudflare. OAuth credentials are excluded from Git under
`.runtime/cloud-config/`.
