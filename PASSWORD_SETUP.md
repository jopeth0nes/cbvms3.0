# Mandatory student password setup

Students authenticate with their existing credential, then receive either a restricted setup session or a full portal session. **Setup is required when `must_change_password` is true OR `first_login_completed_at` is null.** Successful authentication alone never completes setup. Administrators retain their normal role routing.

## Migration and existing accounts

`CBVMSDatabase.initialize()` runs the additive `migrate_credentials()` migration. It adds `must_change_password` (default 1), `first_login_completed_at`, `password_changed_at`, and `credential_version` (default 1), plus the `student_sessions` table. Repeating initialization preserves completed setup; it does not reset flags or passwords.

The old schema has only account ID, student ID, username, password hash, and creation time. The desktop activity log is an in-memory list; it cannot prove a historical password change. Enrollment issues random 10-character credentials; self-registration accepts a chosen initial password; administrator resets and student profile changes previously used the same reset function. There is no trustworthy durable completion history to migrate. All legacy accounts therefore receive one password-change requirement. Current usernames, mappings, and credentials remain valid for authenticating into setup. No plaintext initial passwords are stored. A read-only inspection of the existing database confirmed this schema; verification used disposable databases and did not migrate the real database.

New accounts created by enrollment, self-registration, `insert_student_account`, or `upsert_student_account` inherit the requirement. Upserting an existing account uses administrator-reset semantics and preserves its username. An administrator reset sets the flag and increments the credential version. It preserves the historical first-completion/password-change timestamps; those describe the student's authenticated changes, not resets.

## Password storage and validation

`auth/passwords.py` is the shared hashing/verification abstraction. New hashes use `pbkdf2_sha256$v1$600000$<salt>$<digest>`, with a random 16-byte salt and PBKDF2-HMAC-SHA256. Existing unsalted SHA-256 credentials remain verifiable and are upgraded on successful authentication using a compare-and-update write. This does not change the password or complete setup. Staff hashing uses the same abstraction and retains existing credentials.

The implementation uses Python's [PBKDF2 implementation](https://docs.python.org/3/library/hashlib.html#hashlib.pbkdf2_hmac) and the 600,000-round SHA-256 work factor documented by [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2). It needs no new dependency.

Both interfaces and the authoritative backend require confirmation and at least eight characters. The backend additionally verifies that the new password differs from the current authenticated credential. It rejects the account username/student ID and the bootstrap denylist (`password`, `password123`, `student123`, `admin123`, `superadmin123`). This list is a policy denylist, not an assumption that all accounts share one default. Random temporary credentials are rejected by verification against the current hash. Unicode, spaces, long passphrases, and paste are supported; passwords are never trimmed, normalized, or truncated. Newly chosen passwords are not logged, displayed, notified, or emailed.

## Sessions and atomic completion

Opaque tokens contain 256 bits of randomness; only their SHA-256 digests are persisted. Sessions bind an account ID, credential version, expiry, restriction state, and CSRF token. Restricted sessions expire after 10 minutes; full sessions expire after eight hours. Both processes use the same SQLite state, so revocation works across desktop/browser instances and restarts.

A restricted session permits only setup information, password completion, and sign-out. `/api/me` returns minimal session information without preferences while restricted. All portal APIs reject restricted sessions before reading records, evidence, notifications, appeals, profiles, preferences, or reports. The desktop does not construct a portal until setup completes; `StudentPortal` independently requires a full session whose student mapping matches the requested owner. Desktop worker database connections and browser portal database connections recheck persisted authorization. An open desktop portal checks revocation in the background every two seconds and returns to login when revoked.

`change_student_password(token, password, confirmation, current_password=...)` resolves identity exclusively from the authenticated token. Restricted sessions need no repeated current password; full-session profile changes must verify it. Expensive hashing occurs outside the write transaction. An immediate SQLite transaction rechecks the token, expiry, and version, then updates the hash, clears the flag, records UTC completion/change timestamps, increments the version, deletes old sessions, and inserts a new full session. Success is returned only after commit. A failed update or session insertion rolls everything back. Concurrent changes allow one winner. An administrator reset during hashing makes the older request fail.

Browser writes require a matching CSRF token and retain the existing same-origin check. Completion and ordinary password changes rotate the HttpOnly/SameSite cookie and CSRF token. Stale cookies receive HTTP 401; restricted access to portal routes receives HTTP 403. Existing deployments remain the local HTTP server described in `STUDENT_WEBSITE.md`.

## Desktop lifecycle

The setup panel occupies the right half of the same login window and retains the screenshot's navy/teal branding. It provides independent Show/Hide controls, inline errors, Save Password & Continue, and Back to Login. No portal is created behind it.

Login verification and password saves use daemon workers, short SQLite lock timeouts, cancellation events, and a 12-second UI deadline. Duplicate submissions are blocked. Back/close invalidate pending callbacks and revoke the restricted session. Cancellation before transaction completion prevents saving; a commit that already won a cancellation race remains valid, but cannot trigger stale navigation. A timeout tells the student to retry or sign in again if the save committed. Password fields are cleared after completion. Administrator resets also hash/save on a worker.

Welcome callbacks are guarded by a generation and full-login state. The end of the animation rechecks the session; portal construction rechecks it again to cover resets during the handoff. Ordinary profile password changes rotate the shared desktop session reference without causing a first-login loop.

## Changed files

- `auth/passwords.py`, `database/student_credentials.py`: hashing, policy, migration, sessions, atomic authenticated changes.
- `database/db_manager.py`, `auth/auth_manager.py`: shared account state, creation defaults, reset semantics, compatibility verification, restricted authentication results.
- `auth/login.py`: worker-driven login/setup and guarded welcome routing. `ui/login_intro.py` was reviewed; its existing cancellable intro remains unchanged.
- `ui/student_portal.py`, `core/portal_state.py`: full-session entry, worker authorization, live revocation, ordinary password changes.
- `ui/account_manager.py`: asynchronous reset and next-login messaging.
- `web_portal.py`, `web/app.js`, `web/style.css`: restricted API gate, setup UI, CSRF/session rotation, profile-password confirmation.
- `tests/test_password_setup.py`, `tests/test_password_setup_native.py`, `tests/test_password_setup_web.py`: backend, native navigation, and HTTP regressions.
- `tests/auth_fixture.py`, `tests/test_student_portal_native_ui.py`, `tests/test_appeal_flow_completion.py`, `tests/test_web_portal.py`: existing tests now establish authenticated full sessions. The web notification assertion counts suspension notices separately from the existing publication notices.
- `scripts/verify_portal_navigation.py`: the audit establishes setup in its disposable database copy before constructing a portal.

## Verification

All tests used disposable databases/accounts. Native tests ran in the actual macOS Tk event loop. A native setup screenshot was captured and visually inspected at `/private/tmp/cbvms-password-setup.png`; branding, text, fields, and both actions fit the fixed 920×620 window.

| Final command / check | Result |
| --- | --- |
| `.venv/bin/python -m unittest tests.test_password_setup tests.test_password_setup_native tests.test_password_setup_web -v` | **35 tests passed**, 40.918 seconds. Includes 14 credential tests, 11 native tests, and 10 HTTP tests (five inherited existing route regressions). Log: `/private/tmp/cbvms-password-feature-tests.log`. |
| `.venv/bin/python -m unittest tests.test_web_portal tests.test_student_management tests.test_startup_portal tests.test_portal_requests tests.test_student_portal_native_ui tests.test_appeal_flow_completion tests.test_student_management_ui -v` | **66 tests: 65 passed, one pre-existing failure**, 62.830 seconds. Log: `/private/tmp/cbvms-password-existing-tests.log`. |
| `.venv/bin/python -m unittest tests.test_password_setup_web tests.test_web_portal -v` after the final per-connection HTTP authorization change | **15 tests passed**, 18.563 seconds. Log: `/private/tmp/cbvms-password-web-final.log`. |
| `node --check web/app.js`, Python compile checks, `git diff --check` | Passed. |

The existing failure is `tests.test_portal_requests.PortalRequestTests.test_no_due_deadlines_need_no_writer_lock`: it receives `database is locked`. The same single test was run from an untouched HEAD copy in `/private/tmp/cbvms-password-baseline` and failed identically (one test, 0.830 seconds). It concerns existing deadline/suspension reconciliation, not password setup. Production reconciliation code was not changed for this feature.

Coverage includes initial enrollment/self-registration credentials, conservative legacy migration, repeat initialization, completed accounts, administrator resets (including the real Account Manager widget), mismatch/short/default/unchanged passwords, Unicode and whitespace preservation, failed update and failed session insertion rollback, retry, concurrent completion, duplicate button clicks, cancellation during save, worker timeout, Back/close/reopening, attempted premature welcome, reset during welcome, reset during hashing/setup, expired restricted sessions, HTTP refresh, every restricted portal route, CSRF rejection/rotation, authenticated identity despite spoofed form IDs, old-password rejection, both cross-portal directions, ordinary profile changes, live desktop revocation, and native administrator welcome.

**Remaining verification limitation:** the Browser skill initialized but reported no available browser; discovery returned an empty list. Actual browser DOM navigation and visual rendering could not be exercised. Browser requests were tested through the real HTTP server, and JavaScript syntax was checked. Native UI checks did run and passed. The existing native portal regression fixtures now complete authenticated setup before entering their portals.

## Manual walkthrough

1. Enroll a disposable student or self-register one, then sign in using its generated/chosen initial credential. Confirm setup appears in the login window and the portal is absent.
2. Try a short password, mismatched confirmation, and the current temporary password. Each must remain on setup with an inline error. Test each Show/Hide button and paste a passphrase.
3. Save a different passphrase. Confirm the normal welcome transition runs and the student portal opens. Log out: the old credential must fail and the new one must bypass setup.
4. Sign in through the website against the same database. Verify refresh retains the correct setup/full state. A restricted cookie must receive 403 from `/api/page`; setup POST without its CSRF token must receive 403.
5. Reset the account in Account Manager. The next login on either surface must require setup. Reset again while setup is open and confirm the older form cannot replace that reset. Verify Back/close never opens a portal.
6. Change the password from the portal profile page. Confirm normal subsequent login and rejection of old sessions. Sign in as an administrator and confirm the normal welcome/dashboard flow.
