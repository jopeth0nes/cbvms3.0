-- Provider-admin dispatcher only; no student or sync HTTP route can write here.
CREATE TABLE portal_lifecycle_authorities (
 authority_id TEXT PRIMARY KEY NOT NULL,
 source_checkpoint TEXT NOT NULL,
 actor_id INTEGER NOT NULL,
 actor TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role='superadmin'),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 UNIQUE(source_checkpoint,actor_id,actor)
);
--> statement-breakpoint
CREATE TABLE portal_student_lifecycle (
 student_id TEXT PRIMARY KEY NOT NULL REFERENCES portal_students(id),
 import_id TEXT NOT NULL REFERENCES campus_portal_imports(import_id),
 version INTEGER NOT NULL CHECK(version>0),
 recycled INTEGER NOT NULL CHECK(recycled IN (0,1)),
 access_blocked INTEGER NOT NULL DEFAULT 0 CHECK(access_blocked IN (0,1)),
 applying INTEGER NOT NULL DEFAULT 0 CHECK(applying IN (0,1)),
 operation_id TEXT NOT NULL
);
--> statement-breakpoint
CREATE TABLE portal_lifecycle_operations (
 operation_id TEXT PRIMARY KEY NOT NULL,
 student_id TEXT NOT NULL,
 import_id TEXT NOT NULL,
 source_checkpoint TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,
 expected_username TEXT NOT NULL,
 expected_version INTEGER NOT NULL CHECK(expected_version>=0),
 version INTEGER NOT NULL CHECK(version=expected_version+1),
 operation_type TEXT NOT NULL CHECK(operation_type IN ('recycle','restore')),
 authority_id TEXT NOT NULL REFERENCES portal_lifecycle_authorities(authority_id),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 committed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(student_id,version)
);
--> statement-breakpoint
-- A legacy revocation is conservatively independently blocked: its prior state
-- cannot be reconstructed. This migration does not change any student row.
INSERT INTO portal_student_lifecycle(student_id,import_id,version,recycled,access_blocked,operation_id)
 SELECT student_id,import_id,1,1,1,operation_id FROM portal_student_revocations;
--> statement-breakpoint
DROP TRIGGER validate_student_revocation;
--> statement-breakpoint
DROP TRIGGER apply_student_revocation;
--> statement-breakpoint
DROP TRIGGER preserve_revoked_student;
--> statement-breakpoint
DROP TRIGGER retain_revoked_student;
--> statement-breakpoint
DROP TRIGGER retain_revoked_account_update;
--> statement-breakpoint
DROP TRIGGER retain_revoked_account_delete;
--> statement-breakpoint
DROP TRIGGER deny_revoked_session;
--> statement-breakpoint
DROP TRIGGER preserve_revoked_action_update;
--> statement-breakpoint
DROP TRIGGER preserve_revoked_action_delete;
--> statement-breakpoint
DROP TRIGGER retain_revoked_student_insert;
--> statement-breakpoint
DROP TRIGGER retain_revoked_account_insert;
--> statement-breakpoint
DROP TRIGGER deny_revoked_action_insert;
--> statement-breakpoint
CREATE TRIGGER reject_legacy_revocation BEFORE INSERT ON portal_student_revocations
BEGIN SELECT RAISE(ABORT,'Use authenticated superadmin lifecycle operations'); END;
--> statement-breakpoint
CREATE TRIGGER validate_lifecycle_operation BEFORE INSERT ON portal_lifecycle_operations
BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM portal_lifecycle_operations WHERE operation_id=NEW.operation_id)
 THEN RAISE(ABORT,'Recover existing lifecycle receipt') END;
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM portal_lifecycle_authorities a JOIN campus_portal_imports i ON i.source_checkpoint=a.source_checkpoint,
  json_each(i.mapping_json) m JOIN portal_accounts p ON p.student_id=m.key JOIN portal_students s ON s.id=p.student_id
  WHERE a.authority_id=NEW.authority_id AND a.enabled=1 AND a.role='superadmin'
   AND i.import_id=NEW.import_id AND i.source_checkpoint=NEW.source_checkpoint AND i.payload_sha256=NEW.payload_sha256
   AND m.key=NEW.student_id AND p.username=NEW.expected_username
 ) THEN RAISE(ABORT,'Superadmin authority or import ownership mismatch') END;
 SELECT CASE WHEN COALESCE((SELECT version FROM portal_student_lifecycle WHERE student_id=NEW.student_id),0)<>NEW.expected_version
 THEN RAISE(ABORT,'Lifecycle version conflict; recover earlier operations') END;
 SELECT CASE WHEN (NEW.operation_type='restore' AND COALESCE((SELECT recycled FROM portal_student_lifecycle WHERE student_id=NEW.student_id),0)<>1)
 OR (NEW.operation_type='recycle' AND COALESCE((SELECT recycled FROM portal_student_lifecycle WHERE student_id=NEW.student_id),0)<>0)
 THEN RAISE(ABORT,'Lifecycle state conflict') END;
END;
--> statement-breakpoint
CREATE TRIGGER apply_lifecycle_operation AFTER INSERT ON portal_lifecycle_operations
BEGIN
 INSERT INTO portal_student_lifecycle(student_id,import_id,version,recycled,access_blocked,applying,operation_id)
 SELECT NEW.student_id,NEW.import_id,NEW.version,NEW.operation_type='recycle',1-active,1,NEW.operation_id FROM portal_students WHERE id=NEW.student_id
 ON CONFLICT(student_id) DO UPDATE SET version=NEW.version,recycled=NEW.operation_type='recycle',applying=1,operation_id=NEW.operation_id;
 UPDATE portal_students SET active=(SELECT CASE WHEN recycled=0 AND access_blocked=0 THEN 1 ELSE 0 END FROM portal_student_lifecycle WHERE student_id=NEW.student_id) WHERE id=NEW.student_id;
 DELETE FROM portal_sessions WHERE student_id=NEW.student_id;
 UPDATE portal_student_lifecycle SET applying=0 WHERE student_id=NEW.student_id;
END;
--> statement-breakpoint
CREATE TRIGGER immutable_lifecycle_receipt_update BEFORE UPDATE ON portal_lifecycle_operations
BEGIN SELECT RAISE(ABORT,'Lifecycle receipts are immutable'); END;
--> statement-breakpoint
CREATE TRIGGER immutable_lifecycle_receipt_delete BEFORE DELETE ON portal_lifecycle_operations
BEGIN SELECT RAISE(ABORT,'Lifecycle receipts are immutable'); END;
--> statement-breakpoint
CREATE TRIGGER retain_lifecycle_state BEFORE DELETE ON portal_student_lifecycle
BEGIN SELECT RAISE(ABORT,'Lifecycle identity must be retained'); END;
--> statement-breakpoint
CREATE TRIGGER retain_lifecycle_student_insert BEFORE INSERT ON portal_students
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=NEW.id)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_lifecycle_student_delete BEFORE DELETE ON portal_students
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'Recycle Bin history must be retained'); END;
--> statement-breakpoint
CREATE TRIGGER retain_lifecycle_snapshot BEFORE UPDATE ON portal_students
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.id AND applying=0)
 AND (NEW.id<>OLD.id OR NEW.email<>OLD.email OR NEW.name<>OLD.name OR NEW.snapshot<>OLD.snapshot OR NEW.synced_at<>OLD.synced_at)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER record_independent_access_block AFTER UPDATE OF active ON portal_students
WHEN NEW.active=0 AND EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=NEW.id AND applying=0)
BEGIN UPDATE portal_student_lifecycle SET access_blocked=1 WHERE student_id=NEW.id; END;
--> statement-breakpoint
CREATE TRIGGER protect_lifecycle_access BEFORE UPDATE OF active ON portal_students
WHEN NEW.active=1 AND EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.id AND (recycled=1 OR access_blocked=1))
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_recycled_account_insert BEFORE INSERT ON portal_accounts
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=NEW.student_id AND recycled=1)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_recycled_account_update BEFORE UPDATE ON portal_accounts
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.student_id AND recycled=1)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_lifecycle_account_delete BEFORE DELETE ON portal_accounts
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.student_id)
BEGIN SELECT RAISE(ABORT,'Recycle Bin identity must be retained'); END;
--> statement-breakpoint
CREATE TRIGGER deny_lifecycle_session BEFORE INSERT ON portal_sessions
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=NEW.student_id AND (recycled=1 OR access_blocked=1))
BEGIN SELECT RAISE(ABORT,'Account access restricted'); END;
--> statement-breakpoint
CREATE TRIGGER retain_recycled_action_update BEFORE UPDATE ON portal_actions
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.student_id AND recycled=1)
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
CREATE TRIGGER retain_lifecycle_action_delete BEFORE DELETE ON portal_actions
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.student_id)
BEGIN SELECT RAISE(ABORT,'Pending submissions must be preserved'); END;
--> statement-breakpoint
CREATE TRIGGER deny_recycled_action BEFORE INSERT ON portal_actions
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=NEW.student_id AND (recycled=1 OR access_blocked=1))
BEGIN SELECT RAISE(ABORT,'Account access restricted'); END;
