CREATE TABLE "free_grants" (
	"email_hash" text PRIMARY KEY NOT NULL,
	"org_id" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "billing" ADD COLUMN "stripe_subscription_id" text;--> statement-breakpoint
ALTER TABLE "billing" ADD COLUMN "subscription_status" text;--> statement-breakpoint
ALTER TABLE "billing" ADD COLUMN "active_jobs" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "billing" ADD COLUMN "monthly_limit_usd" double precision;--> statement-breakpoint
ALTER TABLE "jobs" ADD COLUMN "max_cost_usd" double precision;--> statement-breakpoint
ALTER TABLE "jobs" ADD COLUMN "last_event_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "free_grants" ADD CONSTRAINT "free_grants_org_id_orgs_id_fk" FOREIGN KEY ("org_id") REFERENCES "public"."orgs"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
UPDATE "billing" b SET "active_jobs" = (SELECT count(*) FROM "jobs" j WHERE j."org_id" = b."org_id" AND j."status" IN ('queued', 'planning', 'rendering', 'encoding'));--> statement-breakpoint
UPDATE "jobs" SET "last_event_at" = now() WHERE "status" IN ('queued', 'planning', 'rendering', 'encoding');
