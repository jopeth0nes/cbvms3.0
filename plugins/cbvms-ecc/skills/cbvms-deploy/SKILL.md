---
name: cbvms-deploy
description: "Prepare or change CBVMS hosting, release configuration, backups or recovery. Skip local development tooling and ordinary application edits."
---

# Simplest sufficient deployment

Inspect `STUDENT_WEBSITE.md`, `web_portal.py`, environment handling and the actual target. The current website uses Python `ThreadingHTTPServer` and local HTTP assumptions; do not describe it as production-hardened. Camera/ML desktop execution and hosted portal access have different requirements.

For the requested release, establish HTTPS termination, suitable production serving, trusted proxy/Host behavior, Secure/HttpOnly/SameSite cookies, explicit origin/CORS policy, CSRF and rate limiting. Current cookies/origin checks need deliberate review behind HTTPS proxies.

Separate development and production secrets, database paths/connections and static assets. Check bootstrap staff credentials, restricted filesystem access, log redaction and useful error reporting. Define health checks, backup restoration, migration order, rollback compatibility and recovery ownership.

Prefer a single understandable deployment. Add CI/CD only where it supports actual release needs; containers, Kubernetes and extra cloud services are not defaults. Verify in staging with synthetic identities. Provisioning or publishing needs task authorization; a readiness review does not authorize deployment.
