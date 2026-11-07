# Stage 3 — Evaluation & Models

Open **Training → Uniform Check → Evaluation & Models → Open Evaluation & Models**.
The existing gallery below remains available for ambiguous legacy examples. Campus
Uniform, Organizational Shirt, Other Clothing, Earring Check, and DYCI references
retain their existing workspaces. All model operations run outside Tk.

## Current prerequisites

The recovered real catalog has 963 pending examples and zero approvals: 36 unique
Organizational Shirt originals and 927 ambiguous legacy examples. There are no real
completed three-class candidates and no independent final-test set. Two retained
synthetic Stage 2 bundles are smoke artifacts only and cannot be activated. The
baseline has binary correct_uniform/wrong_uniform labels; those labels do not define
the three garment classes. Baseline training capture provenance remains incomplete.
There are no real accuracy, regression, camera-performance or activation claims.

Review original/crop, category, quality, association and actual capture provenance
before approving examples in Stage 1. Keep unknown groups unknown. Meaningful
training requires reviewed examples of both accepted garments and relevant Other
Clothing. Collect a separate held-out set from independent people/sessions and
conditions. Never use the final test set to tune weights, labels or policy confidence.
After tuning, collect a new independent final test set.

## Reviewed training provenance

Both models need a complete reviewed training inventory, including original-image
SHA256, original-raster 64-bit dHash, and actual capture groups. Unknown provenance
blocks the independence claim. Use **Import training provenance**; its model_id must
match the displayed active model or selected completed candidate's weight SHA256.
For a candidate, the imported SHA256 set must match exactly the frozen training
manifest, and its known groups must be retained. This is an explicit human audit;
the software cannot establish the historical completeness of a legacy inventory.
No labels are inferred from filenames. Inventories freeze on first import.

Example shape (placeholders must be replaced; do not fabricate values):

```json
{
  "model_id": "displayed-model-id",
  "complete": true,
  "reviewed_by": "responsible-reviewer",
  "statement": "Explain the source audit, complete training coverage and recovered capture provenance.",
  "training_hashes": ["64-lowercase-hex-SHA256-of-original"],
  "training_dhashes": ["16-lowercase-hex-dHash-of-original"],
  "training_groups": ["actual-session-identifier"]
}
```

The dHash algorithm is the existing grayscale 9×8 image resize followed by adjacent
column comparison, packed in row order into 64 bits. Preserve source records;
importing provenance changes only evaluation metadata, never a model or dataset.
If an already imported inventory is wrong, stop and audit it before any further
evaluation; do not replace it silently to remove overlap evidence.

## Independent evaluation

Use the exact schema in [UNIFORM_EVALUATION_MANIFEST.md](UNIFORM_EVALUATION_MANIFEST.md).
Import validates paths, bounds, image hashes, original-raster crops, approvals,
known capture groups and the independence declaration. Frozen images remain byte
identical; no automatic rotation or mirroring. Exact, group and conservative
near-duplicate training/test overlap with either model blocks evaluation.

Select candidate and test set, supply a confidence between 0.5 and 1, acknowledge
CPU contention, then **Evaluate candidate**. No operating point is recommended by
the application. That operating point is bound at first evaluation to the test set
and model pair; cancellation does not permit tuning it against the same final set.
Evaluation owns the existing candidate training slot. Pause monitoring if practical;
no additional camera is opened. Cancel waits for current inference to finish.
Closing the window requests cancellation; saved reports are recoverable on reopen.

Reports show real per-class precision/recall, confusion counts, denominators,
Wilson 95% intervals, unassessed rates, camera/lighting coverage and missing data.
The legacy baseline's Campus Uniform response is explicitly a binary response
proxy, not garment recall. It has no Organizational Shirt class. Policy false/missed
violation metrics require separately reviewed policy ground truth; garment-class
quality is distinct from policy correctness. null means undefined/unavailable.
The evaluated crops do not test face/body association, motion, camera capture,
identity, cooldowns or the full live pipeline. Those need physical-camera checks.

## Explicit activation and rollback

Only a currently valid superadmin staff session can change models. Activation is
never automatic. **Activate Candidate** requires the selected completed candidate,
unchanged preprocessing/policy, current active baseline, independent real-data
report, all three garment categories represented, compliant and violation policy
labels, and a written acceptance review. Those are completeness requirements, not
accuracy thresholds. The responsible reviewer must decide acceptance criteria and
assess uncertainty, Campus Uniform regression and missing camera/lighting coverage.
A confirmation dialog is the final user action. Synthetic candidates always fail.

Activation validates the complete original candidate, copies immutable deployment
artifacts, warms the compatible model, preserves the previous bundle, rechecks
session authorization, and replaces one atomic pointer containing a fresh generation.
Weights, mapping, preprocessing, policy, report and review move together. In-flight
old-generation results fail validation. Uniform evidence resets on the next frame;
identity, earrings, alerts and cooldowns are retained. No original active file is
overwritten. With no pointer, the original legacy assets continue to serve live use.
The legacy Ultralytics transform path is separately versioned from candidate direct
224px letterbox/RGB float preprocessing; no silent transform substitution is made.

**Roll Back** validates and warms the preserved compatible bundle and atomically
switches to it, keeping the displaced bundle as the previous version. A reason and
confirmation are required. **Check switch recovery** validates the committed pointer
and reconciles a prepared journal as committed or aborted; it never guesses a model
or activates an uncommitted staging directory. Incompatible/corrupt pointers fail
closed and require manual inspection. Record the journal and hashes before repair.
Filesystem integrity checks assume the local model store and application code remain
under the user's trusted permissions; checksums are not signatures.

Storage: real candidates `data/uniform_candidates/`; frozen sets/reports/provenance
`data/uniform_evaluations/`; immutable deployments `data/uniform_models/bundles/`;
sole deployment commit point `data/uniform_models/active.json`; recovery journal
`data/uniform_models/switch-journal.json`. No production pointer was created or
changed during this stage. Activation/rollback verification uses temporary roots.

## Manual acceptance

1. Open the named Organizational Shirt workspace; review an original/crop without
   approving an ambiguous example. Confirm other Training sections remain present.
2. Open Evaluation & Models. Confirm actual active version, unknown baseline data
   provenance, zero real candidates, and unavailable metrics. Actions remain blocked
   until their prerequisites exist; nothing reports fabricated accuracy.
3. Check two-finger vertical scrolling and navigation through the long model view
   and report. Close/reopen during a disposable evaluation to check cancellation and
   report recovery. Production activation is not part of this acceptance exercise.
4. In an isolated configuration with reviewed fixture reports, confirm activation
   confirmation, stale result rejection, rollback and interruption recovery. The
   automated temporary-root tests already cover these software contracts.
5. Check live camera switching/pop-out and current-session alerts manually with the
   existing production model. No physical-camera measurements were made here.
