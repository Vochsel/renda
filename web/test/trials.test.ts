import { afterEach, describe, expect, it, vi } from "vitest";
import { assertWithinTrialLimits, hashToken, isActiveTrial, isExpiredTrial } from "@/lib/trials";
import { ApiError } from "@/lib/errors";

const trial = { isTrial: true, claimedAt: null, trialExpiresAt: new Date(Date.now() + 86_400_000) };

describe("trials", () => {
  it("active vs claimed vs expired", () => {
    expect(isActiveTrial(trial)).toBe(true);
    expect(isActiveTrial({ ...trial, claimedAt: new Date() })).toBe(false);
    expect(isExpiredTrial(trial)).toBe(false);
    expect(isExpiredTrial({ ...trial, trialExpiresAt: new Date(Date.now() - 1000) })).toBe(true);
    expect(isExpiredTrial({ ...trial, claimedAt: new Date(), trialExpiresAt: new Date(0) })).toBe(false);
    expect(isExpiredTrial({ isTrial: false, claimedAt: null, trialExpiresAt: null })).toBe(false);
  });

  afterEach(() => vi.unstubAllEnvs());

  it("limits resolution and rush priority for unclaimed trials only", () => {
    vi.stubEnv("TRIAL_MAX_DIMENSION", "1920");
    expect(() => assertWithinTrialLimits(trial, { resolution: [1920, 1080], priority: "standard" })).not.toThrow();
    expect(() => assertWithinTrialLimits(trial, { resolution: [3840, 2160], priority: "standard" })).toThrow(ApiError);
    expect(() => assertWithinTrialLimits(trial, { resolution: [640, 480], priority: "rush" })).toThrow(/1920px/);
    expect(() => assertWithinTrialLimits({ isTrial: false, claimedAt: null }, { resolution: [8000, 8000], priority: "rush" })).not.toThrow();
    try {
      assertWithinTrialLimits(trial, { resolution: [4000, 100], priority: "economy" });
    } catch (e) {
      expect((e as ApiError).toBody().error.hint).toContain("renda claim");
    }
  });

  it("defaults to a conservative resolution cap when unconfigured", () => {
    expect(() => assertWithinTrialLimits(trial, { resolution: [1920, 1080], priority: "standard" })).toThrow(/1280px/);
  });

  it("claim tokens are stored hashed", () => {
    expect(hashToken("abc")).toMatch(/^[0-9a-f]{64}$/);
    expect(hashToken("abc")).not.toContain("abc");
  });
});
