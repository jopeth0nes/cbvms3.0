# Stage 5 logo annotation backend evidence

## Implemented

- Reference artwork blocks garment approval even when `logo_reviewed=False`; its unreviewed `logo_annotation` draft is retained across default reviews. A correction is permitted only through an explicit `photographed_print` review with confirmed torso association.
- Every explicit or source-kind draft annotation is deep-copied into `review_history`. Each version records schema, reviewed state, visibility, box, source kind, association confirmation, image hash, torso crop, category, preprocessing version, provenance, and timestamp.
- Readiness rejects malformed coordinates and annotations whose current box or visibility differs from their reviewed state. Known capture groups and unknown-provenance coverage count only eligible visible photographed-print examples.

## Verification

Command:

```sh
.venv/bin/python -m unittest tests.test_uniform_logo_annotations tests.test_uniform_dataset -v
```

Result: **36 tests passed** (14 logo-annotation tests and 22 Stage 1 dataset regression tests). Full output is retained in [`annotation-tests.log`](annotation-tests.log). `git diff --check` passed for the owned code and evidence paths.

No production catalog, model, training run, or actual archive was accessed.

## SHA-256

- `core/uniform_dataset.py`: `669a08654f8c1ae8fd5270ded548bcc7c92c8784cd4c18a27275675227a978bc`
- `core/uniform_logo_annotations.py`: `88b606b4260bb5da2d066f4d2d92efa0dcf179d672ebab48345a986c44c1ca92`
- `tests/test_uniform_logo_annotations.py`: `93dba3db209a7af1786a735cc9348c161ab1ab53e7e4929dfb3d38584dfc7997`
- `output/uniform-stage5/annotation-tests.log`: `993f6c8b39dd8e7fa6ca21acbc11fd0a76642b04f5b7ba4f3c42b47414c6f0a0`
