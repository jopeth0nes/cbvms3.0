// Intentionally empty by default.
// Add Drizzle tables here when the site actually needs a database.
// See examples/d1/db/schema.ts for an opt-in example.
import { sql } from 'drizzle-orm';
import { sqliteTable, text, integer, check } from 'drizzle-orm/sqlite-core';
export const students = sqliteTable('portal_students', {
  id: text('id').primaryKey(), email: text('email').notNull().unique(),
  name: text('name').notNull(), snapshot: text('snapshot').notNull(),
  syncedAt: text('synced_at').notNull(), active: integer('active').notNull().default(1),
});
export const preferences = sqliteTable('portal_preferences', {
  studentId: text('student_id').primaryKey(), value: text('value').notNull(),
});
export const actions = sqliteTable('portal_actions', {
  id: text('id').primaryKey(), studentId: text('student_id').notNull(),
  kind: text('kind').notNull(), payload: text('payload').notNull(), createdAt: text('created_at').notNull(),
  status: text('status').notNull().default('queued'), result: text('result'), dedupe: text('dedupe').unique(),
});
export const accounts = sqliteTable('portal_accounts', {
  studentId: text('student_id').primaryKey(), username: text('username').notNull().unique(),
  passwordHash: text('password_hash').notNull(), mustChange: integer('must_change_password').notNull().default(1),
  credentialVersion: integer('credential_version').notNull().default(0),
  pendingBaseHash: text('pending_base_hash'), pendingActionId: text('pending_action_id'),
});
export const sessions = sqliteTable('portal_sessions', {
  tokenHash: text('token_hash').primaryKey(), studentId: text('student_id').notNull(),
  credentialHash: text('credential_hash').notNull(), csrf: text('csrf').notNull(), expires: integer('expires').notNull(),
});
export const loginLimits = sqliteTable('portal_login_limits', {
  key: text('key').primaryKey(), attempts: integer('attempts').notNull(), expires: integer('expires').notNull(),
});

export const syncControl = sqliteTable('campus_sync_control', {
  singleton: integer('singleton').primaryKey(), sourceId: text('source_id').notNull(),
  writerId: text('writer_id').notNull(), protocol: integer('protocol').notNull().default(2),
  generation: integer('generation').notNull().default(0), runId: text('run_id'), checkedAt: text('checked_at'),
}, table => [check('sync_singleton', sql`${table.singleton}=1`)]);

export const syncPublications = sqliteTable('campus_sync_publications', {
  generation: integer('generation').primaryKey(), runId: text('run_id').notNull().unique(),
});
