# Unknown Sightings workspace

Open **Unknown Sightings** in the admin/superadmin sidebar. Attendance and Records → Attendance → Unknown / Unrecognized use the same `AttendancePanel` implementation. Select one row with the mouse or keyboard to preview that database event's saved evidence. Multiple selection is reserved for export and clears the preview. Click the image or **Enlarge saved evidence** for a larger view of the same decoded evidence.

The workspace replaces the activity sidebar while visible. The split is draggable; below 900 content pixels it stacks vertically. Dates use Asia/Manila (UTC+08:00). Today, Last 7 days, All time, recorded camera and reference/presence search filter encounter records, not unique people. Automatic refresh runs every ten seconds while visible, uses applied filters, and preserves the selection/image when that event remains on the current page. Export all matching includes all pages. Session writer counts are separate from the historical matching count.

## Evidence and migration

`security_events` remains the only encounter store. The additive, repeat-safe migration adds `context_snapshot` (BLOB) and `evidence_metadata` (TEXT); existing IDs, keys, crops, timestamps and legacy duplicates are retained. Both images and metadata commit in the same insert. A retry with the same event key returns the original row without replacing evidence.

New encounters encode an unannotated context image from the exact qualified `MonitorTask.frame` used for the face crop. Metadata records the original frame ID, camera generation, face box, original dimensions and saved context dimensions; the existing session/source/timestamp fields are retained. No newer camera pixels or enrollment images are used. All existing confirmation, ambiguity, freshness, cancellation and deduplication checks remain in place.

Context JPEGs are quality 80, at most 1280 pixels on the longest edge and 1,000,000 encoded bytes. Existing crop behavior remains quality 85, at most 1024 pixels on the longest edge and 2,000,000 encoded bytes. Oversize/failed encodings are omitted, never replaced from another frame. The writer permits 64 queued facts plus one active fact: at most 195 MB of encoded image payload, plus metadata/object overhead. The producer temporarily holds its task frame and encoding buffers. Encoding and database writes remain on pipeline/writer threads.

Context is preferred for new records; Face crop selects the saved crop. Legacy records display their saved crop unchanged. Records without evidence explicitly say no snapshot was saved. No historical backfill occurs. Missing camera metadata says Camera not recorded. Snapshot access (both kinds), queries and exports retain the admin/superadmin data-layer checks, including rejection of student sessions.

One background preview operation is allowed at a time. A generation and event ID bind the image to selection; changing selection immediately clears the old image and enlargement. When an old operation finishes, only the newest selection is loaded. Navigation, filtering and destruction invalidate obsolete results. Tk image creation and rendering occur on the main thread. Decoding rejects images above 25 million pixels and downsizes the display copy to 1280×960 without changing stored bytes.

## Verification

Disposable databases and distinct fixture images exercise event association with repeated presence IDs and different cameras, restart persistence, crop-only legacy rows, missing/corrupt evidence, rapid selection races, refresh preservation, pagination, exports, permission denial, repeat migration and original-evidence retention on retry. A synthetic newer live frame test verifies both saved encodings still originate from the qualified older frame. Camera/attendance/evidence regressions cover the unchanged qualification and cancellation rules.

Final regression runs passed 119 sightings/attendance/evidence/camera tests and 24 additional startup/camera-transition tests. The six native workspace tests were rerun after the final refresh-selection change and passed.

Native test windows were visually inspected at 1200×850 and 700×820; the latter stacks the preview beneath the table. Fixtures use synthetic images, not production evidence. No production database or camera was opened for these checks.

## Physical-camera check — user participation required

1. Sign in as admin and start Live Monitor on a recorded camera. Have a consenting person who does not match a registered identity remain visible until an unknown encounter is saved.
2. Open Unknown Sightings, select that encounter, and compare the context/crop, time and camera against the observed detection. Record its encounter reference.
3. Have the person leave or change position. Switch the live camera. Reopen the same reference and confirm the saved image has not changed.
4. Restart CBVMS, locate the same reference and verify the identical context/crop again. Check an older crop-only row and a no-image legacy row as well.

This physical check has not been performed. It needs the user's camera and an observer. No commit or push was made.
