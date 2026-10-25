-- Provider-admin only. The student HTTP API has no write route to this ledger.
-- A row is both the durable operation acknowledgment and permanent access fence.
CREATE TABLE portal_student_revocations (
 operation_id TEXT PRIMARY KEY NOT NULL,
 student_id TEXT NOT NULL UNIQUE,
 import_id TEXT NOT NULL REFERENCES campus_portal_imports(import_id),
 source_checkpoint TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,
 expected_username TEXT NOT NULL,
 expected_credential_version INTEGER NOT NULL,
 expected_version INTEGER NOT NULL CHECK(expected_version=0),
 version INTEGER NOT NULL CHECK(version=1),
 revoked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
--> statement-breakpoint
CREATE TRIGGER validate_student_revocation BEFORE INSERT ON portal_student_revocations
BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM campus_portal_imports i, json_each(i.mapping_json) m
  JOIN portal_students s ON s.id=m.key
  JOIN portal_accounts a ON a.student_id=s.id
  WHERE i.import_id=NEW.import_id AND i.source_checkpoint=NEW.source_checkpoint
   AND i.payload_sha256=NEW.payload_sha256 AND m.key=NEW.student_id
   AND a.username=NEW.expected_username AND a.credential_version=NEW.expected_credential_version
 ) THEN RAISE(ABORT,'Revocation identity or version conflict') END;
END;
--> statement-breakpoint
CREATE TRIGGER apply_student_revocation AFTER INSERT ON portal_student_revocations
BEGIN
 UPDATE portal_students SET active=0 WHERE id=NEW.student_id;
 DELETE FROM portal_sessions WHERE student_id=NEW.student_id;
END;
--> statement-breakpoint
CREATE TRIGGER immutable_student_revocation_update BEFORE UPDATE ON portal_student_revocations
BEGIN SELECT RAISE(ABORT,'Separate authorized restoration required'); END;
--> statement-breakpoint
CREATE TRIGGER immutable_student_revocation_delete BEFORE DELETE ON portal_student_revocations
BEGIN SELECT RAISE(ABORT,'Separate authorized restoration required'); END;
--> statement-breakpoint
-- Preserve imported history and pending credentials against stale campus upserts.
CREATE TRIGGER preserve_revoked_student BEFORE UPDATE ON portal_students
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=OLD.id)
 AND NOT (OLD.active=1 AND NEW.active=0 AND NEW.id=OLD.id AND NEW.email=OLD.email
          AND NEW.name=OLD.name AND NEW.snapshot=OLD.snapshot AND NEW.synced_at=OLD.synced_at)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_revoked_student BEFORE DELETE ON portal_students
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'Archived history must be preserved'); END;
--> statement-breakpoint
CREATE TRIGGER retain_revoked_account_update BEFORE UPDATE ON portal_accounts
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=OLD.student_id)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_revoked_account_delete BEFORE DELETE ON portal_accounts
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=OLD.student_id)
BEGIN SELECT RAISE(ABORT,'Archived account must be preserved'); END;
--> statement-breakpoint
CREATE TRIGGER deny_revoked_session BEFORE INSERT ON portal_sessions
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=NEW.student_id)
BEGIN SELECT RAISE(ABORT,'Account archived'); END;
--> statement-breakpoint
CREATE TRIGGER preserve_revoked_action_update BEFORE UPDATE ON portal_actions
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=OLD.student_id)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER preserve_revoked_action_delete BEFORE DELETE ON portal_actions
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=OLD.student_id)
BEGIN SELECT RAISE(ABORT,'Pending submissions must be preserved'); END;
--> statement-breakpoint
CREATE TRIGGER retain_revoked_student_insert BEFORE INSERT ON portal_students
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=NEW.id)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_revoked_account_insert BEFORE INSERT ON portal_accounts
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=NEW.student_id)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER immutable_campus_import_update BEFORE UPDATE ON campus_portal_imports
BEGIN SELECT RAISE(ABORT,'Import receipt is immutable'); END;
--> statement-breakpoint
CREATE TRIGGER immutable_campus_import_delete BEFORE DELETE ON campus_portal_imports
BEGIN SELECT RAISE(ABORT,'Import receipt is immutable'); END;
--> statement-breakpoint
CREATE TRIGGER prevent_revocation_replace BEFORE INSERT ON portal_student_revocations
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE operation_id=NEW.operation_id OR student_id=NEW.student_id)
BEGIN SELECT RAISE(ABORT,'Revocation receipt already exists; recover it'); END;
--> statement-breakpoint
CREATE TRIGGER prevent_import_receipt_replace BEFORE INSERT ON campus_portal_imports
WHEN EXISTS(SELECT 1 FROM campus_portal_imports WHERE import_id=NEW.import_id)
BEGIN SELECT RAISE(ABORT,'Import receipt already exists; recover it'); END;
--> statement-breakpoint
CREATE TRIGGER deny_revoked_action_insert BEFORE INSERT ON portal_actions
WHEN EXISTS(SELECT 1 FROM portal_student_revocations WHERE student_id=NEW.student_id)
BEGIN SELECT RAISE(ABORT,'Account archived'); END;
