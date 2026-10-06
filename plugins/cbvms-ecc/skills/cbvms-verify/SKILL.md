---
name: cbvms-verify
description: "Verify meaningful CBVMS implementations or fixes with affected tests and diff review. Skip full-suite runs without a concrete cross-cutting risk."
---

# Targeted verification

Inspect affected files, `git status --short`, and the relevant tracked and untracked diff. Map acceptance criteria to evidence before choosing checks. Use `.venv/bin/python -m unittest tests.test_MODULE -v`; inspect the chosen test's fixtures first. There is no configured project-wide lint/typecheck gate to invent.

Reuse worker evidence only when commands, exit status, environment, and tested revision match the final files. Astra independently reads the diff and checks acceptance; rerun only invalidated evidence, gaps, or concrete risks. No second discovery pass or automatic full-suite run.

Choose checks by changed behavior:
- Portal: `tests/test_web_portal.py`, `test_password_setup_web.py`, `test_portal_requests.py`; native UI has separate `test_student_portal_native_ui.py` checks.
- Persistence: relevant migration/discipline/attendance tests; check transactions, foreign keys, duplicate events, old-schema compatibility and recovery using temporary databases.
- Vision: `test_live_pipeline.py`, `test_camera_switching.py`, `test_live_persistence.py`, `test_recognizer_identity.py`; inspect UI-thread boundaries, latest-frame queues, inference reuse, cooldowns, recognition stability and preview latency. Load vision evaluation only when measurements are needed.

Astra independently investigates security assumptions, migration safety and camera/AI evidence when relevant. Distinguish synthetic benchmarks from physical-camera FPS. Report commands/results, regressions, acceptance met, and unverified behavior. No invented passes or measurements.
