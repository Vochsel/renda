import { describe, expect, it, vi } from "vitest";

vi.mock("next/server", () => ({ after: (fn: () => unknown) => fn() }));

import { clientOf, track } from "@/lib/posthog";

const req = (ua?: string) => new Request("https://renda.test/api/v1/jobs", { headers: ua ? { "user-agent": ua } : {} });

describe("clientOf", () => {
  it("names our clients from their User-Agent", () => {
    expect(clientOf(req("renda-cli/0.1.0"))).toBe("renda-cli");
    expect(clientOf(req("renda-mcp/0.1.0 (remote)"))).toBe("renda-mcp");
    expect(clientOf(req("renda-py/0.1.0"))).toBe("renda-py");
    expect(clientOf(req("Mozilla/5.0 (Macintosh)"))).toBe("browser");
    expect(clientOf(req("curl/8.7.1"))).toBe("http");
    expect(clientOf(req())).toBe("unknown");
  });
});

describe("track", () => {
  it("is a no-op without a PostHog key", () => {
    delete process.env.NEXT_PUBLIC_POSTHOG_KEY;
    expect(() => track("job_submitted", { orgId: "org_1" })).not.toThrow();
  });
});
