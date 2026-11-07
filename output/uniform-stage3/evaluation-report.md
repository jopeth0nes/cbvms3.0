# Stage 3 independent evaluation backend report

Status: **ready for lead review**. This remains an evaluation-only backend: it freezes explicitly reviewed final-test sets and applies caller-supplied paired predictors to the same reviewed BGR crops. It never loads weights, trains, tunes, augments, activates, or switches models. No real dataset or real model was evaluated in this work.

Each imported set now has a stable content identity over reviewed records, image hashes, crops, labels, capture provenance, and independence declaration. Import time and source/storage paths are excluded so identical content re-imported under a new set ID cannot bypass a prior attempt. At first evaluation, an atomic root-level ledger binds the ordered baseline/candidate descriptor IDs and both policy versions. The binding is written before independence/overlap checks or predictions, so cancelled and failed attempts consume the binding too; a changed policy for the same content and model pair fails closed. Different descriptor IDs constitute a different model pair.

`load_set()` verifies the frozen manifest file digest, content identity, image inventory digest, and every stored original image SHA256. Terminal reports receive an atomic SHA256 sidecar. `load_report()` and `report_checksum(report_id)` verify the report against that sidecar; the checksum API returns the verified report JSON hash. For activation review, the registry must pin this returned checksum together with report ID, dataset hash, baseline/candidate IDs, and policy versions. The sidecar detects report-only edits but is not a cryptographic signature; the external registry pin is the durable review binding.

Completed reports expose `status`, `report_id`, `set_id`, `dataset_sha256`, baseline/candidate descriptor snapshots (including optional `dataset_version`), `independent`, `synthetic`, `policy_version`, `per_model.baseline/candidate.classification`, coverage, campus comparison, limitations, and predictions. Classification metrics provide Wilson intervals for prediction abstentions over both the assessable reviewed class-metric population and all reviewed examples, each with an explicit denominator. Human unassessability is not counted as model abstention, and an abstention never becomes an `other_clothing` prediction. Failed, cancelled, and interrupted reports have null metrics and terminal checksums. Synthetic inputs remain experimental and ineligible for real-data claims. No accuracy threshold or activation decision is encoded.

## Verification

- `.venv/bin/python -m py_compile core/uniform_evaluation.py tests/test_uniform_evaluation.py` — passed.
- `.venv/bin/python -m unittest tests.test_uniform_evaluation -v` — **19 passed**. Focused regressions cover cancelled-attempt policy binding across identical re-import, frozen-manifest mutation rejection, terminal-report mutation detection, separate all-example/assessable-population abstention rates, and descriptor dataset-version retention. Existing coverage checks safe import, image hash/provenance, overlap, paired metrics, cancellation/failure/recovery, and locking.
- No real evaluation, model load, training, registry activation, or production dataset access was performed.

## Owned file SHA-256

- `core/uniform_evaluation.py`: `6dfb4b7dab56f6a087cc1420c523a99048d482cd3a4b4986aa1bf96fd90089e3`
- `tests/test_uniform_evaluation.py`: `96ccee61705578c31522ff4133a92c5e905a527103d0b1413a799933b75e220d`
- `docs/UNIFORM_EVALUATION_MANIFEST.md`: `53a1e7b9fe37ccfa10390c9331b9c4410186506c3229bfd6250f4749a5f38c25`

No real independent final-test set is available here, so real-model evaluation and deployment readiness remain unverified. Terminal sidecars are integrity checks rather than signatures; activation review must compare against the checksum persisted by the registry.
