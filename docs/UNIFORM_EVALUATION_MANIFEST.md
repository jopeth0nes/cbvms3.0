# Independent uniform final-test manifest

`EvaluationService.import_set(path)` accepts a JSON manifest with schema version `1` and a nonempty manifest `version`. The manifest must describe a reviewed `final_test` set and state why it is independent, who reviewed that claim, and which baseline descriptor it applies to. The set importer verifies every image and crop before making an owner-only frozen copy; it does not read model weights or infer labels.

Use the field names and fixed garment IDs below. Replace every placeholder with real reviewed metadata. This template is intentionally not importable until all placeholders are replaced with real paths, SHA256 digests, group identifiers, and explicit review outcomes.

```json
{
  "schema_version": 1,
  "version": "replace-with-manifest-version",
  "purpose": "final_test",
  "synthetic": false,
  "independence": {
    "baseline_id": "replace-with-baseline-descriptor-id",
    "reviewed_by": "replace-with-reviewer-identity",
    "statement": "Describe how these captures were held out from both training datasets."
  },
  "records": [
    {
      "path": "images/replace-with-reviewed-image.png",
      "sha256": "replace-with-64-character-lowercase-image-sha256",
      "category": "campus_uniform",
      "review_status": "approved",
      "crop": [0, 0, 100, 100],
      "capture_group": "replace-with-known-camera-session-group",
      "camera_id": null,
      "lighting": null,
      "assessability": "assessable",
      "policy_label": "unknown"
    }
  ]
}
```

`category` must be exactly one of `campus_uniform`, `organizational_shirt`, or `other_clothing`. `crop` uses integer coordinates `[x1, y1, x2, y2]` in the original decoded raster. EXIF orientation is recorded but the image is never auto-rotated or mirrored. Each record needs a nonempty known `capture_group`; unknown provenance is not treated as independent evidence. Exact duplicate image hashes within one set are rejected. `camera_id` and `lighting` may be null so their missing coverage can be reported. `assessability` is explicitly one of `assessable`, `unassessable`, or `unknown`. `policy_label` is independently reviewed as `compliant`, `violation`, or `unknown`; garment class is never translated into policy compliance.

At evaluation time, model descriptors provide stable `id`, `labels`, `preprocessing_version`, nonempty complete `training_hashes`, `training_groups`, `training_dhashes`, `synthetic`, and optional `policy_version`. The declaration's `baseline_id` must match the baseline descriptor. The service rejects exact-hash, capture-group, and conservative dHash overlap (Hamming distance ≤5) with either model's training inventory. If a descriptor's inventories are incomplete, the service cannot establish independence; do not submit an incomplete descriptor as evidence.

The same frozen crops are passed as BGR arrays to both caller-supplied predictors. The evaluator itself does not load models or train, tune, augment, activate, or choose a model. A null prediction is counted as unassessed. Candidate and baseline class metrics include only records whose reviewed `assessability` is `assessable`; predictions and coverage still include all reviewed records, with other assessability states counted as excluded from class denominators. Candidate metrics use the fixed three-class labels. For a legacy `correct_uniform`/`wrong_uniform` baseline, raw truth-by-baseline-label confusion is reported; the `correct_uniform` response rate among Campus Uniform examples is only a named proxy, never Campus Uniform recall. Policy false/missed violation rates use only explicit policy truths and explicit policy predictions. Undefined denominators produce null values, with Wilson 95% intervals for defined proportions. No acceptance threshold or accuracy claim is encoded.

Completed reports expose `status`, `report_id`, `set_id`, `dataset_sha256`, frozen baseline/candidate descriptors, `independent`, `synthetic`, `policy_version`, `per_model`, `coverage`, `campus_comparison`, `limitations`, and per-example `predictions`. `EvaluationService.report_checksum(report_id)` returns the terminal report JSON SHA256 only after checking its immutable checksum sidecar; `load_report()` performs the same check for terminal reports. A caller should persist this checksum alongside the reviewed report ID and model IDs. Cancelled, failed, or interrupted reports contain null metrics; partial predictions are retained where available and also receive terminal checksums.

The first attempt to evaluate a content-addressed final-test set with an ordered baseline/candidate ID pair permanently binds both model policy versions in `final_test_bindings.json`, including attempts that later fail or are cancelled. A subsequent attempt for that same set content and model pair with a changed policy version fails before predictions. Reimporting the same reviewed records under a new set ID does not bypass the binding: `dataset_sha256` excludes import time and storage/source paths, but covers the reviewed labels, crops, provenance, image hashes, and independence declaration. Set loading checks the frozen manifest digest, this content identity, an image-inventory digest, and each original image hash. Model pairs with different descriptor IDs have distinct bindings, while the evaluation service still makes no threshold or activation decision. The checksum sidecar detects report-only edits; it is not a signature, so activation review must pin the returned report checksum outside the evaluation store and compare that pin later.

Sets are frozen byte-identical snapshots in owner-only directories under `data/uniform_evaluations` by default. Reports are atomic JSON snapshots there. Synthetic sets or model descriptors mark the report experimental and ineligible for real-data accuracy claims. Cancelled or failed evaluations preserve partial prediction evidence and contain no metrics for incomplete runs.
