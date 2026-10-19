import { describe, expect, it } from "vitest";
import { z } from "zod";
import { ApiError, ERRORS } from "@/lib/errors";
import { api } from "@/lib/http";

describe("errors", () => {
  it("every error code has a status, message and actionable hint", () => {
    for (const [code, e] of Object.entries(ERRORS)) {
      expect(e.status, code).toBeGreaterThanOrEqual(400);
      expect(e.hint.length, code).toBeGreaterThan(10);
    }
  });
  it("payment_required tells the agent how to get a checkout link", () => {
    expect(new ApiError("payment_required").toBody().error.hint).toMatch(/billing\/checkout/);
  });
  it("api() wraps ApiError and ZodError into the error envelope", async () => {
    const h1 = api(async () => { throw new ApiError("unauthorized"); });
    const r1 = await h1(new Request("http://x"), {});
    expect(r1.status).toBe(401);
    expect((await r1.json()).error.hint).toMatch(/magic\/start/);

    const h2 = api(async () => { z.object({ a: z.string() }).parse({}); return new Response(); });
    const r2 = await h2(new Request("http://x"), {});
    expect(r2.status).toBe(400);
    const b2 = await r2.json();
    expect(b2.error.code).toBe("invalid_request");
    expect(b2.error.issues[0].path).toBe("a");
  });
  it("unknown errors become 500 internal without leaking", async () => {
    const h = api(async () => { throw new Error("db password is hunter2"); });
    const orig = console.error; console.error = () => {};
    const r = await h(new Request("http://x"), {});
    console.error = orig;
    expect(r.status).toBe(500);
    expect(JSON.stringify(await r.json())).not.toMatch(/hunter2/);
  });
});
