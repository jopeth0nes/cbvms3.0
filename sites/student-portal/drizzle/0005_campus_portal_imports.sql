CREATE TABLE `campus_portal_imports` (
  `import_id` text PRIMARY KEY NOT NULL,
  `source_checkpoint` text NOT NULL,
  `payload_sha256` text NOT NULL,
  `imported_at` text NOT NULL,
  `student_count` integer NOT NULL,
  `evidence_count` integer NOT NULL,
  `mapping_json` text NOT NULL
);
