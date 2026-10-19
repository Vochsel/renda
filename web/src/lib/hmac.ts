import { createHmac, timingSafeEqual } from "node:crypto";

/** Legacy (untimed) signature: `sha256=<hex of HMAC(body)>`. */
export function signBody(rawBody: string, secret: string): string {
  return "sha256=" + createHmac("sha256", secret).update(rawBody).digest("hex");
}

/** Timed signature: `v1=<hex of HMAC("<timestamp>.<body>")>`, sent with `x-renda-timestamp`. */
export function signTimed(rawBody: string, timestamp: number, secret: string): string {
  return "v1=" + createHmac("sha256", secret).update(`${timestamp}.${rawBody}`).digest("hex");
}

function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** Timing-safe check of a legacy `x-renda-signature: sha256=<hex>`. */
export function verifySignature(rawBody: string, header: string | null, secret: string): boolean {
  if (!header) return false;
  return safeEqual(signBody(rawBody, secret), header.trim());
}

export const MAX_EVENT_SKEW_SECONDS = 300;

export type WorkerSigResult = "ok" | "legacy" | "bad" | "stale";

/**
 * Verify a worker event. Timed signatures must be within MAX_EVENT_SKEW_SECONDS of `now`, so a
 * captured request can't be replayed later (handlers are idempotent for replays inside the window).
 * Untimed legacy signatures report "legacy"; the caller decides whether to still accept them.
 */
export function verifyWorkerEvent(
  rawBody: string,
  headers: { signature: string | null; timestamp: string | null },
  secret: string,
  now = Date.now(),
): WorkerSigResult {
  const sig = headers.signature?.trim();
  if (!sig) return "bad";
  if (headers.timestamp !== null) {
    const ts = Number(headers.timestamp);
    if (!Number.isInteger(ts)) return "bad";
    if (!safeEqual(signTimed(rawBody, ts, secret), sig)) return "bad";
    return Math.abs(now / 1000 - ts) <= MAX_EVENT_SKEW_SECONDS ? "ok" : "stale";
  }
  return verifySignature(rawBody, sig, secret) ? "legacy" : "bad";
}
