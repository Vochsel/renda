CREATE TABLE "rate_limits" (
	"bucket" text NOT NULL,
	"key" text NOT NULL,
	"window_start" timestamp with time zone NOT NULL,
	"count" bigint DEFAULT 0 NOT NULL,
	CONSTRAINT "rate_limits_bucket_key_window_start_pk" PRIMARY KEY("bucket","key","window_start")
);
--> statement-breakpoint
ALTER TABLE "assets" ADD COLUMN "verified_at" timestamp with time zone;--> statement-breakpoint
CREATE INDEX "rate_limits_window_idx" ON "rate_limits" USING btree ("window_start");--> statement-breakpoint
-- Uploads made before verification existed went straight to assets/{sha}; the worker still rejects
-- hash mismatches on download, so grandfather them rather than break existing packages.
UPDATE "assets" SET "verified_at" = "created_at" WHERE "verified_at" IS NULL;
