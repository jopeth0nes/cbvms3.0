-- Create-only bridge ownership; existing portal accounts are never claimed.
CREATE TABLE IF NOT EXISTS campus_registration_owners (
 student_id TEXT PRIMARY KEY NOT NULL REFERENCES portal_students(id),
 source_id TEXT NOT NULL,
 lifecycle_version INTEGER NOT NULL CHECK(lifecycle_version>=0),
 active INTEGER NOT NULL CHECK(active IN (0,1)),
 access_blocked INTEGER NOT NULL DEFAULT 0 CHECK(access_blocked IN (0,1))
);
