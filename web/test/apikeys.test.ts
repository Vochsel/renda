import { describe, expect, it } from "vitest";
import { generateApiKey, generateUserCode, hashApiKey, looksLikeApiKey, KEY_PREFIX } from "@/lib/apikeys";

describe("api keys", () => {
  it("generates prefixed keys whose hash matches", () => {
    const k = generateApiKey();
    expect(k.key.startsWith(KEY_PREFIX)).toBe(true);
    expect(k.hash).toBe(hashApiKey(k.key));
    expect(k.hash).toMatch(/^[0-9a-f]{64}$/);
    expect(k.key.startsWith(k.prefix)).toBe(true);
    expect(looksLikeApiKey(k.key)).toBe(true);
  });
  it("keys are unique and hashes differ", () => {
    const a = generateApiKey(), b = generateApiKey();
    expect(a.key).not.toBe(b.key);
    expect(a.hash).not.toBe(b.hash);
  });
  it("rejects non-keys", () => {
    expect(looksLikeApiKey("sk_live_abc")).toBe(false);
    expect(looksLikeApiKey("rnd_live_short")).toBe(false);
  });
  it("user codes are XXXX-XXXX without vowels", () => {
    for (let i = 0; i < 50; i++) expect(generateUserCode()).toMatch(/^[B-DF-HJ-NP-TV-XZ]{4}-[B-DF-HJ-NP-TV-XZ]{4}$/);
  });
});
