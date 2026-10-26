import { describe, expect, it } from "vitest";
import { RendaApiError, RendaClient } from "../src/client.js";
import { fakeFetch } from "./helpers.js";

describe("RendaClient", () => {
  it("sends bearer auth to /api/v1", async () => {
    const f = fakeFetch(() => ({ json: { user: { id: "u" } } }));
    const c = new RendaClient({ baseUrl: "https://x.test/", apiKey: "rnd_live_abc", fetch: f.fetch });
    await c.me();
    expect(f.calls[0].url).toBe("https://x.test/api/v1/me");
    expect(f.calls[0].headers.authorization).toBe("Bearer rnd_live_abc");
  });

  it("maps structured errors with hint", async () => {
    const f = fakeFetch(() => ({ status: 402, json: { error: { code: "payment_required", message: "No credit left.", hint: "POST /api/v1/billing/checkout" } } }));
    const c = new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch });
    const err = await c.createJob({ package: { scene: { root: "a.glb" } } }).catch((e) => e);
    expect(err).toBeInstanceOf(RendaApiError);
    expect(err.status).toBe(402);
    expect(err.code).toBe("payment_required");
    expect(err.hint).toContain("billing/checkout");
    expect(err.toAgentText()).toContain("Next step:");
  });

  it("maps oauth-style device errors", async () => {
    const f = fakeFetch(() => ({ status: 428, json: { error: "authorization_pending" } }));
    const c = new RendaClient({ baseUrl: "https://x.test", fetch: f.fetch });
    const err = await c.deviceToken("dc").catch((e) => e);
    expect(err.code).toBe("authorization_pending");
  });

  it("maps non-JSON errors", async () => {
    const f = fakeFetch(() => ({ status: 502, text: "Bad Gateway" }));
    const c = new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch });
    const err = await c.me().catch((e) => e);
    expect(err.code).toBe("http_502");
    expect(err.message).toBe("Bad Gateway");
  });

  it("fails fast without a key and tells the agent how to sign up", async () => {
    const f = fakeFetch(() => ({ json: {} }));
    const c = new RendaClient({ baseUrl: "https://x.test", fetch: f.fetch });
    const err = await c.me().catch((e) => e);
    expect(err.code).toBe("unauthenticated");
    expect(err.hint).toContain("renda_signup");
    expect(f.calls).toHaveLength(0);
  });

  it("does not send auth on public routes", async () => {
    const f = fakeFetch(() => ({ json: { ok: true } }));
    const c = new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch });
    await c.magicStart("a@b.co");
    expect(f.calls[0].headers.authorization).toBeUndefined();
    expect(f.calls[0].body).toEqual({ email: "a@b.co" });
  });

  it("waitForJob polls to a terminal status", async () => {
    let n = 0;
    const f = fakeFetch(() => ({ json: { id: "j", status: ++n < 3 ? "rendering" : "succeeded", progress: n / 3, outputs: [] } }));
    const c = new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch });
    const seen: string[] = [];
    const j = await c.waitForJob("j", { intervalMs: 1, onProgress: (x) => { seen.push(x.status); } });
    expect(j.status).toBe("succeeded");
    expect(seen).toEqual(["rendering", "rendering", "succeeded"]);
  });
});
