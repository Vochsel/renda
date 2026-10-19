import { describe, expect, it } from "vitest";
import { createHmac } from "node:crypto";
import { MAX_EVENT_SKEW_SECONDS, signBody, signTimed, verifySignature, verifyWorkerEvent } from "@/lib/hmac";

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

describe("timed worker signatures", () => {
  it("match the python worker byte for byte (shared vector)", () => {
    // worker/tests/test_events.py::test_timed_signature_vector signs the same input.
    expect(signTimed('{"jobId":"j","type":"status"}', 1700000000, "k"))
      .toBe("v1=1d77ff1e13f223f73a2a3115e9a42c4f4b66c1a6d30af94172543288cd1eb0d4");
  });
  it("accept within the window, report stale outside it, reject forgeries", () => {
    const body = '{"jobId":"j","type":"warning","message":"m"}';
    const ts = 1_700_000_000;
    const at = (seconds: number) => seconds * 1000;
    const h = (t: number, s = "k") => ({ signature: signTimed(body, t, s), timestamp: String(t) });
    expect(verifyWorkerEvent(body, h(ts), "k", at(ts + 120))).toBe("ok");
    expect(verifyWorkerEvent(body, h(ts), "k", at(ts + MAX_EVENT_SKEW_SECONDS + 1))).toBe("stale");
    expect(verifyWorkerEvent(body, h(ts), "k", at(ts - MAX_EVENT_SKEW_SECONDS - 1))).toBe("stale");
    expect(verifyWorkerEvent(body, h(ts, "x"), "k", at(ts))).toBe("bad");
    expect(verifyWorkerEvent(body, { signature: signTimed(body, ts, "k"), timestamp: String(ts + 1) }, "k", at(ts))).toBe("bad");
    expect(verifyWorkerEvent(body, { signature: signTimed(body, ts, "k"), timestamp: "soon" }, "k", at(ts))).toBe("bad");
    expect(verifyWorkerEvent(body, { signature: signBody(body, "k"), timestamp: null }, "k")).toBe("legacy");
    expect(verifyWorkerEvent(body, { signature: null, timestamp: null }, "k")).toBe("bad");
  });
});
