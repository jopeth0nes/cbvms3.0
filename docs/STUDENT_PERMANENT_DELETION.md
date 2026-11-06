Permanent local student deletion
================================

Moving a student to the Recycle Bin is reversible and reserves the student ID
and account username. To free those values for a fresh registration, sign in as
superadmin, open the Recycle Bin, select the student and choose Delete Permanently.
Type the student ID and enter a reason. This is an irreversible application action.

Permanent deletion removes the selected local student, account and sessions,
enrollment photos and face embeddings, term enrollment/captures/reviews,
attendance records, violations, appeals, evidence, notifications and suspensions.
Other students remain unchanged. The face gallery refreshes in the background.
The immutable lifecycle audit remains, recording who deleted the student and why.
External backups, exported reports and cloud databases are not erased.

New registration may reuse the student ID, account username and face. It receives
a new student primary key and a higher lifecycle version, preventing old queued
camera work from accepting the deleted identity as the fresh registration.

Deletion requires a current superadmin session and the selected archived version.
It rolls back atomically on errors, including restoring the account and normal
history guards. It blocks installations with a registered campus cloud source,
cloud-sync configuration, or verified lifecycle cloud history. Those hosted
identities need reconciliation before local ID reuse; restoring the existing
student preserves the current identity instead.

This operation is available only through the explicit confirmation flow. It does
not automatically delete Recycle Bin entries during registration.

Provider-owner deletion of an existing website identity
-----------------------------------------------------

For the explicitly confirmed desktop-and-website deletion, the administrator tool
is `scripts/delete_student_everywhere.py`. It defaults to a read-only preflight:

```powershell
.\.venv\Scripts\python.exe scripts\delete_student_everywhere.py --student-id STUDENT_ID --backup-dir VERIFIED_BACKUP_DIRECTORY
```

Only add `--apply` after confirming deletion scope. The backup directory must
contain verified `campus.db` and `cloud-restore.db` copies. The tool requires the
provider owner's Cloudflare OAuth credentials and a matching, immutable,
superadmin recycle operation; the limited website registration key cannot claim
or delete an existing website identity.

The current tool supports a matching cloud account with no cloud violation/appeal
evidence or protected lifecycle history. It stops for those more complex cases.
Cloud receipt creation and targeted deletes run in one server-side statement.
An interrupted local step can resume using the same receipt. A re-registered cloud
identity prevents an old receipt from being used again. Other students' records
and pending actions are preserved.

The desktop button retains its cloud safeguard. Do not remove the connection file
or bypass that safeguard to force a local-only deletion.
