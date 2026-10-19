import { createHash, randomBytes } from "node:crypto";

export const KEY_PREFIX = "rnd_live_";

/** New key: `rnd_live_<32 base62-ish chars>`. Only the hash is stored. */
export function generateApiKey(): { key: string; hash: string; prefix: string } {
  const key = KEY_PREFIX + randomBytes(24).toString("base64url");
  return { key, hash: hashApiKey(key), prefix: key.slice(0, KEY_PREFIX.length + 6) };
}

export function hashApiKey(key: string): string {
  return createHash("sha256").update(key).digest("hex");
}

export function looksLikeApiKey(s: string): boolean {
  return s.startsWith(KEY_PREFIX) && s.length >= KEY_PREFIX.length + 20;
}

/** Human-friendly device user code, e.g. `WDJB-MJHT` (no ambiguous chars). */
export function generateUserCode(): string {
  const alphabet = "BCDFGHJKLMNPQRSTVWXZ";
  const bytes = randomBytes(8);
  const chars = Array.from(bytes, (b) => alphabet[b % alphabet.length]);
  return `${chars.slice(0, 4).join("")}-${chars.slice(4).join("")}`;
}

export function generateDeviceCode(): string {
  return randomBytes(32).toString("base64url");
}
