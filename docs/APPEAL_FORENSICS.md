# Appeal supporting-image forensics

**Advisory, local, and deliberately uncalibrated.** No forensic detector can prove authenticity merely from image pixels. Results are decision-support evidence and require human review.

This subsystem never approves/rejects appeals, changes violations, awards/removes strikes, changes suspensions, selects decision categories or supplies decision reasons. The existing appeal-text `ai_recommendation`, `ai_confidence` and `ai_analysis` fields remain separate. An unavailable analysis does not prevent a manual decision. Existing original-evidence hash protections still apply independently.

## Data flow and audit

Both native and web submission call `insert_appeal`. Validation checks the original upload, the transaction commits the unchanged BLOB and SHA-256, then a best-effort forensic job is queued. Analysis failure does not roll back the appeal. Additional supporting images follow the same path. The original image is never resized, recompressed, stripped or overwritten. Derived grayscale/tensor/map data is disposable analysis output.

`evidence_forensics_runs` is an additive SQLite migration with evidence/appeal foreign keys, source SHA-256, processing state, classification, analyzer version, UTC timestamps, claim token/lease, structured signals, allowlisted metadata, container/pixel facts, C2PA result, model/version/checksum, reliability, nullable risk, map BLOBs, duration and sanitized error code. Indexes enforce one active job per evidence and one analyzing job per database. Terminal runs are retained; retry creates a new run. No legacy evidence hash is invented.

A SQLite-backed coordinator processes one job at a time, independently of student session lifetime. Claims expire into recorded errors; late worker results cannot overwrite another claim. A restart resumes pending work. `initialize(process_deadlines=False)` avoids starting workers during migration previews. The coordinator recovers uploads committed before scheduling by inserting missing jobs in batches of 64; administrator reads can also recover a missing initial job. POSIX workers additionally hold a database-path lock inherited by the child, so coordinator death or lease expiry cannot start overlapping inference while that child still runs. Keep the `.forensics.lock` sidecar in place while any process is running. Windows currently relies on the SQLite lease; inherited-lock behavior is not implemented or verified there. Retry is staff-only, pending-appeal-only and rate limited. Routine reports are hash-checked against both the current BLOB and its recorded upload hash. Stale reports and maps are withheld; their audit rows remain.

The child receives bytes through stdin, without a database connection. No shell invocation. CPU execution and numerical-library threads are limited; wall limits are 45 seconds normally, 120 seconds for an explicitly enabled model; the lease is longer. POSIX limits bound CPU, address space, file output and open files where supported, and lower scheduling priority. On the verified macOS host, CPU, descriptor and output-file limits applied, but `RLIMIT_AS` remained unlimited; there is no demonstrated hard memory cap on that host. Input dimensions, maps and execution time remain bounded. These are process resource controls, **not an OS security sandbox**. Windows lacks the POSIX memory/CPU controls. Never run the service with unnecessary filesystem permissions. The camera's inference code, dependencies and GPU are not used. Physical-camera FPS has not been measured.

## Supported input and observations

JPG/JPEG, PNG and BMP; 10 MiB encoded bytes, 20 million decoded pixels, at most 10,000 pixels per side. Decoding verifies container integrity before inference. Filename extensions are compared to actual format but mismatch alone is neutral. Arbitrary upload filenames are never used as filesystem paths.

Metadata is allowlisted and bounded: camera make/model, editor software, orientation and date fields. Dates are editable claims, not authoritative timestamps. Full XMP/IPTC packets and arbitrary EXIF fields are not retained or analyzed. GPS is recorded only as a presence flag; coordinates are excluded. Missing metadata and editor tags are neutral. Duplicate metadata-block detection is not implemented.

JPEG quantization-table facts, native-resolution eight-pixel difference profiles and local high-pass median absolute deviations are recorded without scoring thresholds. The pixel measurements use a documented central crop capped at 1024×1024. Edges, texture, camera pipelines, JPEG and benign processing affect them. They do not establish double compression, cloning, splicing, screenshot origin, screen recapture or AI generation. No ELA-based detection claim is made.

## C2PA

Optional official [`contentauth/c2pa-python`](https://github.com/contentauth/c2pa-python), pinned to **0.38.0** (observed native SDK **0.91.0**; Python >=3.10, MIT OR Apache-2.0). The Context/Reader API was inspected and exercised against real SDK fixtures. Remote-manifest retrieval, online OCSP and identity-assertion decoding are disabled; the allowed-network-host list is empty. Only embedded JPEG/PNG credentials are inspected. BMP and unavailable SDKs are explicit unavailable states, not absent credentials.

- `absent`: neutral; no embedded credential found.
- `valid`: the SDK reports `Trusted` under the locally configured trust anchors.
- `untrusted`: SDK `Valid`; cryptographic provenance is distinguishable from trusted signer identity.
- `invalid`: SDK validation failure; review the credential anomaly, not an allegation against the student.
- `error`/`unavailable`: no inference about authenticity.

Persisted assertions include bounded action identifiers and digital-source types only. AI source assertions are recorded separately from pixel-detector output. Valid editing history does not imply fraudulent editing. Invalid/untrusted assertions are marked unverified. Local trust anchors are fingerprinted; never install the SDK's test anchors for production. Offline revocation and missing remote manifests limit validation.

## Learned model evaluation decision

**No learned model is production-enabled.** TruFor is the optional experimental adapter candidate because its released inference code exposes localization, an image-level output and a reliability map. It is not a general proof of authenticity or a comprehensive AI-image detector.

| Candidate inspected | Release/runtime/license assessment | Decision |
|---|---|---|
| [TruFor](https://github.com/grip-unina/TruFor) | Released inference and training code; CPU/CUDA upstream; localization and reliability; informational/nonprofit license plus CMX notices. Upstream Docker uses old torch/NumPy versions. | Optional CPU-only adapter in a separate environment; no threshold. |
| [PhotoHolmes](https://github.com/photoholmes/photoholmes) | Python >=3.10, torch >=2.1, substantial extra dependencies; Apache-2.0 framework, method-specific licenses still apply. Warns of MPS output differences. | Do not import the entire framework into CBVMS. |
| [IMDLBenCo](https://github.com/scu-zjz/IMDLBenCo) | Released framework/checkpoints, CC-BY-4.0; image-metric bug fix documented for v0.1.29; recent Mesorch/Sparse-ViT/RITA directions. | Useful future comparison/evaluation framework, not a calibrated deployment model. |
| [FRD-Net (AAAI 2026)](https://github.com/chchshshhh/FRD-Net) | Released evaluation/training code and linked Drive weights, CC-BY-4.0 repository; recommends Python 3.8–3.10 and CUDA. No equivalent calibrated reliability evidence established here. | Defer pending isolated reproduction and CBVMS benchmark; newer publication alone is insufficient. |

TruFor source revision: `ae54475df6f41a491d7615100feb19263dec13f7`.
Reviewed `test_docker/src` bundle SHA-256: `6b06b819fac4b0095e37025793aa34be57532bcb15b8f5a205fc326f82203d9b`.
**Checkpoint SHA-256: unavailable; no checkpoint acquired or loaded.** Official download timed out during this implementation; [upstream issue #35](https://github.com/grip-unina/TruFor/issues/35) reports the outage. Upstream publishes an archive MD5, not a security-grade checkpoint digest. No unverified mirror was substituted. Real learned inference, latency, memory, reliability and accuracy therefore remain unvalidated.

The adapter validates the reviewed source bundle and a configured trusted checkpoint SHA-256 before importing model code. It requires an isolated torch >=2.10 (see [PyTorch security advisory](https://github.com/pytorch/pytorch/security/advisories/GHSA-63cw-57p8-fm3p)), uses restricted checkpoint loading and refuses a fallback to unrestricted pickle. It uses upstream RGB/256 preprocessing, keeps encoded-pixel coordinates, and refuses images above 1,048,576 pixels or below 32 pixels per side instead of silently downsampling. This size restriction is an experimental resource guard, not an evaluated operating range. Larger uploads retain metadata/provenance analysis and an explicit model limitation. Maps retain EXIF orientation information for display alignment.

## Optional runtime and acquisition

Core requirements are unchanged. To enable C2PA, use a separate Python 3.12 environment:

```sh
python3.12 -m venv /approved/local/path/forensics-env
/approved/local/path/forensics-env/bin/python -m pip install -r requirements-forensics.txt
export CBVMS_FORENSICS_PYTHON=/approved/local/path/forensics-env/bin/python
export CBVMS_FORENSICS_C2PA_TRUST_ANCHORS=/approved/local/path/anchors.pem
```

Trust anchors are optional; without an appropriate local trust policy, signed content can be untrusted. No student evidence is sent to obtain anchors.

For a future TruFor evaluation, acquire the upstream source at the recorded revision and obtain weights plus a SHA-256 through a trusted channel. Preserve all upstream license notices. The model is restricted to compatible informational/nonprofit use; eligibility must be established before enabling it. Use a separate runtime with compatible torch >=2.10, torchvision, timm and yacs; an exact learned-runtime lockfile cannot yet be certified without the checkpoint. Do not upgrade CBVMS's camera environment to satisfy this adapter.

```sh
.venv/bin/python scripts/prepare_forensics_model.py \
  --source /approved/TruFor/test_docker/src \
  --weights /approved/weights/trufor.pth.tar \
  --expected-sha256 <trusted-checkpoint-sha256> --receipt /approved/model-receipt.json
```

Then configure `CBVMS_FORENSICS_TRUFOR_ROOT`, `CBVMS_FORENSICS_TRUFOR_WEIGHTS`, `CBVMS_FORENSICS_TRUFOR_SHA256`, `CBVMS_FORENSICS_LICENSE_ACCEPTED=1`, and `CBVMS_FORENSICS_ENABLE_EXPERIMENTAL=1`. The runtime is CPU-only; there is no CUDA/MPS override to contend with the live camera. Model weights are never committed. Update the source pin/adapter and analyzer bundle version through review; rerun the benchmark and create new forensic runs. Old results remain historical and retain their versions.

## Admin interpretation and states

The Appeals workspace retains both original and supporting evidence, explanation, category selector, reason and manual decision buttons. A separate supporting-image panel shows processing status, hash status, provenance, model version/reliability and analyzed time. Details and history are staff-only. Maps offer original, overlay, map and reliability modes with a caution legend; colored pixels do not establish editing. Fresh hash checks and PIL preview preparation run on the background reader; Tk only displays bounded prepared previews. Map display was tested with synthetic maps, not learned-model output.

`pending`/`analyzing` mean processing has not finished. `error` means Analysis unavailable. `inconclusive` means the available pipeline cannot make a supported negative/positive claim. `review_recommended` currently means a C2PA validation anomaly needs examination. `no_significant_indicators` is reserved and not emitted by the current uncalibrated policy. `strong_manipulation_indicators` is not emitted at all. Numeric risk remains NULL. Raw model output, if available, is explicitly uncalibrated and never a probability of student misconduct.

No forensic details, thresholds or maps enter student portal responses. Students retain the existing successful-upload UX and eligibility/deadline rules.

## Reproducible evaluation

```sh
.venv/bin/python scripts/benchmark_evidence_forensics.py \
  --generate /private/tmp/cbvms-forensics-fixtures \
  --output /private/tmp/cbvms-forensics-benchmark.json
```

The generated corpus has 31 procedural fixtures: 10 benign variants and 21 manipulations (seven edits × original, JPEG65, resize). Benign cases include JPEG/PNG, resizing, successive JPEG encoding, crop, rotation, a metadata-free identity control, metadata rewriting and a simulated viewer frame. The metadata-free control intentionally duplicates original pixels; it does not exercise an actual stripping application. Edits include clone, removal, insertion, splice, background region replacement, local color and text overlay. These are **pipeline smoke fixtures**, not phone photographs, physical recaptures or representative school evidence. Rewritten metadata alone is correctly labeled benign for pixel evaluation. Fixture hashes and scene grouping are recorded; no student data is used.

Use `--manifest` for a consented/licensed corpus with relative paths, exact SHA-256, labels (`benign`/`manipulated`), categories, scene groups and optional ground-truth masks. Keep related transformations in the same split. Collect real phone devices, apps, screenshots/recaptures, AI inpainting/replacement, full synthetic images and valid/edited/tampered/absent C2PA samples. Independently review ground truth and preserve acquisition provenance. Fit thresholds on a development split, then freeze them for a held-out test split with representative prevalence. Report confidence intervals and device/editor subgroup failures before proposing any warning threshold.

The harness reports a 2×3 confusion matrix including abstentions, decided-only precision/recall/FPR/FNR, decision coverage, manipulated-without-alert rate, per-category resize/recompression behavior, runtime and failures. Optional `--evaluation-threshold` is solely a research comparison; it never changes the application policy. Localization IoU is evaluated separately when maps/masks exist. Do not quote decided-only rates without coverage.

See `APPEAL_FORENSICS_VALIDATION.md` for observed results and missing evidence.
