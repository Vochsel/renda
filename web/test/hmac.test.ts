import { describe, expect, it } from "vitest";
import { createHmac } from "node:crypto";
import { signBody, verifySignature } from "@/lib/hmac";

describe("worker HMAC", () => {
  const secret = "s3cret";
  const body = JSON.stringify({ jobId: "job_1", type: "usage", seconds: 12.5, chunk: 0, hardware: "gpu-l4" });
  it("matches the python worker's format", () => {
    const hex = createHmac("sha256", secret).update(body).digest("hex");
    expect(signBody(body, secret)).toBe(`sha256=${hex}`);
  });
  it("verifies good signatures", () => {
    expect(verifySignature(body, signBody(body, secret), secret)).toBe(true);
  });
  it("rejects tampered body, wrong secret, missing or malformed header", () => {
    expect(verifySignature(body + " ", signBody(body, secret), secret)).toBe(false);
    expect(verifySignature(body, signBody(body, "other"), secret)).toBe(false);
    expect(verifySignature(body, null, secret)).toBe(false);
    expect(verifySignature(body, "sha256=abc", secret)).toBe(false);
  });
});
