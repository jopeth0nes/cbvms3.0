-- Proof of a saved contact address requires a full authenticated student session
-- and a code sent only to the unambiguous existing contact. No legacy backfill.
CREATE TABLE IF NOT EXISTS portal_recovery_contact_proofs (
 challenge_id TEXT PRIMARY KEY REFERENCES portal_recovery_challenges(id) ON DELETE CASCADE,
 contact_email TEXT NOT NULL
);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS invalidate_saved_contact_proof AFTER UPDATE OF snapshot ON portal_students
WHEN lower(trim(COALESCE(json_extract(NEW.snapshot,'$.profile.email'),'')))
 <>lower(trim(COALESCE(json_extract(OLD.snapshot,'$.profile.email'),'')))
BEGIN
 UPDATE portal_recovery_challenges SET invalidated=1 WHERE student_id=NEW.id
  AND id IN(SELECT challenge_id FROM portal_recovery_contact_proofs);
END;
