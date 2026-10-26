# Security

## Reporting a vulnerability

Please email **security@renda.vochsel.com** (or open a private
[GitHub security advisory](https://github.com/vochsel/renda/security/advisories/new)) with steps to
reproduce. Don't open a public issue for anything exploitable. We aim to acknowledge within 2 business
days and to fix or mitigate high-severity issues within 14 days, and we'll credit you if you'd like.

Please test only against your own account and data, don't degrade the service for others (no load or
denial-of-service testing), and don't access other users' data beyond what's needed to show the issue.

## Security model (hosted service)

- **Accounts and keys.** API keys (`rnd_live_…`) are random 192-bit secrets; only their SHA-256 is
  stored. Every `/api/v1` route resolves the key to one organization and scopes all reads and writes to
  it. Dashboard sessions use WorkOS AuthKit; server actions re-check the session and organization and
  rely on Next.js's Origin check against CSRF.
- **Uploads.** Assets are content-addressed by SHA-256. Each organization uploads to its own staging
  area; the server hashes the bytes on first use and only then makes them usable (and stores one shared
  copy). Knowing a file's hash doesn't let another account use it, and nobody can place different bytes
  under someone else's hash. Upload and download URLs are presigned, single-object and short-lived.
- **Worker channel.** The API calls the render workers with a bearer token, which only the two HTTP
  endpoints (submit, cancel) hold. Render containers parse untrusted files and run user HTML/JS, so they
  hold no long-lived secret: each job gets its own event-signing key (HMAC-SHA256 of the master secret
  and `job:<jobId>`) and Cloudflare R2 temporary credentials limited to `outputs/<jobId>/` and
  `work/<jobId>/`, both minted by the API at submit. Events are timestamped (±5 minutes), must all name
  one job, and must verify with that job's key; `done` may only point at the job's own outputs. Event
  handling is idempotent so retries can't double-bill. Blender, pbrt, ffmpeg, headless Chromium and the
  HyperFrames CLI run with an allowlisted environment. A compromised render can therefore read and write
  only its own job's objects and report only on its own job. During a rollout, events signed with the
  master secret are still accepted until `RENDA_ACCEPT_MASTER_WORKER_EVENTS=0`.
- **Fetching URLs on your behalf.** Package layers and `https://` assets are fetched only over https, only
  from globally routable addresses (checked against the resolved address that is then connected to),
  with redirects re-validated and size and time limits.
- **Abuse limits.** Sign-in, device login, anonymous trials and write endpoints are rate limited per
  network, per address or per key. IPs and emails used for limiting are stored only as keyed hashes.
- **Billing.** Stripe webhooks are signature-verified. Usage is metered from signed worker events.
- **Browser hardening.** The site sends a Content-Security-Policy, HSTS, `frame-ancestors 'none'`,
  `nosniff` and a strict referrer policy.

## Self-hosting

If you run your own deployment, set strong random values for `RENDA_WORKER_TOKEN`,
`RENDA_WORKER_SECRET`, `WORKOS_COOKIE_PASSWORD`, `CRON_SECRET` and `TRIAL_IP_SALT`, keep them out of the
repository, and choose rate limits that suit your traffic (`RATE_LIMIT_*`, see `web/.env.example`).
