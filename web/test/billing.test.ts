import { describe, expect, it } from "vitest";
import { splitUsage } from "@/lib/billing";
import { costUsd, PRICE_PER_MINUTE } from "@/lib/pricing";
import { canonicalJson } from "@/lib/jobs";

describe("billing math", () => {
  it("free seconds are not billed", () => {
    expect(splitUsage("gpu-l4", 60, 60)).toEqual({ freeSeconds: 60, billedSeconds: 0, costUsd: 0 });
    const s = splitUsage("gpu-l4", 90, 30);
    expect(s.billedSeconds).toBe(60);
    expect(s.costUsd).toBeCloseTo(PRICE_PER_MINUTE["gpu-l4"]);
  });
  it("cost per class", () => {
    expect(costUsd("cpu", 120)).toBeCloseTo(PRICE_PER_MINUTE.cpu * 2);
  });
  it("canonical json ignores key order", () => {
    expect(canonicalJson({ b: 1, a: { d: [1, { y: 2, x: 1 }], c: null } })).toBe(canonicalJson({ a: { c: null, d: [1, { x: 1, y: 2 }] }, b: 1 }));
  });
});
