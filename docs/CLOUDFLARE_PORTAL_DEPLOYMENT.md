# Deploying the student portal to Cloudflare

> Resolved on the free plan: password derivation now runs in the internal
> `PasswordCrypto` SQLite-backed Durable Object. It keeps the existing
> 600,000-round password format and does not persist or log passwords or derived
> keys. The regular Worker awaits the internal operation rather than spending
> its own CPU allowance on derivation. Isolated integration tests cover a
> real-format PBKDF2 fixture, login and password changes through that binding.
> A production invalid-login request now returns JSON 401 after completing the
> 600,000-round dummy check, rather than provider error 1102. No billing upgrade
> or password reset was performed. Actual student password login remains for
> the student to verify.

> Historical production login blocker discovered after cutover: a password-check request
> returns Cloudflare error 1102 under Workers Free. The 600,000-round PBKDF2
> implementation exceeds that plan's CPU budget. Attempting a 5,000 ms limit
> was rejected with provider code 100328 (CPU limits unsupported on Free).
> A native PBKDF2 trial also failed in production and was reverted; the original
> compatible password implementation remains deployed. No passwords were reset
> or weakened. The frontend now handles non-JSON hosting errors clearly.
> At that point, a compatible authentication host was required. The free Durable
> Object solution above supersedes the paid-plan proposal. Isolated integration
> tests do not reproduce the provider's production CPU/crypto limits.

> Cutover completed on 24 October 2026. The owner attached `studentportal.bond`
> to the new Worker; the old Sites domain attachment was then removed to release
> its routing claim. Main-domain authenticated sync audit verified both student
> records, and a third campus publication succeeded. Desktop configuration now
> uses `https://studentportal.bond`. Main-domain pages/assets, access denial and
> private-file denial passed. Historical cutover blockers below are resolved.

This deployment runs the existing portal API, authentication, sessions, and
protocol-v2 campus sync directly on Cloudflare Workers. D1 stores portal
snapshots, account password hashes and submissions. R2 stores original evidence
and must remain private. Face photos and recognition embeddings stay in the
campus SQLite database; the sync publisher excludes them.

## Prepare the target account

Cloudflare account `88e619a8bc929cbec00901fdadd3edc1` was authorized on
24 October 2026. D1 `cbvms-student-portal` and private R2
`cbvms-student-evidence` are configured in
`sites/student-portal/cloudflare/wrangler.jsonc`. These are separate resources
from the old Sites project. Keep R2 public access disabled.

## Build and check

From `sites/student-portal` in PowerShell:

```powershell
$repoRoot = (Resolve-Path ..\..).Path
$env:XDG_CONFIG_HOME = Join-Path $repoRoot '.runtime\cloud-config'
$env:WRANGLER_SEND_METRICS = 'false'
& (Join-Path $repoRoot '.runtime\node\node-v24.22.1-win-x64\node.exe') `
  (Join-Path $repoRoot '.runtime\cloud-tools\node_modules\wrangler\bin\wrangler.js') deploy `
  --config cloudflare\wrangler.jsonc --dry-run
node cloudflare\worker.test.mjs
```

The Node and Wrangler paths above are for this Windows checkout. On another
machine, use its installed Node and Wrangler versions. Run the portal's
`npm run typecheck` and `npm test` before deployment. The
standalone Worker entry only dispatches `/api/*` to the existing tested GET/POST
handlers. It serves the portal HTML, JavaScript, stylesheet and favicon from an
explicit allowlist, and returns 404 for other paths.

Before first publication, export and restore-check the existing Sites D1/R2 data.
The October 24 transfer backed up all six legacy tables through paginated Sites
reads and restored every column/value in disposable SQLite. Password hashes
were preserved. There were no original or appeal evidence references in the
exported records. The transfer helper refuses records needing a separate R2
backup. Persistent rows were copied to D1 and compared with the backup; sessions
and login-attempt counters remain in the backup only. A subsequent D1 SQL export
also passed a disposable restore check. Compare schemas and migration history;
apply missing SQL files in `drizzle/` in order, with backups and verification.
Set `SYNC_KEY` as a Worker secret, and do not put it in Wrangler vars or source.

## Campus connection and cutover

Configure the desktop's ignored `data/cloud-sync.json` with the new HTTPS Worker
origin and matching sync credentials. Keep protocol-v2 source and writer IDs
identical at both ends. The source registration, deployment, credential rotation,
and first sync have separate gates in `CLOUD_SYNC_RECONCILIATION.md`. Preserve
cloud-only accounts and queued submissions. Audit and reconcile against restored
copies first; publish only after backups, count comparisons, evidence checks and
portal login checks pass. Keep the Sites deployment available until students
can log in at the new origin and pending actions work.

## Limits

The deployment is verified at `https://migration.studentportal.bond` and
`https://cbvms-student-portal.cbvms-campus-gerry.workers.dev`. Campus source
registration and credentials are configured in ignored `data/cloud-sync.json`.
Two real campus publications passed, preserving the cloud-only account. Restart
the desktop app to start its configured background worker, which retries every
minute. Cloud portal access remains available when the PC is off; new campus
registrations and detections require the desktop worker to publish.

The owner explicitly approved the main `studentportal.bond` cutover after the
initial automatic-review rejection. Publication then failed with Cloudflare
error 100117: externally managed DNS records must be deleted first. The old
portal still serves the main domain. Only the public DNS answers and known
Sites routing addresses could be backed up; the OAuth grant cannot read or edit
the complete DNS record configuration. The two conflicting apex A addresses
are `162.159.143.30` and `172.66.3.26`. The owner can remove only those two
records in the dashboard, then add `studentportal.bond` under the new Worker's
Settings > Domains & Routes. Retain other DNS records. Until then, the temporary
migration hostname is active and the Wrangler configuration includes the
approved but not yet attached main domain.

Final verification: 34 Python sync/transfer tests passed; isolated D1/R2 HTTP
integration tests, standalone routing checks and TypeScript checks passed.
Live checks confirmed both active accounts, matching campus credentials, two
successful campus publications, pages/assets, unauthenticated denial, and 404
for a private-file path. The student's actual password login was not attempted.
