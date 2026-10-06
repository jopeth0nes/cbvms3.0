---
name: cbvms-regression-first
description: "Fix confirmed CBVMS bugs with a minimal reproducible regression check. Prioritize detection, FPS, duplicate events, discipline, authentication, appeals and notifications; skip blanket TDD for new work or trivial styling."
---

# Fix the observed regression

Understand the failure and locate its owner before editing. Reproduce when practical, identify the root cause, add the smallest useful failing test/check, make the targeted fix, then verify affected behavior. Preserve useful failing evidence; if reproduction needs unavailable camera data or a desktop session, state that limit and choose a narrower check.

Reuse existing unittest fixtures and temporary databases. For duplicate events, assert persisted counts and cooldown behavior; for session/authorization bugs, include the denied path; for preview issues, distinguish scheduling checks from measured FPS. Relevant homes include `test_live_pipeline.py`, `test_violation_workflow.py`, `test_suspensions.py`, `test_unknown_sightings.py`, `test_password_setup.py`, `test_appeal_flow_completion.py` and `test_live_notifications.py`.

No arbitrary coverage targets, large replacement test suite, checkpoint commits, tests that merely mirror code, or unrelated refactors. A tiny visual edit can use inspection. Reuse trustworthy worker reproduction and verification; request missing evidence from the same worker instead of redoing discovery.
