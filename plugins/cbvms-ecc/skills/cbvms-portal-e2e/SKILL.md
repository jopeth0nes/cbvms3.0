---
name: cbvms-portal-e2e
description: "Verify meaningful CBVMS student portal workflows across native or web UI. Specialist/on-demand; skip expensive E2E for trivial visual edits and backend-only changes."
---

# Verify the changed journey

Choose the surface: native `ui/student_portal.py` or browser `web/` plus `web_portal.py`. Start with existing fixtures/tests; do not introduce a browser framework merely to run this skill. Use an available browser tool for real web interaction and a desktop session for native rendering.

Build only affected flows from synthetic fixtures: login/wrong password/throttle, forced password setup, own profile and violations, suspension state, evidence, appeal submission/status, notifications, expired session and logout. Verify cross-student isolation by changing resource IDs and supplied identity fields. Include keyboard/mobile checks when the changed web workflow requires them.

Existing entry points include `test_web_portal.py`, `test_password_setup_web.py`, `test_student_portal_native_ui.py`, `test_portal_requests.py`, and `scripts/verify_portal_navigation.py`. Inspect setup/teardown and database paths before running; avoid real emails, student data and production sessions. Prefer event/state waits to arbitrary sleeps.

Retain focused failure evidence without sensitive screenshots. State whether evidence is handler integration, native automation or actual browser E2E; one does not prove another. Reuse matching worker runs and test only missing/high-risk acceptance paths.
