import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";

const authenticate = vi.fn();
const listOutputs = vi.fn();
vi.mock("@/lib/auth", () => ({ authenticate: (req: Request) => authenticate(req) }));
vi.mock("@/lib/outputs", () => ({ listOutputs: (...a: unknown[]) => listOutputs(...a) }));

const { GET } = await import("@/app/api/v1/outputs/route");

const get = (qs = "") => GET(new Request(`https://renda.test/api/v1/outputs${qs}`, { headers: { authorization: "Bearer rnd_live_x" } }), undefined as never);

describe("GET /api/v1/outputs", () => {
  beforeEach(() => {
    authenticate.mockReset();
    listOutputs.mockReset();
    listOutputs.mockResolvedValue({ items: [], nextCursor: null });
  });

  it("401s without a valid key (with the onboarding hint)", async () => {
    authenticate.mockRejectedValue(new ApiError("unauthorized"));
    const res = await get();
    expect(res.status).toBe(401);
    expect((await res.json()).error.hint).toMatch(/auth\/trial|magic/);
    expect(listOutputs).not.toHaveBeenCalled();
  });

  it("lists only the authenticated org's outputs and passes query options through", async () => {
    authenticate.mockResolvedValue({ org: { id: "org_mine" }, user: null, keyId: "k" });
    const res = await get("?limit=10&kind=video&cursor=abc&all=1");
    expect(res.status).toBe(200);
    expect(listOutputs).toHaveBeenCalledWith("org_mine", { cursor: "abc", limit: 10, kind: "video", all: true });
  });

  it("validates the query", async () => {
    authenticate.mockResolvedValue({ org: { id: "org_mine" }, user: null, keyId: "k" });
    expect((await get("?kind=pdf")).status).toBe(400);
    expect((await get("?limit=0")).status).toBe(400);
  });
});
