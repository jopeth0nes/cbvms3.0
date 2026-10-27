-- Provider-admin only: explicit owner-approved destination, not automatic verification.
CREATE TABLE IF NOT EXISTS portal_recovery_assistance (
 id TEXT PRIMARY KEY,
 student_id TEXT NOT NULL REFERENCES portal_accounts(student_id),
 email TEXT NOT NULL,
 credential_version INTEGER NOT NULL,
 email_version INTEGER NOT NULL,
 approved_by TEXT NOT NULL,
 created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL,
 consumed_at INTEGER
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS portal_recovery_assisted_challenges (
 challenge_id TEXT PRIMARY KEY REFERENCES portal_recovery_challenges(id) ON DELETE CASCADE,
 approval_id TEXT NOT NULL REFERENCES portal_recovery_assistance(id)
);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS validate_recovery_assistance BEFORE INSERT ON portal_recovery_assistance
BEGIN
 SELECT CASE WHEN NOT EXISTS(
  SELECT 1 FROM portal_accounts a JOIN portal_students s ON s.id=a.student_id
  WHERE a.student_id=NEW.student_id AND a.credential_version=NEW.credential_version
   AND a.pending_action_id IS NULL AND s.active=1
   AND NOT EXISTS(SELECT 1 FROM portal_student_lifecycle l WHERE l.student_id=s.id AND (l.recycled=1 OR l.access_blocked=1))
   AND NEW.email_version=COALESCE((SELECT version FROM portal_recovery_emails WHERE student_id=a.student_id),0)+1
   AND length(NEW.approved_by)>0 AND NEW.expires_at>NEW.created_at AND NEW.expires_at<=NEW.created_at+1800000
 ) THEN RAISE(ABORT,'Assisted recovery identity, access or version conflict') END;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS apply_recovery_assistance AFTER INSERT ON portal_recovery_assistance
BEGIN
 INSERT INTO portal_recovery_emails(student_id,email,version) VALUES(NEW.student_id,NEW.email,NEW.email_version)
 ON CONFLICT(student_id) DO UPDATE SET email=NEW.email,version=NEW.email_version,verified_at=NULL;
 DELETE FROM portal_sessions WHERE student_id=NEW.student_id;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS retain_recovery_approval BEFORE DELETE ON portal_recovery_assistance
BEGIN SELECT RAISE(ABORT,'Recovery approvals are audit records'); END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS immutable_recovery_approval BEFORE UPDATE ON portal_recovery_assistance
WHEN NEW.id<>OLD.id OR NEW.student_id<>OLD.student_id OR NEW.email<>OLD.email
 OR NEW.credential_version<>OLD.credential_version OR NEW.email_version<>OLD.email_version
 OR NEW.approved_by<>OLD.approved_by OR NEW.created_at<>OLD.created_at OR NEW.expires_at<>OLD.expires_at
 OR OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
BEGIN SELECT RAISE(ABORT,'Recovery approval is immutable except single consumption'); END;
