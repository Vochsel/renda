import { createHmac, timingSafeEqual } from "node:crypto";
import { env, envOptional } from "./env";

/**
 * The caller's IP for rate limiting and abuse controls.
 *
 * On Vercel, `x-real-ip` / the first `x-forwarded-for` hop are set by the platform, not the client.
 * The remote MCP endpoint calls this API from our own servers, so it forwards the original client IP
 * in `x-renda-client-ip`, signed with a key only the server knows; unsigned or badly signed values
 * are ignored (a client can't pick its own IP by sending the header).
 */
export const FORWARDED_IP_HEADER = "x-renda-client-ip";

function key(): string {
  return envOptional("RENDA_INTERNAL_SECRET") ?? `client-ip:${env("RENDA_WORKER_SECRET")}`;
}

export function signForwardedIp(ip: string): string {
  return `${ip}.${createHmac("sha256", key()).update(ip).digest("hex")}`;
}

function verifiedForwardedIp(value: string | null): string | null {
  if (!value) return null;
  const dot = value.lastIndexOf(".");
  if (dot <= 0) return null;
  const ip = value.slice(0, dot);
  const want = Buffer.from(signForwardedIp(ip));
  const got = Buffer.from(value);
  return want.length === got.length && timingSafeEqual(want, got) ? ip : null;
}

export function clientIp(req: Request): string {
  return (
    verifiedForwardedIp(req.headers.get(FORWARDED_IP_HEADER)) ??
    req.headers.get("x-real-ip")?.trim() ??
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown"
  );
}
