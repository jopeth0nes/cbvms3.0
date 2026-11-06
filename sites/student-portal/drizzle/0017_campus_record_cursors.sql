-- Per bridge-owned student, never a roster-wide replacement.
CREATE TABLE IF NOT EXISTS campus_record_cursors (
 student_id TEXT PRIMARY KEY NOT NULL REFERENCES portal_students(id) ON DELETE CASCADE,
 lifecycle_version INTEGER NOT NULL,
 sequence INTEGER NOT NULL CHECK(sequence>0)
);
