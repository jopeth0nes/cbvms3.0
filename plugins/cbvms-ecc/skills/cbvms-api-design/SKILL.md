---
name: cbvms-api-design
description: "Design or change CBVMS HTTP endpoints and contracts, including ownership, errors, pagination and retries. Skip internal Python APIs and tasks without an HTTP contract change."
---

# Preserve portal contracts

Start with `web_portal.py` routing, `web/app.js` consumers, `core/portal_state.py`, and the affected database method. Preserve existing paths and `{error: ...}` behavior unless the requested change includes a compatibility plan. Do not invent future endpoints or adopt a framework for a small contract change.

Derive the student ID from the authenticated session, never browser-supplied identity. Authorize each resource, including evidence, appeal and notification IDs. Preserve password-setup gates, session revalidation and CSRF for writes.

Choose resource names, methods and status codes deliberately; GET must not perform requested mutations. Validate JSON types, bounded body sizes, allowed filters and pagination with stable ordering. Define safe, consistent validation/auth/conflict/rate-limit errors. Consider idempotency for retryable ingestion or submissions; version only when a real incompatible consumer contract warrants it.

Verify happy path, malformed input, missing/expired session, cross-student access, and relevant retry behavior with temporary fixtures in `tests/test_web_portal.py` or a focused adjacent test. Security review is warranted for changed trust boundaries, not automatically every contract edit.
