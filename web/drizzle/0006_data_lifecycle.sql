-- Terms acceptance (lib/terms.ts), account deletion (lib/account-deletion.ts) and retention (lib/retention.ts).
ALTER TABLE "users" ADD COLUMN "terms_version" text;--> statement-breakpoint
ALTER TABLE "users" ADD COLUMN "terms_accepted_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "terms_version" text;--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "terms_accepted_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "deleted_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "purged_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "jobs" ADD COLUMN "outputs_deleted_at" timestamp with time zone;--> statement-breakpoint
-- Existing uploads count as used now, so they get the full 30 days from this release rather than being
-- swept on the first cron run.
ALTER TABLE "assets" ADD COLUMN "last_used_at" timestamp with time zone DEFAULT now() NOT NULL;--> statement-breakpoint
CREATE INDEX "assets_sha256_idx" ON "assets" USING btree ("sha256");--> statement-breakpoint
CREATE INDEX "assets_last_used_idx" ON "assets" USING btree ("last_used_at");--> statement-breakpoint
CREATE INDEX "jobs_outputs_expiry_idx" ON "jobs" USING btree ("finished_at") WHERE "jobs"."outputs_deleted_at" is null;--> statement-breakpoint
-- usage_events are invoicing/tax records: account deletion anonymises the org and job rows instead of
-- deleting them, and these FKs now refuse a delete that would take billing records with it.
ALTER TABLE "usage_events" DROP CONSTRAINT "usage_events_job_id_jobs_id_fk";--> statement-breakpoint
ALTER TABLE "usage_events" DROP CONSTRAINT "usage_events_org_id_orgs_id_fk";--> statement-breakpoint
ALTER TABLE "usage_events" ADD CONSTRAINT "usage_events_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "usage_events" ADD CONSTRAINT "usage_events_org_id_orgs_id_fk" FOREIGN KEY ("org_id") REFERENCES "public"."orgs"("id") ON DELETE restrict ON UPDATE no action;
