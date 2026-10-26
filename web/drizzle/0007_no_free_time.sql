DROP TABLE "free_grants" CASCADE;--> statement-breakpoint
ALTER TABLE "billing" ADD COLUMN "credit_usd" double precision DEFAULT 0 NOT NULL;--> statement-breakpoint
-- No free render time any more: renders draw on prepaid credit only.
UPDATE "billing" SET "free_seconds_remaining" = 0;
