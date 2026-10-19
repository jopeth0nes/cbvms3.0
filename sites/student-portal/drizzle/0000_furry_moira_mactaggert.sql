CREATE TABLE `portal_actions` (
	`id` text PRIMARY KEY NOT NULL,
	`student_id` text NOT NULL,
	`kind` text NOT NULL,
	`payload` text NOT NULL,
	`created_at` text NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`result` text,
	`dedupe` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX `portal_actions_dedupe_unique` ON `portal_actions` (`dedupe`);--> statement-breakpoint
CREATE TABLE `portal_preferences` (
	`student_id` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `portal_students` (
	`id` text PRIMARY KEY NOT NULL,
	`email` text NOT NULL,
	`name` text NOT NULL,
	`snapshot` text NOT NULL,
	`synced_at` text NOT NULL,
	`active` integer DEFAULT 1 NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `portal_students_email_unique` ON `portal_students` (`email`);