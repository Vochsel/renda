CREATE TABLE "trial_requests" (
	"id" text PRIMARY KEY NOT NULL,
	"ip_hash" text NOT NULL,
	"org_id" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "is_trial" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "claim_token_hash" text;--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "trial_expires_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "orgs" ADD COLUMN "claimed_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "trial_requests" ADD CONSTRAINT "trial_requests_org_id_orgs_id_fk" FOREIGN KEY ("org_id") REFERENCES "public"."orgs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "trial_requests_ip_created_idx" ON "trial_requests" USING btree ("ip_hash","created_at");--> statement-breakpoint
ALTER TABLE "orgs" ADD CONSTRAINT "orgs_claim_token_hash_unique" UNIQUE("claim_token_hash");