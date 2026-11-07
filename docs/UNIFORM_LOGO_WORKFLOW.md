# Optional printed-logo verification — Stage 5

**Not trained. Disabled.** Production models and the torso-only baseline remain unchanged.
There are currently no approved photographed-logo annotations, trained logo bundles or
independent paired footage. Stage 5 therefore supplies annotation/configuration support
and tested scheduling/reporting contracts. It does not supply a working validated verifier,
logo localization/fusion model, OCR, or an activation route.

## Review actual photographed logos

1. Open **Training → Uniform Check → Organizational Shirt** (or the correct garment
   gallery), then **Review original & crop**. Keep collar, sleeves and garment body in
   the torso; verify the face/body/torso association, especially in group photographs.
2. Choose **Logo box** above the original and drag around the actual printed crest
   including department lettering when visible. Coordinates are original raster pixels,
   not classifier input pixels. The yellow box must stay inside this person's torso.
   **Preview logo from original** shows the selected pixels; only the display is scaled.
   Neither original nor derived pixels are mirrored automatically.
3. Choose `visible`, `obscured`, `too_small`, `back_view` or `uncertain`. Hidden or
   unreadable does not mean absent. Legacy `unknown`/`not_visible` becomes uncertain
   for interpretation; `occluded` becomes obscured. Reading the old catalog does not
   rewrite it. No negative label is generated from any visibility state.
4. For an actual photographed print choose `photographed_print`; confirm the correct
   torso association and the separate logo review checkbox. Visible reviewed logos need
   a box. Hidden states can be reviewed without a box. If unsure, leave pending.
   Review category and quality warnings separately; only confirm what the pixels support.
5. Save pending review, approve the garment when justified, or exclude it. Logo review
   does not automatically approve a garment. Reopening does not pre-confirm the logo;
   changing a crop/label/visibility requires renewed confirmation. Association, obstruction,
   blur, resolution and unresolved label flags exclude the logo from eligible-visible counts.

The colored reference image belongs in the existing **DYCI Logo & Digital Shirts**
library (`data/uniform_references`). It is artwork, not a photographed positive or negative.
If artwork was accidentally imported as a garment, choose `reference_artwork` and exclude
it. The backend blocks its approval even without the separate logo-review checkbox.
Correcting a mistaken artwork designation requires explicit photographed-print re-review
and confirmed association. No model trains automatically from either library.

Annotations live beside the original record in `data/uniform_stage1/catalog.json` under
`logo_annotation`, schema `printed-logo-v1`. Each version records original SHA256,
original-pixel box, torso crop, garment category, preprocessing version, review timestamp,
source kind, visibility, association confirmation and a provenance snapshot. Complete
versions remain in `review_history`. Derived crops are not independent captures.
`UniformDataset.logo_image(image_id)` returns original-resolution pixels only for a current,
approved, eligible photographed annotation after verifying the source hash. Crop/category,
source/provenance or subsequent review changes invalidate the old annotation's eligibility.
Counts and known/unknown group coverage are descriptive, not an accuracy threshold.

## Observation configuration and scheduling limits

The Stage 4 observation JSON may include this optional section:

```json
"logo_verification": {"enabled": false, "max_added_latency_ms": 100}
```

Omitting it keeps the verifier disabled. Setting `enabled` to true is rejected with
**Not trained** before any model is loaded. There is no model download, alternate
architecture, production switch, camera owner or OCR fallback. Configuration is part
of the existing observation version token. Missing logo evidence leaves the existing
torso result and policy outcome unchanged; uncertain torso evidence stays unassessed.

`LogoRequestGate` is a tested future-backend admission contract, not an executor.
It admits at most one request, queues none, requires uncertain torso classification plus
visible logo evidence, validates a box in the same original-resolution torso, and calls
the existing ticket-current check for camera/session/generation/track/revision freshness.
Results beyond the smaller of the frame deadline and configured added-time budget are
discarded. Invalidating a running request does not free its slot until completion is
acknowledged, preventing overlapping work. It retains no image or independent evidence
cache. A future backend must reuse existing passage ownership and invalidation checks.

The default 100 ms is an operational admission budget, **not a measured speed or acceptance
threshold**. The allowed range is greater than 0 through 1000 ms. A future model executor
also needs cooperative cancellation or process isolation: discarding late results does
not stop a hung inference call. No real logo execution or hard latency limit has been
validated in this stage. The current app never schedules a logo request.

## What to collect before training or enablement

- Original-resolution photographs of the white printed Organizational Shirt crest,
  including genuinely readable department lettering where available. Retain the whole
  associated torso, original bytes, camera/session and capture-group provenance.
- Campus Uniform and Other Clothing examples, including any carrying the same crest.
  Human-review garment category separately from logo presence. Include real comparison
  examples; do not manufacture negatives, infer labels from filenames or use artwork as data.
- Reviewed visible, obscured, too-small, back-view and uncertain cases across the intended
  cameras, lighting, distances, pose and motion conditions. Keep all same-session captures,
  near-duplicates and crops together; unknown provenance cannot establish independence.
- Independent held-out footage with passage aliases, torso/logo associations, visibility,
  camera/lighting conditions, assessment-zone entry and first usable torso times. Separate
  training/development/final-test capture groups before augmentation. Freeze the final set.
- Independent uniform-policy ground truth where false/missed violations are to be measured;
  include relevant department/day/schedule rules. A shared crest is not a permission rule.
  Decide acceptable classification errors, false violations, unassessed rate and added
  latency **before** final evaluation. No count or threshold is supplied as proof of accuracy.

No fixed number of photos establishes sufficient data. Coverage, independent groups,
class balance and uncertainty must be assessed against the responsible reviewer's criteria.
The optional reference-art path is still missing; it is not the blocker for photographed
logo training. There is no reason to train from that artwork alone.

## Paired evaluation support

`core.uniform_logo_evaluation.compare_logo_runs` / `scripts/compare_uniform_logo.py` summarize
recorded passage exports; they do not perform inference or certify independence. When a
real verifier and compatible observation executor exist, run torso-only and torso-plus-logo
on the identical held-out frames, torso bundle, preprocessing, policy and hardware/settings.
Record actual backend/fallback and test conditions. Do not tune against final-test results.

The manifest has schema `1`, purpose `logo-held-out-comparison`, `reviewed: true`,
`footage_sha256`, and `passages`. Each passage has a unique `passage_alias`; optional
`garment_label`, `policy_label`, `logo_visibility`, `camera`, `lighting`, `capture_group`
must describe reviewed observations. Missing ground truth remains missing.

Both run exports must match `footage_sha256`, `frame_sequence_sha256`,
`annotation_manifest_sha256` (canonical JSON hash of that manifest), `torso_model_sha256`,
`torso_preprocessing`, `policy_version`, `configuration_sha256` (common execution settings,
excluding the logo toggle) and `test_conditions`. Pin the same `criteria_sha256` using
`core.uniform_model_registry.json_hash`. The criteria JSON requires nonempty reviewer text
for `classification_errors`, `false_violations`, `unassessed_rate` and `added_latency_ms`.

The `mode` values are `torso_only` and `torso_plus_logo`. Only the augmented export supplies
`logo_model_sha256` and `logo_preprocessing`. Each `results` entry is one final result per
passage with `passage_alias`, `label` (stable garment ID or null), `policy_outcome`
(`compliant`, `violation` or null) and measured `first_usable_to_decision_ms`,
`zone_entry_to_decision_ms`, `logo_ms` when actually available. Missing result rows count
as missed passages; null labels count as unassessed. Timing values must be nonnegative
and finite. This is a future export contract, not evidence that current replay produces
logo results; the current Stage 4 replay remains torso-only.

```sh
.venv/bin/python scripts/compare_uniform_logo.py \
  --manifest /path/to/reviewed-footage-manifest.json \
  --torso-only /path/to/torso-results.json \
  --torso-plus-logo /path/to/logo-results.json \
  --criteria /path/to/frozen-criteria.json \
  --output /path/to/new-comparison.json
```

The new output reports per-class precision/recall with denominators and intervals,
confusion matrices, classified errors separately from unresolved cases, explicit-policy
false/missed violations, missing/unassessed counts, timing median/p95 and paired added
latency. Signed latency differences retain improvements as negative values. Reports
always set `activation_allowed: false`: matching hashes alone cannot establish genuine
independence, truthful labels, acceptance criteria timing or a validated working verifier.

Manual acceptance should use a disposable review dataset first. Confirm the box/visibility
workflow, pending/approved separation, scrolling and close-during-load behavior, then
review real examples only when authorized. Physical camera and real model accuracy/latency
remain unverified. See `UNIFORM_UPGRADE_CHECKPOINT.md` and `output/uniform-stage5/report.md`.
