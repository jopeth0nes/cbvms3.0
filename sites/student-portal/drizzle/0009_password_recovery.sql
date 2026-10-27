-- Additive and repeat-safe. Contact/routing emails are deliberately NOT backfilled.
CREATE TABLE IF NOT EXISTS portal_recovery_emails (
 student_id TEXT PRIMARY KEY REFERENCES portal_accounts(student_id),
 email TEXT UNIQUE,
 verified_at INTEGER,
 version INTEGER NOT NULL DEFAULT 0
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS portal_recovery_challenges (
 id TEXT PRIMARY KEY,
 purpose TEXT NOT NULL CHECK(purpose IN ('reset','email')),
 account_key TEXT NOT NULL,
 student_id TEXT REFERENCES portal_accounts(student_id),
 email TEXT,
 email_version INTEGER NOT NULL,
 credential_version INTEGER NOT NULL,
 credential_hash TEXT NOT NULL,
 code_digest TEXT NOT NULL,
 created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,
 invalidated INTEGER NOT NULL DEFAULT 0,
 grant_hash TEXT UNIQUE,
 grant_expires INTEGER,
 consumed TEXT,
 delivery TEXT NOT NULL DEFAULT 'pending' CHECK(delivery IN ('pending','accepted','failed','none'))
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS recovery_challenge_account ON portal_recovery_challenges(account_key,created_at);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS recovery_challenge_expiry ON portal_recovery_challenges(expires_at);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS portal_recovery_limits (
 id TEXT PRIMARY KEY,
 key TEXT NOT NULL,
 at INTEGER NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS recovery_limit_window ON portal_recovery_limits(key,at);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS portal_recovery_mail (
 id TEXT PRIMARY KEY,
 email TEXT NOT NULL,
 sealed_code TEXT,
 created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued',
 claim TEXT,
 claimed_at INTEGER
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS recovery_mail_pending ON portal_recovery_mail(status,created_at);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS portal_recovery_mail_status (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_seen INTEGER NOT NULL
);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS invalidate_recovery_credentials AFTER UPDATE ON portal_accounts
WHEN NEW.password_hash<>OLD.password_hash OR NEW.credential_version<>OLD.credential_version OR NEW.username<>OLD.username
BEGIN
 UPDATE portal_recovery_challenges SET invalidated=1 WHERE student_id=OLD.student_id;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS invalidate_recovery_email AFTER UPDATE ON portal_recovery_emails
WHEN NEW.version<>OLD.version OR NEW.email IS NOT OLD.email OR NEW.verified_at IS NOT OLD.verified_at
BEGIN
 UPDATE portal_recovery_challenges SET invalidated=1 WHERE student_id=OLD.student_id;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS invalidate_recovery_access AFTER UPDATE OF active ON portal_students
WHEN NEW.active<>OLD.active
BEGIN
 UPDATE portal_recovery_challenges SET invalidated=1 WHERE student_id=OLD.id;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS invalidate_recovery_email_removal AFTER DELETE ON portal_recovery_emails
BEGIN
 UPDATE portal_recovery_challenges SET invalidated=1 WHERE student_id=OLD.student_id;
END;
