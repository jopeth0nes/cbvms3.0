-- Provider-only audited projection updates. No student/sync HTTP issuer exists.
CREATE TABLE IF NOT EXISTS portal_academic_owner_updates (
 operation_id TEXT PRIMARY KEY,
 student_id TEXT NOT NULL REFERENCES portal_students(id),
 authority_id TEXT NOT NULL REFERENCES portal_lifecycle_authorities(authority_id),
 expected_snapshot TEXT NOT NULL,
 academic_terms TEXT NOT NULL CHECK(json_valid(academic_terms)),
 current_term TEXT NOT NULL CHECK(json_valid(current_term)),
 enrollment_status TEXT NOT NULL,
 term_history TEXT NOT NULL CHECK(json_valid(term_history)),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 committed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS validate_academic_owner_update BEFORE INSERT ON portal_academic_owner_updates
BEGIN
 SELECT CASE WHEN NOT EXISTS(
  SELECT 1 FROM portal_students s,portal_lifecycle_authorities a
  JOIN campus_portal_imports i ON i.source_checkpoint=a.source_checkpoint,json_each(i.mapping_json) m
  WHERE s.id=NEW.student_id AND s.snapshot=NEW.expected_snapshot AND s.active=1
   AND a.authority_id=NEW.authority_id AND a.enabled=1 AND a.role='superadmin' AND m.key=s.id
   AND NOT EXISTS(SELECT 1 FROM portal_student_lifecycle l WHERE l.student_id=s.id AND (l.recycled=1 OR l.access_blocked=1))
 ) THEN RAISE(ABORT,'Academic authority, ownership, access or snapshot conflict') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM portal_academic_owner_updates WHERE operation_id=NEW.operation_id)
  THEN RAISE(ABORT,'Recover existing academic operation') END;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS apply_academic_owner_update AFTER INSERT ON portal_academic_owner_updates
BEGIN
 UPDATE portal_student_lifecycle SET applying=1 WHERE student_id=NEW.student_id;
 UPDATE portal_students SET snapshot=json_set(snapshot,
  '$.academic_terms',json(NEW.academic_terms),'$.term',json(NEW.current_term),
  '$.enrollment_status',NEW.enrollment_status,'$.term_history',json(NEW.term_history))
 WHERE id=NEW.student_id;
 UPDATE portal_student_lifecycle SET applying=0 WHERE student_id=NEW.student_id;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS immutable_academic_owner_update BEFORE UPDATE ON portal_academic_owner_updates
BEGIN SELECT RAISE(ABORT,'Academic operation receipt is immutable'); END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS retain_academic_owner_update BEFORE DELETE ON portal_academic_owner_updates
BEGIN SELECT RAISE(ABORT,'Academic operation receipt must be retained'); END;
