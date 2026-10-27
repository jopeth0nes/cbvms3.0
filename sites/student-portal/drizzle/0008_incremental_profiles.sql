-- Dormant until explicit administrative enrollment/reconciliation. No automatic adoption.
CREATE TABLE campus_devices (
 device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
--> statement-breakpoint
CREATE TABLE campus_actors (
 actor_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('admin','superadmin')),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
--> statement-breakpoint
CREATE TABLE campus_actor_sessions (
 token_hash TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES campus_actors(actor_id),
 device_id TEXT NOT NULL REFERENCES campus_devices(device_id), expires INTEGER NOT NULL
);
--> statement-breakpoint
CREATE TABLE campus_profile_grants (
 actor_id TEXT NOT NULL REFERENCES campus_actors(actor_id),
 student_id TEXT NOT NULL REFERENCES portal_students(id),
 PRIMARY KEY(actor_id,student_id)
);
--> statement-breakpoint
CREATE TABLE campus_shared_profiles (
 student_id TEXT PRIMARY KEY REFERENCES portal_students(id), version INTEGER NOT NULL CHECK(version>=0),
 name TEXT NOT NULL, email TEXT NOT NULL,
 -- Set only inside an atomic accepted command, not a persistent bypass flag.
 applying INTEGER NOT NULL DEFAULT 0 CHECK(applying IN (0,1))
);
--> statement-breakpoint
CREATE TABLE campus_profile_operations (
 operation_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES campus_devices(device_id),
 actor_id TEXT NOT NULL REFERENCES campus_actors(actor_id), session_hash TEXT NOT NULL, student_id TEXT NOT NULL REFERENCES campus_shared_profiles(student_id),
 expected_version INTEGER NOT NULL CHECK(expected_version>=0), payload TEXT NOT NULL CHECK(json_valid(payload)),
 payload_digest TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('accepted','conflict')),
 version INTEGER NOT NULL, result TEXT NOT NULL CHECK(json_valid(result)),
 committed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
--> statement-breakpoint
CREATE TABLE campus_profile_changes (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL UNIQUE REFERENCES campus_profile_operations(operation_id),
 student_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload))
);
--> statement-breakpoint
CREATE INDEX campus_profile_changes_student ON campus_profile_changes(student_id,cursor);
--> statement-breakpoint
CREATE TRIGGER immutable_profile_operation_update BEFORE UPDATE ON campus_profile_operations
BEGIN SELECT RAISE(ABORT,'Immutable operation receipt'); END;
--> statement-breakpoint
CREATE TRIGGER immutable_profile_operation_delete BEFORE DELETE ON campus_profile_operations
BEGIN SELECT RAISE(ABORT,'Immutable operation receipt'); END;
--> statement-breakpoint
CREATE TRIGGER validate_profile_operation BEFORE INSERT ON campus_profile_operations
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM campus_actor_sessions WHERE token_hash=NEW.session_hash AND actor_id=NEW.actor_id AND device_id=NEW.device_id AND expires>unixepoch()) THEN RAISE(ABORT,'Staff session expired') END;
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM campus_devices d, campus_actors a
 JOIN campus_profile_grants g ON g.actor_id=a.actor_id
 WHERE d.device_id=NEW.device_id AND d.enabled=1 AND a.actor_id=NEW.actor_id AND a.enabled=1 AND g.student_id=NEW.student_id)
 THEN RAISE(ABORT,'Profile permission revoked') END;
 SELECT CASE WHEN NEW.status='accepted' AND (EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=NEW.student_id AND (recycled=1 OR access_blocked=1))
 OR NOT EXISTS(SELECT 1 FROM portal_students WHERE id=NEW.student_id AND active=1))
 THEN RAISE(ABORT,'Student access restricted') END;
 SELECT CASE WHEN NEW.status='accepted' AND NOT EXISTS(SELECT 1 FROM campus_shared_profiles WHERE student_id=NEW.student_id AND version=NEW.expected_version AND NEW.version=version+1)
 THEN RAISE(ABORT,'Profile version changed') END;
 SELECT CASE WHEN NEW.status='conflict' AND NOT EXISTS(SELECT 1 FROM campus_shared_profiles WHERE student_id=NEW.student_id AND version=NEW.version) THEN RAISE(ABORT,'Conflict baseline changed') END;
END;
--> statement-breakpoint
CREATE TRIGGER apply_profile_operation AFTER INSERT ON campus_profile_operations WHEN NEW.status='accepted'
BEGIN
 UPDATE campus_shared_profiles SET name=COALESCE(json_extract(NEW.payload,'$.name'),name),
 email=COALESCE(json_extract(NEW.payload,'$.email'),email),version=NEW.version,applying=1 WHERE student_id=NEW.student_id;
 UPDATE portal_students SET
 name=(SELECT name FROM campus_shared_profiles WHERE student_id=NEW.student_id),
 email=(SELECT email FROM campus_shared_profiles WHERE student_id=NEW.student_id),
 snapshot=json_set(snapshot,'$.profile.name',(SELECT name FROM campus_shared_profiles WHERE student_id=NEW.student_id),
 '$.profile.email',(SELECT email FROM campus_shared_profiles WHERE student_id=NEW.student_id)) WHERE id=NEW.student_id;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM portal_students p JOIN campus_shared_profiles s ON s.student_id=p.id
 WHERE p.id=NEW.student_id AND p.name=s.name AND p.email=s.email
 AND json_extract(p.snapshot,'$.profile.name')=s.name AND json_extract(p.snapshot,'$.profile.email')=s.email)
 THEN RAISE(ABORT,'Profile projection not applied') END;
 INSERT INTO campus_profile_changes(operation_id,student_id,version,payload)
 SELECT NEW.operation_id,student_id,version,json_object('entity_type','student','entity_id',student_id,'version',version,'name',name,'email',email)
 FROM campus_shared_profiles WHERE student_id=NEW.student_id;
 UPDATE campus_shared_profiles SET applying=0 WHERE student_id=NEW.student_id;
END;
--> statement-breakpoint
-- Keep the lifecycle snapshot fence, allowing only an exact profile-only mutation
-- from a version-checked command. No active bit, credentials or history changes.
DROP TRIGGER retain_lifecycle_snapshot;
--> statement-breakpoint
CREATE TRIGGER retain_lifecycle_snapshot BEFORE UPDATE ON portal_students
WHEN EXISTS(SELECT 1 FROM portal_student_lifecycle WHERE student_id=OLD.id AND applying=0)
 AND (NEW.id<>OLD.id OR NEW.email<>OLD.email OR NEW.name<>OLD.name OR NEW.snapshot<>OLD.snapshot OR NEW.synced_at<>OLD.synced_at)
 AND NOT EXISTS(SELECT 1 FROM campus_shared_profiles s WHERE s.student_id=OLD.id AND s.applying=1
 AND NEW.id=OLD.id AND NEW.active=OLD.active AND NEW.synced_at=OLD.synced_at
 AND NEW.name=s.name AND NEW.email=s.email
 AND NEW.snapshot=json_set(OLD.snapshot,'$.profile.name',s.name,'$.profile.email',s.email))
BEGIN SELECT RAISE(IGNORE); END;
--> statement-breakpoint
-- Adoption fences incompatible legacy publication. The API must reject the batch,
-- rather than silently acknowledging stale fields or losing non-profile history.
CREATE TRIGGER fence_shared_profile BEFORE UPDATE ON portal_students
WHEN EXISTS(SELECT 1 FROM campus_shared_profiles WHERE student_id=OLD.id AND applying=0)
 AND (NEW.name<>OLD.name OR NEW.email<>OLD.email OR NEW.snapshot<>OLD.snapshot)
BEGIN SELECT RAISE(ABORT,'Shared record requires compatible campus client'); END;

--> statement-breakpoint
CREATE TRIGGER immutable_profile_change_update BEFORE UPDATE ON campus_profile_changes
BEGIN SELECT RAISE(ABORT,'Immutable change journal'); END;
--> statement-breakpoint
CREATE TRIGGER immutable_profile_change_delete BEFORE DELETE ON campus_profile_changes
BEGIN SELECT RAISE(ABORT,'Immutable change journal'); END;
