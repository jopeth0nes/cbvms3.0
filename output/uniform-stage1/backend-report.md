# Stage 1 uniform dataset backend

## Result

Implemented an isolated JSON catalog at `data/uniform_stage1/catalog.json` with byte-preserving originals in `data/uniform_stage1/originals/`. Imports are review-first and have no connection to the trainer, production database, or active model artifacts.

`snapshot()` returns `schema_version`, `preprocess_version`, the stable category map, `records`, aggregate `counts` (including approved per-category counts), `experimental_training_eligible`, `readiness` with explicit eligibility reasons, and optional `proposed_splits`. UI labels are `Campus Uniform`, `Organizational Shirt`, and `Other Clothing`. A record contains the content hash and relative original path, source occurrences (including source kind, capture group, selected source category, legacy label, and ZIP archive hash), original raster dimensions/format/EXIF orientation, preprocessing version, category/status, crop source and logo fields, quality/review flags, notes, source and provenance groups, dHash/near-duplicate links, detector proposal history, and review history. Exact hashes share one stored original; repeated source imports retain provenance. A conflicting source label adds `label_conflict` and returns the record to pending review. Re-importing an unchanged source does not invalidate its reviewed status.

`review()` requires a valid original-raster crop and explicit status/category; an unassigned record cannot be approved. `crop_source` is `manual` by default or `detector` when accepting the exact stored proposal. History records preprocessing version and crop source for every review. Review flags can clear import-time association uncertainty or a conflict after human review. `image(..., crop=True)` uses recorded raster coordinates and does not apply EXIF orientation; EXIF orientation is recorded. Captures encode as PNG. Legacy audit imports old labels as `unassigned` and preserves the old label only as provenance.

ZIP preflight rejects path traversal, drive paths, symlinks, encrypted entries, high compression ratios, oversized files, excessive entry counts, compressed archive bytes over 200 MiB, and expanded contents over 200 MiB (including directory entries). `__MACOSX` metadata is skipped. Entry content is read under a strict limit without extracting paths; ZIP provenance retains the archive SHA256. Path/folder imports have equivalent per-file, count, and aggregate bounds. Catalog updates use a process/thread lock and atomic replacement; a corrupt catalog fails closed.

Near duplicates are conservatively linked incrementally by dHash Hamming distance <= 4 and marked for review. Split groups combine near-duplicate links, exact identity, source capture groups, and a shared unknown-provenance group across all catalog records. Proposed train/validation/evaluation partitions require at least three groups with approved records and known provenance across each related component; the split membership lists approved records only. Experimental training eligibility counts only approved, assigned records without ambiguity/conflict flags and reports reasons. Independent evaluation readiness and model activation remain false in Stage 1; eligibility is not an accuracy or activation claim.

## Verification

Command: `.venv/bin/python -m unittest tests.test_uniform_dataset -v`

Outcome: **19 tests passed**. Coverage includes exact-byte storage/dedup and provenance occurrences, incremental near-duplicate links and review flags, ZIP traversal/directory/drive rejection, archive hash and metadata skipping, legacy audit idempotence and label isolation, conflict invalidation vs unchanged-source idempotence, crop validation/original-raster/EXIF behavior, detector proposal provenance and human acceptance, unknown and known group split rules (including exact duplicates bridging capture groups and approved-only membership), eligibility flags/counts and human resolution of association uncertainty, lossless capture encoding, quality flags, lock release on open failure, and fail-closed catalog corruption. Earlier runs exposed two test assumptions that split proposals include pending data; fixtures now explicitly approve the eligible records.

## Limits for lead review

- ZIP validation and directory imports were exercised with disposable fixtures; no supplied real archive or production legacy tree was imported. The configured ZIP bounds cover the supplied archive's reported 150,478,162-byte expanded size.
- dHash is a review signal and conservative grouping heuristic, not a semantic duplicate detector.
- Detector proposals call only the repository's existing `PersonDetector`; local weights were not loaded or evaluated. If no detection is available, the proposal is `None`.
- The JSON catalog is intended for the current local review workflow, not concurrent multi-host writers.
