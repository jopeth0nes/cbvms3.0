CREATE TABLE `campus_sync_control` (
	`singleton` integer PRIMARY KEY NOT NULL,
	`source_id` text NOT NULL,
	`writer_id` text NOT NULL,
	`protocol` integer DEFAULT 2 NOT NULL,
	`generation` integer DEFAULT 0 NOT NULL,
	`run_id` text,
	`checked_at` text,
	CONSTRAINT "sync_singleton" CHECK("campus_sync_control"."singleton"=1)
);
--> statement-breakpoint
CREATE TABLE `campus_sync_publications` (
	`generation` integer PRIMARY KEY NOT NULL,
	`run_id` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `campus_sync_publications_run_id_unique` ON `campus_sync_publications` (`run_id`);--> statement-breakpoint
ALTER TABLE `portal_accounts` ADD `credential_version` integer DEFAULT 0 NOT NULL;