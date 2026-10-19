import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/errors";
import { flattenForOrg } from "@/lib/layers";
import { parseJobPackage } from "@/lib/packages";

async function err(p: Promise<unknown>) {
  try {
    await p;
  } catch (e) {
    return e instanceof ApiError ? { code: e.code, ...e.extra } : { code: "other", message: (e as Error).message };
  }
  return { code: "ok" };
}

describe("server-side layer flattening", () => {
  it("passes unlayered packages through without touching the network or DB", async () => {
    const pkg = { scene: { root: "https://cdn.example.com/a.step" } };
    expect((await flattenForOrg("org_1", pkg)).package).toBe(pkg);
  });

  it("refuses local-path layers with the upload hint, naming the field", async () => {
    const e = await err(flattenForOrg("org_1", { extends: "./studio.renda.json", scene: { root: "https://h/a.glb" } }));
    expect(e).toMatchObject({ code: "local_uri", field: "extends", reason: "layer_ref" });
  });

  it("refuses http and private-network layer URLs before fetching", async () => {
    expect(await err(flattenForOrg("org_1", { extends: "http://example.com/a.renda.json", scene: { root: "https://h/a.glb" } })))
      .toMatchObject({ code: "layer_error", reason: "layer_ref" });
    expect(await err(flattenForOrg("org_1", { extends: "https://169.254.169.254/latest/meta-data", scene: { root: "https://h/a.glb" } })))
      .toMatchObject({ code: "layer_error", reason: "layer_fetch", field: "extends" });
  });

  it("parseJobPackage rejects unflattened packages instead of silently ignoring extends", () => {
    try {
      parseJobPackage("scene", { extends: "https://h/base.renda.json", scene: { root: "https://h/a.glb" } });
      throw new Error("expected invalid_package");
    } catch (e) {
      expect(e instanceof ApiError && e.code).toBe("invalid_package");
    }
  });

  it("accepts CAD roots", () => {
    expect(() => parseJobPackage("scene", { scene: { root: "https://h/bracket.stp", tessellation: { angularDeflection: 0.2 } } })).not.toThrow();
  });
});
