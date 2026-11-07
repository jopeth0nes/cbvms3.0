# Stage 4 candidate observation workflow

Stage 4 does not activate a model. Normal startup still uses the unchanged active
bundle. Observation mode uses a separately pinned, validated Stage 2 candidate and
blocks attendance, security, disciplinary and notification/audio side effects in
the live processor. The preview-only secondary camera remains preview-only.

The current real catalog has no approved examples and there is no real candidate.
The retained synthetic candidate is only suitable for software/timing smoke tests.
It cannot demonstrate garment accuracy or meet activation requirements.

## Explicit configuration

Create a JSON file outside a model bundle. No repository configuration is enabled
automatically. Use the candidate's verified weight SHA256, and an explicitly chosen
recognition confidence. The following values are **smoke-test values only**, not
accuracy criteria or recommended production settings:

```json
{
  "schema": 1,
  "mode": "observation_only",
  "candidate_bundle": "/absolute/path/to/completed/candidate",
  "candidate_sha256": "the_64_character_candidate_weight_sha256",
  "recognition_confidence": 0.8,
  "backend": "cpu",
  "assessment_zone": [0.0, 0.0, 1.0, 1.0],
  "policy": null
}
```

Run from the repository:

```sh
CBVMS_ASSESSMENT_OBSERVATION_ONLY=1 \
CBVMS_UNIFORM_OBSERVATION_CONFIG=/absolute/path/to/observation.json \
.venv/bin/python main.py
```

The second variable without observation-only mode is rejected. The config is read
once; changing model, policy, thresholds or zone requires stopping and restarting
the observation session. Loaded weights and preprocessing are a frozen in-memory
snapshot. The config and bundle are never written by observation inference.

Candidate MPS is opt-in (`"backend": "mps"`). Parameter placement is verified and
an unavailable/failing MPS classifier reports failure rather than quietly running
on CPU. The existing body detector separately supports
`CBVMS_BODY_YOLO_BACKEND=cpu|mps` and discloses fallback through its existing backend
status/diagnostics. Face recognition stays on its existing ONNX CPU path.

Use one monitoring app and no concurrent training/benchmark job for measurements.
Model loading/warmup and inference run outside Tk. Loading is excluded from warm
inference measurements and may be much slower. Body/face/candidate work shares the
machine with rendering and ordinary OS activity; component latency does not imply
end-to-end latency. The observation config forces the existing bounded, fresh-frame
router; it does not start a second classifier or camera-analysis owner.

## Evidence, outcomes and policy

The existing tracker supplies same-frame face/body/torso boxes. Evidence is scoped
by camera ID, monitor cancellation/session token, generation, camera generation,
track ID, presence ID and the pinned candidate/config versions. Disappearance,
reassignment, association changes, bad crops, stale frames, source changes,
identity changes, model/config restart and analysis failure revoke evidence.

The cache holds at most 48 passages; the existing router/worker retain bounded
frames/queues. Default frame/evidence TTL is 1 second; reliable evidence can be
reused for at most 0.5 seconds before fresh inference is attempted. Uncertain
classifications are retried on a new usable frame. An unusable frame immediately
reports `Unable to assess`; it does not wait indefinitely for a perfect frame.

Outcomes are `Campus Uniform recognized`, `Organizational Shirt recognized`,
`Other clothing recognized`, or `Unable to assess`. Unknown identity does not turn
garment recognition into other clothing. Garment results do not feed the legacy
binary violation confirmation or disciplinary writer. Existing identity/earring
logic and alert/passage deduplication remain in place.

Geometry/overlap checks and the existing skin guard reject invalid associations
and possible obstruction. Default quality gates use the Stage 1 heuristics:
minimum torso side 160 px and Laplacian variance 25 at no more than 512 px. These
are conservative review heuristics, **not validated accuracy thresholds**, and
cannot detect every obstruction. No mirror flip or smaller classifier input is
introduced; inference remains `trainer-letterbox-rgb-0-1-v1`, 224 px, RGB [0,1].

`policy: null` means policy is not configured; no violation is inferred. Optional
policy must exactly match the existing Stage 3 `garment-policy-v1` rule, with an
explicit `minimum_confidence`, `kind: garment_probability_sum` and
`accepted_classes: [campus_uniform, organizational_shirt]`. This is only global
garment-policy observation. No department/day/schedule garment-permission engine
was found; those permissions remain explicitly unassessed even with this rule.
Recognition and policy uncertainty remain separate. Nothing activates the candidate.

## Reproducible measurements

Raw model stages (same explicit images/configuration on both devices):

```sh
.venv/bin/python scripts/benchmark_uniform_backends.py \
  --candidate /absolute/path/to/candidate \
  --images /absolute/path/to/consented-image.jpg \
  --backend both --warmup 4 --repeats 30 \
  --output output/uniform-stage4/backend-benchmark.json
```

For bounded generated-raster integration with scripted localization and real
tracking/router/worker/candidate inference:

```sh
.venv/bin/python scripts/smoke_uniform_observation.py \
  --candidate /absolute/path/to/candidate --backend both --passages 10 \
  --output output/uniform-stage4/passage-smoke.json
```

This deliberately includes unusable torsos. It opens no database or camera. Its
passages are synthetic; neither its labels nor its latency are real-world evidence.

For consented footage, extend the existing per-frame JSONL annotations with
optional, reviewed per-passage `garment_label`, `policy_label`, `zone_entry_ms` and
`first_usable_torso_ms`. Times are absolute milliseconds on the video timeline.
`garment_label` uses stable IDs `campus_uniform`, `organizational_shirt`,
`other_clothing`; policy labels are independently reviewed `compliant`/`violation`.
Do not derive labels from filenames or predictions. Keep `passage_alias` stable
within a passage and use a new alias for another passage. Conflicting annotations
are rejected. Existing `face_box` annotations join results to the correct person.

```sh
.venv/bin/python scripts/replay_camera_assessment.py \
  --video /absolute/path/to/consented-passage.mp4 \
  --annotations /absolute/path/to/reviewed-passages.jsonl \
  --mode candidate --uniform-observation-config /absolute/path/to/observation.json \
  --body-backend cpu --max-frames 1800 \
  --output output/uniform-stage4/candidate-replay-cpu.json
```

Run CPU and MPS sequentially on identical footage/annotations/zone/confidence and
224 px preprocessing, changing only the backend settings. Report hashes, versions,
warmup, hardware, thread settings, actual devices/fallback, skipped frames, counts
and missing labels. Replay initializes an empty disposable roster by default; an
explicit authorized evaluation DB is copied to a temporary DB before use. It never
writes production records. Garment evaluation does not require an identity gallery.

Reports distinguish first decision (including inability to assess) from first
recognition; a fast abstention is not a successful recognition. Both annotated
zone-entry→decision and first-usable-torso→decision distributions include sample
counts, median and p95. Missing annotations yield null timings, not invented zeroes.
Queue delay measures time actually spent waiting in the assessment worker queue;
capture-to-decision also includes localization, routing and preceding analysis.
Missed/unassessed passages are separate from completed inferences. Policy false/
missed violations are scored only on assessed policy outcomes with policy ground
truth; policy abstentions have their own count.

500 ms from the first usable torso is an initial target, not a guarantee. Real
passage footage, reviewed candidate data, independent labels and physical-camera
coverage are still missing. Smaller input sizes were not adopted or evaluated.

## Manual acceptance and rollback

1. Start an explicitly configured observation session. Check the candidate hash,
   actual backend and synthetic-model warning in the model status. Production
   activation must remain unchanged.
2. On one consented primary camera, enter/leave the configured zone, briefly obscure
   the torso, move out of frame and return. Check fresh evidence can resolve
   uncertainty and no old person's garment follows a replacement track.
3. Switch sources, open/close the preview pop-out, and scroll with two fingers.
   Confirm the secondary camera remains preview-only, the UI stays responsive,
   and a logical passage updates without duplicate alert cards or saved logs.
4. Verify policy-unconfigured text; inspect persistence only in disposable tests.
   Do not use the synthetic candidate to approve model accuracy or activation.

Stop the observation app and unset the two observation environment variables to
return to ordinary startup. No production-model rollback is needed. For code-only
rollback use the Stage 4 checkpoint's scoped patch after reviewing later edits;
retain all original datasets, bundles and previous fixes.
