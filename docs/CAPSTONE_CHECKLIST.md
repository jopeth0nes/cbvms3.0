# Capstone checklist implementation

Items 2 and 3 are one appeal photo verification feature.

## Training references

Open Training → DYCI Logo & Digital Shirts to save approved logo artwork and
digital shirt designs. PNG transparency and full resolution are preserved in
`data/uniform_references/`, with source and saved-file hashes in JSON sidecars.
Open reference folder to inspect the saved images.

Camera uniform training photos now retain their aspect ratio and up to 1600 pixels
on the longest side, instead of being permanently reduced to 224×224 at ingestion.
This retains detail for subsequent torso crops. Existing photos are unchanged.
The model still resizes its inputs as before. Use actual uniform photos with
visible logos and diverse lighting and angles for training; artwork is stored
separately and is not automatically added to the camera dataset. No accuracy
improvement has been measured on campus footage yet.

## Appeal photo verification (combined items 2 and 3)

The existing forensic analyzer now reads original capture date and UTC offset
from nested phone EXIF tags. Appeal case review displays the photo's claimed date,
the difference from the detection time, and missing/invalid timestamp information.
A claimed capture time more than five minutes after server upload, or a modification
date preceding capture, requests human review. This tolerance allows ordinary
clock drift; it is not a manipulation probability or a disciplinary rule.

Dates without timezone offsets require manual comparison. Missing EXIF is
inconclusive. The details view includes extracted metadata. Existing SHA-256
checks continue to withhold analysis when the stored bytes differ from the baseline.
Hashes protect evidence after upload; they cannot authenticate editable metadata
supplied before upload. Decisions remain with staff. Existing analyses can be
rerun with the case's Re-run button to extract newly supported tags.

Exit-camera corroboration remains pending, as marked in the original checklist.

## Face-to-face appeals

Open Appeals → Record face-to-face appeal. Enter the student ID, violation record
ID, the student's explanation, and meeting notes describing the evidence reviewed.
The system records the staff username and server timestamp. A photo upload is
optional for this staff-recorded path; student online submissions still require
picture evidence. The normal ownership, deadline, duplicate, and decision rules
apply. This records a pending appeal; it does not immediately dismiss a violation.

An additive, repeat-safe `in_person_appeals` table holds meeting audit records.
Existing appeals and evidence are preserved.

## Reports

Semester / Term retains stable term IDs and academic-year dependent options.
Two new multi-select filters, Violation category and Enrollment at detection,
combine with the existing semester, academic year, student, date, course, section,
status, and appeal filters. Apply filters before exporting; CSV and print use the
same applied snapshot. Clear means no matches; All means unrestricted.

## Verification

Synthetic tests cover nested EXIF, timezone handling, future dates, reference
preservation, uniform sample resolution, staff authorization, cross-student
ownership, duplicate/deadline rejection, repeated migration, approval, and combined
semester/category filters. Desktop tests exercise reference-tab construction and
face-to-face form submission through opening the pending case. Existing report,
appeal, and forensics regression tests were also run against disposable databases.
Physical-camera performance and detection accuracy require real deployment data.
