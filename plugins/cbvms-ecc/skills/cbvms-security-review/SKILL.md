---
name: cbvms-security-review
description: "Review security-sensitive CBVMS changes to identity, permissions, sessions, uploads, secrets or student/biometric data. Skip unrelated styling and ordinary non-sensitive logic."
---

# Security boundaries

Trace only the affected flow through `auth/auth_manager.py`, `auth/passwords.py`, `database/student_credentials.py`, `database/db_manager.py`, `core/appeal_evidence.py`, and `web_portal.py` as needed.

- Preserve student/admin/superadmin boundaries, first-login password restrictions, persisted session revalidation, expiry and revocation. Student usernames must not fall through into staff authentication.
- Identity comes from the authenticated session. Mutating URL IDs, query parameters, JSON or form fields must never expose another student's records, evidence, appeals, notifications or exports. Check ownership at the database boundary as well as the handler; retain `student_session_ref` safeguards.
- Review password hashing, constant-time token checks, HttpOnly/SameSite cookies, CSRF, login throttling, safe errors and parameterized SQL. Validate dynamic identifiers against an allowlist.
- Bound upload and decoded image sizes; validate bytes, names and paths. Prevent traversal and executable-content handling. Preserve original evidence integrity.
- Keep credentials in the environment/approved secret store. Never include passwords, session tokens, student records, face photos or embeddings in logs, worker briefs or committed fixtures. Preserve `public()` exclusions from portal JSON.
- For hosted changes, verify HTTPS, Secure cookies, trusted proxy/Host handling and explicit origin/CORS behavior; current local HTTP assumptions are not production guarantees.

Use two synthetic student identities for negative isolation checks and relevant password/web-portal tests. Astra checks high-risk assumptions independently even when worker tests pass; report specific defects and evidence without copying sensitive data.
