# Appeal evidence association investigation

## Proven finding and historical limits

The local database was available and inspected using SQLite `mode=ro`. Appeal **3** belongs to student **2023-00883** and points to violation **65**, detected on **September 26**. Its original image is read from that exact violation. The link and the image bytes match `data/backups/cbvms-before-app-start-20261001T101725Z.db`, which predates the previous startup migration. This is a persisted older association, not evidence that a newly loaded October 1 case was rendered with a September 26 image.

No historical click/request audit establishes which card the student selected or whether an earlier client routed a different selection. No independently recorded capture provenance establishes the original frame/person for these legacy photos. Therefore this investigation cannot attribute the historical link conclusively to a student selection, routing defect, or capture error. It does not relink Appeal 3 or replace any historical picture.

The backup shows violation 65 was auto-confirmed on October 1 at 09:04:19 Manila time, while violations 71–73 were pending review with no appeal deadlines under the former confirmation-first workflow. That explains why the older record could be appealable while the newer records were not. Only violation 71 existed when Appeal 3 was submitted; 72 and 73 were detected later. This supports a selection/eligibility explanation, but does not prove the student's interaction.

## Exact trace

All display times below are **Asia/Manila (UTC+08:00)**. Stored timestamps are UTC. Student ID for every row is `2023-00883`.

| Record | Detected | Published | Appeal submitted | Original bytes |
| --- | --- | --- | --- | ---: |
| Appeal 3 → violation 65 | Sep 26, 2026 09:04:19 | Not recorded (legacy) | Oct 1, 2026 16:08:45 | 48,618 |
| Violation 71 | Oct 1, 2026 16:01:16 | Oct 1, 2026 18:18:03 | None | 31,615 |
| Violation 72 | Oct 1, 2026 16:13:12 | Oct 1, 2026 18:18:03 | None | 39,189 |
| Violation 73 | Oct 1, 2026 16:20:43 | Oct 1, 2026 18:18:03 | None | 37,045 |

The October 1 publication times were assigned by the earlier `appeals_publication_v1` startup migration. They are not camera capture times. Violation 65 retains its historical October 6, 09:04:19 appeal deadline and `reconciliation_required` status.

SHA-256 of the actual stored bytes:

- Violation 65: `4fb5036ffda8e62711f058a1f404c5fc5765e943c9549e044d6c88314216c365`
- Violation 71: `ab711f720979dbc3f21caeaed0eba2e96dbf789821309c57c4c7c02dce71d171`
- Violation 72: `754c8d3b8a1b1f3ac6342e613cfae88fb7e4ce77fbfe4a538dc323c5c13931d4`
- Violation 73: `78b8d0f0d72e6a937f60d18a380179780e2b0a6c2a7c3f0ada481856d94d6dfb`
- Appeal 3 supporting evidence **2**, uploaded Oct 1 at 16:08:45, 402,564 bytes: `f633907e3fce98a4f241c8138409a0c64072163e4ee7d513b3ec2da35ffe5075`

These are audit-time hashes for legacy images, not historical integrity attestations. All 73 existing original images lack capture provenance; the audit copy found all originals/supporting images decodable. Successful decoding and matching a backup do not prove the correct person/event was captured. The three October 1 blobs exist, so no reconstruction is needed or attempted. An image that was never saved cannot be reconstructed from these records.

## Implemented safeguards

- `core/camera.py`, `ui/dashboard.py`: stamp wall-clock capture time alongside monotonic freshness time; camera session UUIDs are renewed on opening and differ across process restarts. No source URLs or credentials are stored in provenance.
- `core/live_pipeline.py`: retain the existing frozen frame copy and same-frame crop, add an assessment/frame-context equality guard, and persist session/frame, capture time, presence/track, face/body/torso/crop geometry, and frame dimensions. Recognition thresholds, association rules, freshness/motion guards, and cooldowns remain in place.
- `database/db_manager.py`: atomically save the original hash and provenance with the violation/publication. The database supplies the canonical violation ID, owner, detection time, and byte hash in provenance. Supporting images get their own hash and the exact submission timestamp. Ownership/eligibility still run under the submission write lock. Admin cases load their joined violation and supporting records in one read transaction. Decisions recheck exact violation/evidence keys inside the write transaction. Integrity failures block rejection and unappealed expiry from awarding a new strike; approval/dismissal remains available with a documented reason.
- `core/evidence_integrity.py`: shared exact-record decoding and integrity checks; original keys are `(violation, ID, SHA-256)`, supporting keys are `(appeal, appeal ID, evidence ID, SHA-256)`. Missing, unreadable, or mismatched images never fall back to profile photos, uploads, or another violation. Hashes verify bytes, not recognition accuracy or authenticity.
- `core/portal_state.py`, `ui/student_portal.py`: copied card selections and a fixed form ID survive refresh and failed submission/retry. The form explicitly says “You are appealing violation #…, detected on …”; its image and refreshed metadata come from the same owner-scoped record. Exact appeal queries replace the old page-index lookup, eliminating the race when new appeals shift pagination. Original/supporting viewers expose the exact source key; navigation and closure discard old windows/results.
- `ui/appeals_panel.py`: clear case state and image windows immediately; gate responses by request generation, appeal ID, and linked violation ID. Display both IDs, detection/publication/submission/decision dates, and source-specific image timestamps. Integrity warnings disable rejection, including after errors/retries. The backend revalidates even if bytes change after display.
- `ui/records_panel.py`: apply the same integrity checks in general record/evidence previews, clear stale selection previews, identify enlarged originals, and close them on changing violation selection.
- `core/discipline.py`: display explicit zone names and UTC offsets. `CBVMS_TIMEZONE`, then `TZ`, then the system zone determine display; unnamed local zones still show an explicit offset.

There is no cross-violation image cache. Images passed to enlarged viewers originate from the loaded case/record, and retain their exact evidence key.

## Migration and historical attention

The migration adds nullable `violations.snapshot_sha256`, `violations.snapshot_provenance`, and `evidence_files.file_sha256` columns. Legacy hashes/capture times are not invented or backfilled. The existing database was not initialized or modified during this investigation. A temporary SQLite backup was initialized twice with deadline processing disabled: all original columns, timestamps, bytes, links, strikes, suspensions, and decisions were preserved; repeat initialization was identical.

Reproduce the read-only audit and disposable preview:

```sh
.venv/bin/python scripts/audit_appeal_evidence.py data/cbvms.db \
  --appeal 3 --date 2026-10-01 \
  --compare-backup data/backups/cbvms-before-app-start-20261001T101725Z.db
```

No historical link repairs are proposed. Appeal 3/violation 65 still needs a deliberate administrative review of the intended record and its existing pending-appeal/active-strike conflict. Any relink requires authoritative association evidence, a preserved original, and a dedicated audited repair; chronological proximity alone is insufficient. The existing strike-reconciliation procedure is separate from relinking and was not executed here.

For a verified integrity mismatch, preserve the row and original bytes, compare with an authoritative backup/capture record, and document the disposition. Do not overwrite its hash to make the warning disappear or substitute a newer photograph. If the evidence cannot substantiate the violation, use the existing approve/dismiss operation with a reason. Automatic expiry will not finalize a record whose recorded original integrity fails.

## Verification

The deterministic new fixtures use red September 26 image A and blue October 1 image B for the same student. Assertions cover exact submitted IDs, original bytes/hashes, native form previews, failed upload/retry, student enlarged viewer, admin case/enlargement/refresh/reopening, older record preservation, exact pagination/notification lookup, wrong-owner access, two people with distinct crop bytes, camera buffer mutation, session identity, cross-frame rejection, stale/out-of-order payloads, navigation during image loading, corrupt/missing/hash-mismatched evidence, transactional decision guards, expiry hold, rollback, and additive migration preservation.

Existing tests continue covering five-day publication windows, mandatory explanation/image, pending-appeal protection, single rejection/expiry strikes, approval, and suspension clearance. Native tests operate real CustomTkinter widgets and worker queues on macOS with disposable SQLite databases; capture/model tests use synthetic frames and doubles. No physical-camera recognition accuracy is claimed.

Final results:

- `.venv/bin/python -m unittest discover -s tests`: **377 tests passed in 82.221 seconds**, no failures/errors/skips reported.
- Final focused native/backend run: **35 tests passed in 44.111 seconds** (before the final capture/session metadata consistency assertions, which are included in the full passing run).
- `python -m compileall -q core database ui scripts/audit_appeal_evidence.py`: passed.
- `git diff --check`: passed.
- Read-only real-data audit/disposable migration preview: existing values preserved **true**, idempotence **true**, backup link/bytes match **true**, proposed relinks **none**.

The debug loop caught and corrected two old full-suite assertions tied to the former missing-image wording and private image widget field; the replacement assertion checks the exact violation ID and encoded-byte SHA-256. Earlier focused UI tests also needed to wait through the intentionally cleared case state during refresh. No behavioral failures remain in the final suite.

## Manual walkthrough

1. Restart the app to load the updated code and additive schema. As the student, open **My Violations** and locate the intended October 1 detection by **violation ID and detected time**. Check its publication deadline separately.
2. Choose **Submit Appeal**. Verify the explicit selection sentence and original image; enlarge it. Attach a supporting image and explanation. A validation failure must leave the same violation ID selected.
3. In admin **Appeals**, open the new appeal. Verify both IDs, the same original image, the separately timestamped supporting upload, and all four dates. Enlarge, refresh, switch cases, and reopen.
4. Open historical Appeal 3: it must continue to show **violation 65 / September 26**, with “capture time not recorded.” Do not relabel this as October 1 evidence.
5. On a disposable fixture only, corrupt original bytes without updating the stored hash. Refresh: the original must be withheld, rejection disabled, and the resolution instructions visible. Restore neither evidence nor links by guessing.
