CREATE TABLE `portal_accounts` (
	`student_id` text PRIMARY KEY NOT NULL,
	`username` text NOT NULL,
	`password_hash` text NOT NULL,
	`must_change_password` integer DEFAULT 1 NOT NULL,
	`pending_base_hash` text,
	`pending_action_id` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX `portal_accounts_username_unique` ON `portal_accounts` (`username`);--> statement-breakpoint
CREATE TABLE `portal_login_limits` (
	`key` text PRIMARY KEY NOT NULL,
	`attempts` integer NOT NULL,
	`expires` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `portal_sessions` (
	`token_hash` text PRIMARY KEY NOT NULL,
	`student_id` text NOT NULL,
	`credential_hash` text NOT NULL,
	`csrf` text NOT NULL,
	`expires` integer NOT NULL
);
