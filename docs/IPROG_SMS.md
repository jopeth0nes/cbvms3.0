IPROG automatic violation alerts
===============================

Alerts are queued when a new violation is published to the student portal.
Only the student's registered mobile number is used. Blank or invalid Philippine
numbers are skipped. Supported formats: 09XXXXXXXXX, 639XXXXXXXXX, +639XXXXXXXXX.
Existing historical violations are not backfilled.

Setup on Windows
----------------

Recommended persistent setup:

```powershell
.\.venv\Scripts\python.exe scripts\configure_iprog_sms.py
```

Enter the replacement token at the hidden prompt and restart CBVMS. Windows DPAPI
encrypts it for the current Windows user in ignored `data/iprog-token.dpapi`.
An `IPROG_API_TOKEN` environment variable overrides the saved token. This setup
does not send a message. Do not paste tokens into chat or commit them to Git.

For a published violation whose SMS was never queued, staff can explicitly send
its first notification after setup (this consumes SMS credits):

```powershell
.\.venv\Scripts\python.exe scripts\send_violation_sms.py --violation-id 1
```

The command requires staff authentication. It retains existing attempts and
does not repeat accepted, failed, unknown or sending rows. Check IPROG delivery
history before deciding what to do with an uncertain result.

Alternatively, temporary setup for a single PowerShell session:

1. Create/verify an IPROG SMS account and obtain credits. Check sender network
   coverage in your account, particularly for Smart/TNT.
2. Revoke the token previously shared in chat and create a replacement.
3. Open PowerShell in the project directory and enter the token privately:

```powershell
$smsSecret = Read-Host 'Replacement IPROG token' -AsSecureString
$env:IPROG_API_TOKEN = [System.Net.NetworkCredential]::new('', $smsSecret).Password
```

4. Launch CBVMS from that same PowerShell using your usual launch command.
   The variable is inherited only by processes launched from this shell. Repeat
   after opening a new shell. A .env file is not automatically loaded.
5. Register your own test number on a disposable student record and publish one
   violation to test delivery. This sends a real SMS and consumes credits.

No token means SMS is disabled. To disable it, remove the environment override
and the saved encrypted token, then restart the application:

```powershell
Remove-Item Env:IPROG_API_TOKEN
Remove-Item -LiteralPath .\data\iprog-token.dpapi
```

Sending runs in a single background worker per database. A transactional SQLite
outbox prevents duplicate attempts per violation and recovers pending alerts on
startup. It sends a short generic portal notice without names, evidence or
violation details. The token is sent in the HTTPS request body, never the URL.

The `violation_sms` table records pending, sending, accepted, failed, unknown or
skipped. `accepted` means IPROG queued the SMS, not confirmed handset delivery.
There are no automatic retries after a request: timeouts and crashes can leave
unknown/sending records because IPROG may already have accepted the message.
Check the IPROG dashboard before deciding whether to send another notice manually.
Missing numbers do not create retrospective alerts. New published violations with
valid registered numbers remain pending when configuration is unavailable; the
worker sends them after credentials become available and the application restarts
or the worker is awakened by a new violation. Historical violations that predate
this queue behavior are not automatically backfilled.

API reference: https://www.iprogsms.com/api/v1/documentation
