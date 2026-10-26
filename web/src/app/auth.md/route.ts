import { appUrl } from "@/lib/env";
import { CLI_PACKAGE } from "@/lib/install-snippets";

/** auth.md: how an agent gets credentials for Renda (anonymous-then-claim, email code, device flow). */
export function GET() {
  const base = appUrl();
  const body = `# Renda auth for agents

Credentials are API keys (\`rnd_live_…\`), sent as \`Authorization: Bearer <key>\`. Pick one way to get a key:

## 1. Anonymous trial (no human needed now)
POST ${base}/api/v1/auth/trial
→ 201 {"apiKey", "claimUrl", "freeSecondsLeft": 300, "expiresAt", "limits": {"maxDimension": 1920}}
- Rate limited per network. Unclaimed trials and their keys stop working at \`expiresAt\`.
- Give \`claimUrl\` to the human. When they open it and sign in, the workspace (renders, key) becomes theirs and the free tier is topped up. Don't open it yourself.
CLI: \`npx ${CLI_PACKAGE} trial\` / \`uvx renda trial\`.

## 2. Email code (human reads one email)
POST ${base}/api/v1/auth/magic/start {"email"} → the human receives a 6-digit code.
POST ${base}/api/v1/auth/magic/verify {"email", "code"} → {"apiKey", "user", "org"}
Ask the human for the code; never guess it. CLI: \`renda signup <email>\`, then \`renda verify <code>\`.

## 3. Device flow (human has a browser)
POST ${base}/api/v1/auth/device → {"deviceCode", "userCode", "verificationUri", "verificationUriComplete", "interval", "expiresIn"}
Show the human verificationUriComplete, then poll POST ${base}/api/v1/auth/device/token {"deviceCode"} every \`interval\` seconds:
428 {"error":"authorization_pending"} until approved, then {"apiKey"}. CLI: \`renda login\`.

## Terms
Creating or claiming an account, or adding a card, means the human agrees to the Terms of Service (${base}/legal/terms) and Privacy Policy (${base}/legal/privacy). Tell them when you sign them up; the trial and magic-code responses include a \`terms\` object with these links.

## Storing keys
Keys are shown once. The CLIs and MCP server share ~/.config/renda/credentials.json (mode 600); $RENDA_API_KEY overrides it.
Revoke keys in the dashboard: ${base}/dashboard/keys.

## Errors
401 unauthorized → no/invalid key: use one of the flows above. 401 trial_expired → start a new trial or sign up.
402 payment_required → POST /api/v1/billing/checkout and give the returned url to the human.
`;
  return new Response(body, { headers: { "content-type": "text/markdown; charset=utf-8" } });
}
