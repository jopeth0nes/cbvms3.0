# Stage 3 registry tests

Status: **ready for lead review**. Added isolated registry tests using disposable temporary directories only. The tests exercise the current registry implementation; no registry source was changed.

Coverage includes:

- Current superadmin role validation and a session revocation between validation and pointer switch.
- Completed, nonsynthetic candidate/report binding; class map, preprocessing, candidate identity/checksum, policy, class-support, report checksum, and synthetic-state rejection.
- Atomic activation and rollback pointer changes while preserving source legacy asset hashes.
- Recovery after failure before `active.json` changes and after pointer commit before journal commit.
- Stale generation rejection and a concurrent result guard blocking an exclusive model switch.

Verification:

- `.venv/bin/python -m py_compile tests/test_uniform_model_registry.py` — passed.
- `.venv/bin/python -m unittest tests.test_uniform_model_registry -v` — **9 passed**.
- `git diff --check -- tests/test_uniform_model_registry.py` — passed.

The fixtures patch `candidate_descriptor` and `DeploymentPredictor` to avoid model inspection or loading. Registry bundle publication, metadata validation, report/set binding, checksum checks, pointer switching, recovery, and locks are exercised against real temporary files. Fake evaluation metadata supplies the report/set API; no real final-test data or production registry root is used.

Test file SHA-256: `e08855c79bfd2eb45d737e0a5d53dba5d8166e1dcf328d3b00d07f6602c390a9`.
