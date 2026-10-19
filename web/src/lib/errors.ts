/** Every API error carries a `hint` telling an agent what to do next. */
export const ERRORS = {
  unauthorized: {
    status: 401,
    message: "Missing or invalid API key.",
    hint: "Send `Authorization: Bearer rnd_live_…`. No key yet? POST /api/v1/auth/magic/start {email}, then POST /api/v1/auth/magic/verify {email, code} with the 6-digit code from the user's email.",
  },
  forbidden: { status: 403, message: "This key can't access that resource.", hint: "Use a key from the organization that owns the resource." },
  not_found: { status: 404, message: "Not found.", hint: "Check the id. GET /api/v1/jobs lists your jobs." },
  invalid_request: { status: 400, message: "Invalid request body.", hint: "See `issues` for the failing paths. The package schema is at /api/v1/schema.json and examples at /docs." },
  invalid_package: { status: 422, message: "The Renda package is invalid.", hint: "Fix the fields listed in `issues`. Validate locally with `renda validate scene.renda.json`." },
  asset_missing: { status: 422, message: "The package references assets that haven't been uploaded.", hint: "POST /api/v1/uploads {filename,size,sha256} for each file, PUT the bytes to `uploadUrl`, then use the returned `assetUri` (asset://<sha256>) in the package." },
  local_uri: { status: 422, message: "The package references local file paths.", hint: "Upload local files first (POST /api/v1/uploads) and replace paths with asset://<sha256>, or use the renda CLI / MCP which does this automatically." },
  payment_required: { status: 402, message: "Free render minutes are used up and no payment method is on file.", hint: "POST /api/v1/billing/checkout and send the returned `url` to the user so they can add a card, then retry." },
  invalid_code: { status: 400, message: "That code is invalid or expired.", hint: "Ask the user for the latest 6-digit code, or POST /api/v1/auth/magic/start again to send a new one." },
  authorization_pending: { status: 428, message: "The user hasn't approved this device yet.", hint: "Keep polling POST /api/v1/auth/device/token every `interval` seconds." },
  expired_token: { status: 400, message: "The device code expired.", hint: "Start over with POST /api/v1/auth/device." },
  conflict: { status: 409, message: "Conflict.", hint: "The resource is in a state that doesn't allow this action." },
  invalid_signature: { status: 401, message: "Invalid signature.", hint: "Sign the raw body with HMAC-SHA256 using RENDA_WORKER_SECRET." },
  worker_unavailable: { status: 503, message: "The render backend is unavailable.", hint: "Retry in a few seconds with the same idempotencyKey." },
  signup_unavailable: { status: 503, message: "Email-code signup is temporarily unavailable.", hint: "Use the browser device flow instead: POST /api/v1/auth/device and send the user the verificationUriComplete link (or run `renda login`)." },
  trial_expired: { status: 401, message: "This anonymous trial expired before it was claimed.", hint: "Start a new trial (POST /api/v1/auth/trial, `renda trial`) or sign up: POST /api/v1/auth/magic/start {email}." },
  trial_limit: { status: 403, message: "Anonymous trials have a capped resolution and standard/economy priority only.", hint: "Ask the user to open the claim link (`renda claim`) to lift trial limits, or lower the resolution." },
  trial_rate_limited: { status: 429, message: "Too many trials from this network today.", hint: "Sign up instead: POST /api/v1/auth/magic/start {email} (or `renda signup <email>`), or `renda login` for the browser flow." },
  claim_invalid: { status: 404, message: "That claim link is invalid, already used, or expired.", hint: "Start a new trial with `renda trial` or sign in at /sign-in." },
  rate_limited: { status: 429, message: "Too many requests.", hint: "Back off and retry." },
  internal: { status: 500, message: "Internal error.", hint: "Retry; if it persists, include the request id when reporting." },
} as const;

export type ErrorCode = keyof typeof ERRORS;

export class ApiError extends Error {
  constructor(
    public code: ErrorCode,
    message?: string,
    public extra: Record<string, unknown> = {},
    public hint?: string,
  ) {
    super(message ?? ERRORS[code].message);
  }

  get status(): number {
    return ERRORS[this.code].status;
  }

  toBody() {
    return { error: { code: this.code, message: this.message, hint: this.hint ?? ERRORS[this.code].hint, ...this.extra } };
  }
}

export function errorBody(code: ErrorCode, message?: string, extra?: Record<string, unknown>) {
  return new ApiError(code, message, extra).toBody();
}
