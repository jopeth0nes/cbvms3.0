# Cloud student portal

> Protocol-v2 synchronization now requires explicit campus source registration
> and production approval. See [reconciliation report and runbook](CLOUD_SYNC_RECONCILIATION.md).
> The setup/domain statements below describe the original deployment; live access
> could not be verified during the October 20, 2026 review.

The cloud project is `sites/student-portal`, Site ID
`appgprj_6ad63eb439e08191b42fdcd1035145c2`.
Its hosted origin is https://cbvms-student-portal.epicmaple8.chatgpt.site.
The Site is public: the student portal opens without the private Sites access
screen. Student records require the student username and password registered in CBVMS; ChatGPT sign-in is not used.

## How records work

The existing desktop SQLite database remains the campus source of records.
Only student portal metadata and violation evidence are uploaded. Face encodings,
profile photos and plaintext passwords are excluded. Student password hashes are synced separately to server-only cloud account storage for login verification.
Cloudflare D1 stores cloud snapshots and submissions; R2 stores pictures.

The hosted Site continues serving the last synced records when the PC is off.
New campus detections and decisions appear after the next desktop sync. The UI
shows the snapshot time. Students sign in with their CBVMS student username and password. New accounts appear after campus sync. Contact emails are not used as login identity. First-login password setup remains required. No student rows were present during initial setup.

Appeals, reports, name changes and notification read actions are saved online.
The desktop imports bounded pages of queued commands and atomically publishes
updated records with acknowledgments. Transactional local receipts make retries idempotent.
The Site assigns submission timestamps. On-time submissions preserve those times
when imported after a PC outage; eligibility is rechecked against campus records.
Changed or resolved records may require administrator review. Password changes take effect online immediately and sync back as hashes. If an administrator resets the campus password before a cloud change imports, the campus reset takes precedence. Sessions are revoked on password changes, logout, account deletion, and roster revocation.

## Desktop sync

The ignored `data/cloud-sync.json` holds the Site origin and service credentials.
Never commit or share it. The matching `SYNC_KEY` is stored as a Sites runtime
secret. Source repository credentials are separate and are not stored here.

Desktop launches start a worker only when configuration exists; uploads require
`enabled: true`, matching protocol-v2 registration, and a reviewed production plan.
Default CLI invocation is now a read-only audit:

```powershell
.\.venv\Scripts\python.exe .\scripts\sync_student_cloud.py --audit
```

After the runbook's approval and backup gates, `--once` performs one verified
publication. Settings provides audit and retry controls. Database-scoped file locks
and cloud generation fencing protect publication. Roster absence never disables
students, and missing credentials never delete cloud accounts.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_cloud_sync tests.test_web_portal
```

The cloud project also passes student username/password HTTP integration checks, TypeScript checks and a production build. The local
HTTP integration test, `tests/check_cloud_portal_http.py`, verifies authentication,
student isolation, CSRF, picture ownership, queueing, duplicate prevention and
protocol/source fencing against isolated D1/R2 fixtures. It creates synthetic
local fixtures only; no sample students are deployed.

## Domain

The custom domain https://studentportal.bond is connected and HTTPS is active.
Cloudflare manages the active DNS. The previous apex A record was replaced,
with explicit owner approval, by the two Sites routing addresses plus the Sites
verification TXT records. Unrelated DNS records were preserved. The Site opens publicly; student records require an authenticated CBVMS student session.
A PC tunnel is not required for this cloud version.
