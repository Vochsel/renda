---
name: renda-onboarding
description: Get a user from no Renda account to their first cloud render entirely from the agent. Covers one-command setup (`npx @rendahq/cli init`), anonymous claimable trials, email signup with a 6-digit code, saving the API key, free render minutes, and handing the human a Stripe link when payment is needed. Use whenever a Renda tool or CLI says unauthenticated / no API key / payment_required, when the user wants to sign up for or log in to Renda, or before the first Renda render on a new machine.
---

# Renda onboarding (zero to first render)

The whole account flow works without a browser except two moments that need the human:
reading a code from their email, and entering a card. Everything else is yours.

## 1. Check whether you're already set up

- MCP: call `renda_whoami`. Success means you're done here; skip to rendering.
- CLI: `renda whoami`. Keys live in `~/.config/renda/credentials.json` (shared by the CLI
  and the MCP server) or `RENDA_API_KEY`.

## 2. Fastest: one-command setup with a trial

    npx @rendahq/cli init --yes          # or, with uv: uvx renda trial && uvx renda mcp add

This starts an **anonymous trial** (about 5 free render-minutes, max 1920px, no signup), adds
the renda MCP server to the installed agents, installs these skills, and adds a note to
AGENTS.md. MCP only: `renda_trial` does the same (and `renda_render` starts one on its own
when no key is configured).

A trial returns a **claim link**. Give it to the human right away, in plain words:
*"I started a free Renda trial so I could render this. Open <claimUrl> and sign in to keep
it (and get the full free tier)."* Never open the link yourself. Unclaimed trials stop
working after 7 days. `renda claim` / `renda_claim` prints the link again.

Use a real account instead (step 3) when the user wants renders above 1920px or already
has an account.

## 3. Sign up or log in (same flow)

1. Ask the user for their email if you don't already know it. Don't guess it from git
   config without confirming. The account and its bills belong to them.
2. `renda_signup {email}` (CLI: `renda signup you@example.com`). New accounts are created
   automatically; existing ones just get a login code.
3. Ask the human for the 6-digit code from the email, in plain words: *"I've sent a
   6-digit code to you@example.com. What is it?"* Then **wait for their reply.**
   - Never invent, guess or brute-force a code. Each wrong attempt counts against a rate limit, and the code proves the human consented.
   - Code expired or not received after a minute? Check the spam folder, then call `renda_signup` again for a new one.
4. `renda_verify {email, code}` (CLI: `renda verify 123456`). Locally the key is saved
   automatically. With the **remote** MCP, the key is returned once: tell the user to
   store it and configure it as the Bearer header. Don't echo it into files or commits.

Humans at a terminal can use `renda login` instead (device flow: opens a URL and
shows a short code to confirm in the browser).

## 4. First render

New accounts get **30 free render-minutes** (trials about 5, topped up to 30 when claimed). Do a draft render straight away so the user
sees value: `renda_render {file: "<their model>"}`. Then follow the `renda` skill.

## 5. When free minutes run out

A job or estimate fails with `payment_required` (CLI exit code 4):

1. `renda_billing_link` → Stripe Checkout URL.
2. Give the URL to the human: *"Your free minutes are used up. Add a card here to keep
   rendering (billed per render-minute): <url>"*. You can't complete payment yourself, and
   shouldn't try to fill in card forms.
3. When they confirm, check with `renda_whoami` (`billing.hasPaymentMethod: true`) and
   retry the render.

For invoices or card changes use `renda_billing_link {portal: true}`.

## Gotchas

- Work email vs personal: the org is created from the account. Ask which they want if unclear.
- `trial_limit`: trials cap resolution at 1920px and don't allow `rush`. Ask the human to open the claim link.
- `trial_expired`: the trial wasn't claimed within 7 days. Start a new one or sign up.
- Several agents or machines: each `renda_verify` mints a separate named key. That's fine, and keys can be revoked in the dashboard.
- Self-hosted / dev: set `RENDA_API_URL` (e.g. `http://localhost:3000`) before signup so the key matches the server.
