# Security

## Reporting a vulnerability

Please open a private
[GitHub security advisory](https://github.com/vochsel/renda/security/advisories/new) or email
**security@[DOMAIN]** with steps to reproduce. (The security mailbox isn't set up yet, so until this
placeholder is replaced, use the advisory form. Keep this in sync with `SECURITY_EMAIL` in
`web/src/lib/contact.ts`.) Don't open a public issue for anything exploitable. We aim to acknowledge within 2 business
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
- **Worker channel.** The API calls the render workers with a bearer token. Workers report back with
  HMAC-SHA256-signed, timestamped events; stale or forged events are rejected, and event handling is
  idempotent so retries can't double-bill.
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
