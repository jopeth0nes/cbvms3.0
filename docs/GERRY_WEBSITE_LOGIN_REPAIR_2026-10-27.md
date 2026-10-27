# Website login repair — 27 October 2026

The owner reported that student `2023-02034` could not sign in using the latest enrollment email's temporary password. Read-only provider diagnosis found an existing active website account with a completed password setup and a queued password change dated October 24. Neither its current hash nor its pending base hash matched the current local registration. The Mac has no `data/cloud-sync.json` or registered publication identity. The account was therefore present, but the two credential histories conflicted.

The owner explicitly selected: make the latest emailed temporary password work on the website and require a new password at login. The repair copied only the current local credential hash; no plaintext password was read, generated, emailed, or entered. It advanced the local and cloud credential revision to 2, required password setup, revoked existing sessions, cleared the pending credential pointer, and retained the prior password event as rejected/superseded. A provider-admin receipt is retained in `portal_actions`; a local administrative audit records the operation.

The same atomic cloud operation updated only academic snapshot paths to the current June 1–December 31, 2026 First Semester, AY 2026–2027. Other snapshot paths and student accounts were preserved. Gerry's record was not owned by the earlier import authority, so that import-specific projection operation correctly refused it; no authority record or import mapping was fabricated. The reviewed provider-only repair used exact account, password revision, pending-action and snapshot guards. It refuses inactive or lifecycle-managed accounts and does not expose an HTTP endpoint.

Private rollback metadata, local pre-change backup and verification receipt are under ignored `.runtime/gerry-login-repair/`. The cloud uses a managed Time Travel bookmark; unrelated production records were not exported. Never blindly restore the backup over subsequent student password changes. The operational script reads the durable receipt before considering a retry; the batch itself rejects stale state and replay rather than reapplying credentials.

Verification:

- `tests.test_provider_account_repair` and `tests.test_cloud_sync`: 40 tests passed with disposable data. Includes concurrent-password conflict, inactive account denial, revision fencing, replay refusal, session revocation, unrelated account preservation, and explicit missing-sync status.
- `node tests/account-repair.mjs`: passed against isolated real Worker/D1/password cryptography. Verified rejection of the previous password, temporary login with restricted session, mandatory new-password setup, successful subsequent personal-password login, rejection of the temporary password after setup, and the new pending event's expected revision.
- Live readback confirmed matching local/cloud credential hashes, required setup, cleared pending pointer, no remaining sessions, and the corrected semester projection.
- No real student's password login was performed by the agent. The owner was asked to retry and confirm the setup screen.

Desktop enrollment now warns when publication configuration is absent; cloud startup records an explicit paused status. Future credential emails explain that campus synchronization is required and that enrollment email does not automatically replace an existing website password. These local code changes require restarting the desktop. No website Worker deployment was needed for this data repair.

Automatic account publication and return synchronization remain unconfigured on this Mac. No Windows writer identity was copied or publication fence weakened. Gerry can complete website password setup without campus sync, but that subsequent change will queue for reconciliation; a second website password change is gated by the existing pending-change policy until it is reconciled. This account repair does not claim to configure multi-computer synchronization.
