CREATE TABLE "credit_purchases" (
	"stripe_session_id" text PRIMARY KEY NOT NULL,
	"org_id" text NOT NULL,
	"payment_intent_id" text,
	"amount_usd" double precision NOT NULL,
	"refunded_usd" double precision DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "credit_purchases_payment_intent_id_unique" UNIQUE("payment_intent_id")
);
--> statement-breakpoint
ALTER TABLE "credit_purchases" ADD CONSTRAINT "credit_purchases_org_id_orgs_id_fk" FOREIGN KEY ("org_id") REFERENCES "public"."orgs"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "credit_purchases_org_idx" ON "credit_purchases" USING btree ("org_id");