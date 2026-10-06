# Appeal forensics continuation validation — 2026-10-06

## Recovered state and continuation

Branch: `Ecc-Coc`. The working tree was preserved, with no reset, stash, commit or push. At recovery there were five modified tracked files and five untracked forensic files. The existing two `CODEX_AI_*` document edits belonged to the earlier tooling completion and were left intact.

Already in the repository: bounded upload validation, analyzer/worker, additive SQLite run storage, job claims/leases, upload scheduling, report hash checks, and the native advisory panel. The C2PA/model/classical adapters, benchmark, model-receipt helper, adapter tests and forensic documentation existed only as staged files in `/private/tmp`; they were inspected and recovered into the repository. No implementation worker was needed or spawned.

Continuation changes:

- Wired the recovered bounded descriptive pixel measurements into analysis and added an optional isolated runtime requirements file.
- Reproduced and fixed the committed-upload/before-scheduling crash gap. Coordinator polling now recovers missing jobs in bounded batches without creating legacy upload hashes.
- Added a POSIX database-path lock inherited by the child, beyond the SQLite single-claim constraint; a competing process leaves the reserved job pending. Checked current claim tokens before execution. Removed duplicate evidence hashing at completion.
- Fixed the missing `digest` import in forensic details. Prepared oriented image/map previews on the background reader, bounded displayed images, and handled invalid map data without crashing Tk.
- Tracked/cancelled forensic polling on navigation/destruction. History now loads fresh hash-validated classifications rather than cached classifications. Unavailable-state text no longer incorrectly asserts that an upload hash is missing.
- Discarded optional-library stdout through the null device instead of accumulating it in an unbounded string buffer.
- Added focused restart, populated migration, full-record decision isolation, process-lock, worker environment/resource/privacy, native state/map/history and timer regressions.
- Improved the benchmark's successive JPEG encoding and simulated viewer frame; documented the metadata-free identity control and corrected unsupported XMP/IPTC claims in the recovered draft.

## Architecture and safety evidence

The unchanged original image BLOB and SHA-256 are committed with the appeal. A separate SQLite run records status, source hash, UTC timestamps, analyzer version, descriptive facts, C2PA result, optional model identity/checksums, sanitized errors and bounded map BLOBs. A background coordinator runs one-shot local subprocesses. No camera/live-inference module is changed.

SQLite `BEGIN IMMEDIATE`, unique active-evidence and single-analyzing indexes, leases and claim tokens protect claims. Pending work survives restart; missed scheduling is reconciled. Interrupted analyzing runs become auditable errors on lease expiry, with manual retry available. They are not silently treated as successes. Old tokens cannot publish over terminal/new work. Repeat migration on populated legacy fixtures preserves evidence bytes/links and leaves old baseline hashes NULL; `PRAGMA foreign_key_check` passes.

Snapshot comparison covers every pre-existing table and every column, not only counts: a forensic review alert changes only the forensic run. Appeal status/category/reason, violation status, strikes/events, decision history, student suspension fields and other records are unchanged. Native tests preserve an administrator's category/reason across pending, analyzing, error, inconclusive and review-recommended states, then explicitly invoke a manual decision. Existing category/discipline tests verify manual decisions still behave correctly.

Staff-only read/retry checks reject unknown/student usernames and student-bound database sessions, including a supplied admin username. The feature exposes no new student HTTP endpoint. Existing two-student HTTP tests verify ownership, authentication and CSRF. Original BLOBs are unchanged; fresh read-time hashes bind reports/history/maps to the source and baseline. A changed source suppresses technical signals/maps. Native stale-source tests also cover a previously loaded case.

Worker stdin carries evidence; there is no shell invocation, external AI API call, DB connection in the analyzer, or raw-evidence logging. The subprocess environment is allowlisted and excludes unrelated credentials. C2PA remote manifests, OCSP fetching and identity decoding are disabled. Full assertion bodies, identity/URL/GPS payloads are not persisted. These are application protections, not an OS sandbox.

## C2PA and learned detector

Official `c2pa-python` **0.38.0**, observed native SDK **0.91.0**, was reused in `/private/tmp/cbvms-forensics-env`. The main application venv does not contain C2PA; deployments must explicitly select an optional runtime using the documented `CBVMS_FORENSICS_PYTHON` setting. No global environment or camera dependencies were modified.

Real official fixtures verified: no credential -> neutral absent; signed without test anchors -> untrusted; signed with disposable test anchors -> trusted; tampered signed bytes -> invalid with `assertion.dataHash.mismatch`; signed edited/AI-source assertions -> recorded separately. Test anchors are disposable and were not installed as a production trust policy. The configured optional interpreter also passed the actual DB-to-subprocess-to-report integration test.

TruFor remains disabled and experimental. The recovered source digest matches the reviewed upstream `test_docker/src` tree at revision `ae54475df6f41a491d7615100feb19263dec13f7`. No checkpoint was acquired or loaded, and no checkpoint SHA-256 is claimed. The previous download failure is consistent with the still-open [upstream outage report](https://github.com/grip-unina/TruFor/issues/35), checked during this continuation. No repeated weight download or mirror substitution was attempted. Real inference, compatibility of a complete learned runtime, calibration, accuracy, memory and latency remain unvalidated. Source/weight checks, explicit experimental/license flags and restricted loading are retained. The torch >=2.10 guard matches the [upstream checkpoint-loader advisory](https://github.com/pytorch/pytorch/security/advisories/GHSA-63cw-57p8-fm3p).

## Executed verification

All application fixtures used disposable databases, synthetic identities and local evidence. Native tests had approved macOS display access; HTTP tests had approved localhost access. No live database was initialized or migrated.

Commands below are shown without output redirection; logs are under `/private/tmp/cbvms-forensics-continuation-*.log`.

```sh
.venv/bin/python -m unittest tests.test_evidence_association.EvidenceAssociationUITests -v
```

Initial recovery check: **4 passed**. No trustworthy matching prior success log was found.

```sh
.venv/bin/python -m unittest tests.test_evidence_forensics.EvidenceForensicsDatabaseTests.test_restart_recovers_upload_committed_before_schedule -v
```

Regression-first check: **failed as expected** before recovery fix (`claim_next` returned None). It passes in the final backend run.

```sh
.venv/bin/python -m unittest tests.test_evidence_forensics tests.test_forensics_adapters.AdapterPolicyTests -v
```

Final: **27 passed**. Earlier runs passed 22 and 25 tests before the additional safety tests were added. Covers jobs, restart, leases, concurrent claims/retries, separate-process lock exclusion, stale results, migration, privacy, actual subprocesses, controlled crash/timeout paths, authorization and abstention metrics.

```sh
CBVMS_C2PA_TEST_FIXTURES=/private/tmp/cbvms-c2pa-research/tests /private/tmp/cbvms-forensics-env/bin/python -m unittest tests.test_forensics_adapters.RealC2PATests -v
```

**2 passed**, using official fixture bytes and disposable trust configuration.

```sh
CBVMS_FORENSICS_PYTHON=/private/tmp/cbvms-forensics-env/bin/python .venv/bin/python -m unittest tests.test_evidence_forensics.EvidenceForensicsDatabaseTests.test_worker_stores_inconclusive_historical_run_without_discipline_effects -v
```

**1 passed** with the actual optional subprocess interpreter.

```sh
.venv/bin/python -m unittest tests.test_forensics_ui tests.test_evidence_association.EvidenceAssociationUITests tests.test_appeals_workspace_ui -v
```

**19 passed** after polling cleanup, including native submission/retry/admin association, responsive layouts, drafts, manual decisions, forensic states, map modes and EXIF alignment. An earlier run passed 29 executions because an imported TestCase caused duplicate discovery; that import was corrected.

```sh
.venv/bin/python -m unittest tests.test_forensics_ui -v
```

Final focused rerun after fresh history hashing: **4 passed**. History displays inconclusive and withholds the old review classification after source mutation. This rerun covers the behavior changed after the 19-test run.

```sh
.venv/bin/python -m unittest tests.test_evidence_association.EvidenceAssociationTests tests.test_appeal_flow_completion.AppealFlowBackendTests tests.test_appeal_categories tests.test_portal_requests -v
```

**33 passed**, including exact evidence ownership, legacy migrations, category/decision transactions and student portal state.

```sh
.venv/bin/python -m unittest tests.test_web_portal -v
```

**7 passed** after approved rerun. The initial sandbox attempt had seven setup errors because localhost binding was prohibited; those were environment errors, not product failures. These are real HTTP handler/session integration tests, not browser-rendering E2E. Native student/admin interaction was exercised separately above.

```sh
.venv/bin/python scripts/benchmark_evidence_forensics.py --generate /private/tmp/cbvms-forensics-fixtures --output /private/tmp/cbvms-forensics-benchmark.json
/private/tmp/cbvms-forensics-env/bin/python scripts/benchmark_evidence_forensics.py --manifest /private/tmp/cbvms-forensics-fixtures/manifest.json --output /private/tmp/cbvms-forensics-benchmark-c2pa.json
```

Both final runs: **31 processed, zero pipeline failures, 31 model-not-configured abstentions**. Ten benign and 21 manipulated fixtures; decision coverage 0; decided-only precision/recall/FPR/FNR undefined (NULL); all manipulated samples had no model alert. No production threshold. Final manifest SHA-256: `0dff95a237e702e0f71a99b17285c781df734c8833ec31b1f8729597fc1dd436`. Initial runs also passed before the fixture improvements; their manifest hash is superseded. Observed total/median analysis times were 193.110/3.660 ms in the main venv and 409.121/4.395 ms in the optional C2PA venv. These exclude production queue/subprocess startup and are not learned-model latency or accuracy measurements.

Additional inspections: TruFor source digest matched; `c2pa.sdk_version()` returned `0.91.0`; a disposable child resource probe showed macOS `RLIMIT_AS` remained unlimited. The regression test verifies CPU (100/101 s), file output (2 MiB), descriptors (64) and alarm settings. Parent subprocess wall timeouts are 45 s by default / 120 s experimental. `git diff --check` passed; tracked and untracked source changes were reviewed.

## Remaining limits

- No representative phone, AI-edit, real screenshot/physical recapture, independent scene/device or prevalence dataset; synthetic fixtures establish pipeline behavior only. No detector accuracy or production warning threshold is claimed.
- No real learned inference or learned maps; UI map validation uses synthetic maps. C2PA validates credentials under configured trust, not the truth of depicted events.
- macOS did not apply the requested address-space cap. Input/pixel/map/output/time bounds remain, but hard peak-memory containment is not demonstrated. POSIX limits are best effort; this is not a filesystem/network sandbox.
- Windows does not have the inherited POSIX process lock or POSIX resource controls; only SQLite claim/lease exclusion exists there, and Windows crash/overlap behavior was not validated. Linux limits were not run here. Use the verified POSIX execution model for the demonstrated singleton guarantee; DB hard-link aliases/network filesystem semantics are outside this validation.
- Browser-rendering E2E, physical-camera FPS and live production migrations were not run. No web frontend/camera code changed. Existing original-evidence hash restrictions can independently block rejection; forensic pending/error/inconclusive states do not.
- The optional runtime lives in a disposable path for this verification. Production runtime/trust/model setup requires the documented local configuration; no production learned runtime is certified.

Git changes are intentionally unstaged/uncommitted for review, including the pre-existing workflow documentation edits. No push or deployment occurred.
