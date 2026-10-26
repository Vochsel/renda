# @rendahq/web

Next.js 16 app: the public REST API (`/api/v1`), the remote MCP endpoint (`/api/mcp`), and the dashboard.

- **Accounts**: WorkOS AuthKit for the dashboard (`src/proxy.ts`, `/callback`, `/sign-in`). Agents use API keys
  (`rnd_live_…`, only the sha256 is stored), minted by Magic Auth (`/auth/magic/start` + `/verify`), the device
  flow (`/auth/device` + `/device` approval page), or the dashboard.
- **Data**: Postgres via Drizzle (`src/lib/db/schema.ts`, migrations in `drizzle/`).
- **Files**: Cloudflare R2. Uploads are content-addressed (`assets/<sha256>`), so a file is uploaded once.
  Outputs are written by the worker to `outputs/<jobId>/` and served as presigned GETs.
- **Jobs**: `src/lib/jobs.ts` validates the package (`@rendahq/spec`), checks assets exist, checks balance,
  serves identical re-renders from cache for $0, then posts to the Modal worker. Worker events
  (`/api/v1/internal/worker/events`, HMAC-signed) update status and meter usage.
- **Billing**: per-second usage per hardware class. Free seconds are consumed first (atomic SQL), the rest
  is reported to Stripe Billing Meters (`render_seconds_cpu|gpu_l4|gpu_l40s`, idempotent per job chunk).
  A card is attached with Checkout in setup mode; the webhook starts the metered subscription.
- **Agent discovery**: `/llms.txt`, `/api/v1/openapi.json`, `/api/v1/schema.json`, `/.well-known/mcp.json`.

## Develop

```sh
cp .env.example .env.local        # fill in values
pnpm --filter @rendahq/spec build   # web imports the built spec + mcp packages
pnpm --filter @rendahq/mcp build
pnpm --filter @rendahq/web db:migrate
pnpm --filter @rendahq/web dev
pnpm --filter @rendahq/web test
```

## Stripe setup

1. Create three meters with event names `render_seconds_cpu`, `render_seconds_gpu_l4`, `render_seconds_gpu_l40s`
   (sum aggregation, value key `value`, customer key `stripe_customer_id`).
2. Create a metered price on each ($0.00025, $0.000583, $0.00125 per second) and set `STRIPE_PRICE_*`.
3. Point a webhook at `/api/v1/webhooks/stripe` for `checkout.session.completed` and `payment_method.detached`.

## Data retention and deletion

Policy (stated in `/legal/privacy`, constants in `src/lib/legal.ts`): uploaded files are deleted 30 days after
their last use, outputs 30 days after the job finished; customers can delete sooner.

- **App-enforced** (`src/lib/retention.ts`, called from the `/api/internal/sweep` cron every 5 minutes,
  batch-limited and idempotent): upload registrations unused for 30 days (`assets.last_used_at`, bumped when a
  job or a re-registration uses the file) and then the shared `assets/<sha256>` object once no account has it;
  outputs of jobs finished 30+ days ago (and the job rows' output lists); storage of deleted accounts.
- **Customer-initiated**: `DELETE /api/v1/jobs/:id` (outputs), `DELETE /api/v1/uploads/:sha256`,
  `DELETE /api/v1/me` or dashboard → Settings (whole account; see `src/lib/account-deletion.ts` for what is
  kept, anonymised, for invoicing).
- **R2 lifecycle rules** (the backstop; apply once per bucket, not done automatically). No rule on `assets/`:
  those are kept while in use, so only the app can expire them.

```sh
BUCKET=renda   # and again for the preview bucket
npx wrangler r2 bucket lifecycle add "$BUCKET" expire-work    work/    --expire-days 1  --abort-multipart-days 1
npx wrangler r2 bucket lifecycle add "$BUCKET" expire-outputs outputs/ --expire-days 30 --abort-multipart-days 1
npx wrangler r2 bucket lifecycle add "$BUCKET" expire-cache   cache/   --expire-days 30
npx wrangler r2 bucket lifecycle add "$BUCKET" expire-staging staging/ --expire-days 30 --abort-multipart-days 1
npx wrangler r2 bucket lifecycle list "$BUCKET"
```

The `outputs/` rule counts from when the object was written. A server-side cache hit reuses an earlier job's
files, so a cached copy disappears when the original's files do (≤ 30 days after the original render).

Not covered yet: the worker's content-addressed input cache on the Modal Volume `renda-assets` has no
expiry (see the Privacy Policy placeholder).

## WorkOS setup

Enable AuthKit and Magic Auth, add `${APP_URL}/callback` as a redirect URI and `/` as the sign-out URI.
