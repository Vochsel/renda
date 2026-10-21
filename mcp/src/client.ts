import type { RendaPackageInput } from "@rendahq/spec";

export type JobStatus = "queued" | "planning" | "rendering" | "encoding" | "succeeded" | "failed" | "canceled";
export type Hardware = "cpu" | "gpu-l4" | "gpu-l40s";
export type Priority = "economy" | "standard" | "rush";
export type JobKind = "scene" | "hyperframes";

export interface JobOutput {
  name: string;
  url: string;
  contentType: string;
  bytes: number;
}

export interface Job {
  id: string;
  kind: JobKind;
  status: JobStatus;
  progress: number;
  engine?: string;
  hardware?: Hardware;
  createdAt: string;
  startedAt?: string;
  finishedAt?: string;
  billedSeconds: number;
  costUsd: number;
  cached: boolean;
  warnings: string[];
  error?: string;
  outputs: JobOutput[];
  /** While running: billed so far + running containers at list price (the final bill can be lower). */
  live?: LiveCost;
}

export interface LiveCost {
  seconds: Record<Hardware, number>;
  costUsd: number;
  activeContainers: number;
  ratePerSecond: number;
  updatedAt: string;
}

/** "≈$0.0123 so far" for a running job, "" otherwise. */
export function liveCostLabel(job: Pick<Job, "live">): string {
  const v = job.live?.costUsd;
  if (v === undefined) return "";
  return `≈$${v < 0.01 ? v.toFixed(4) : v.toFixed(3)} so far`;
}

export const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set(["succeeded", "failed", "canceled"]);

/** HyperFrames video job package (kind "hyperframes"). */
export interface HyperframesPackage {
  project: string;
  name?: string;
  composition?: string;
  width?: number;
  height?: number;
  fps?: number;
  format?: "mp4" | "webm" | "mov" | "gif";
  /** HyperFrames encoder presets (draft → fast iteration, delivery → final). */
  quality?: "draft" | "looks" | "delivery";
  durationSec?: number;
  /** Composition variables; asset:// / https:// media values are downloaded into the project by the worker. */
  variables?: Record<string, unknown>;
}

export interface Me {
  user: { id: string; email: string; name?: string };
  org: { id: string; name?: string };
  balance: { freeSecondsLeft: number };
  billing: { hasPaymentMethod: boolean };
}

export interface UploadResponse {
  assetUri: string;
  uploadUrl?: string;
  exists: boolean;
}

export interface Estimate {
  hardware: Hardware;
  estSeconds: [number, number];
  estCostUsd: [number, number];
  warnings: string[];
}

export interface Usage {
  seconds: { cpu: number; gpu: number };
  minutesBilled: number;
  costUsd: number;
  byDay: { date: string; seconds: number; costUsd: number }[];
}

export interface DeviceAuth {
  deviceCode: string;
  userCode: string;
  verificationUri: string;
  verificationUriComplete?: string;
  interval: number;
  expiresIn: number;
}

export interface VerifyResponse {
  apiKey: string;
  user: Me["user"];
  org: Me["org"];
}

export interface TrialResponse {
  apiKey: string;
  claimUrl: string;
  freeSecondsLeft: number;
  expiresAt: string;
  limits?: { maxDimension: number; priorities: string[] };
  next?: string[];
}

export interface Pricing {
  [k: string]: unknown;
}

export class RendaApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly hint?: string,
  ) {
    super(message);
    this.name = "RendaApiError";
  }

  /** Human/agent-readable one-liner including the next-step hint. */
  toAgentText(): string {
    return `Renda error ${this.code} (${this.status}): ${this.message}${this.hint ? `\nNext step: ${this.hint}` : ""}`;
  }
}

export interface RendaClientOptions {
  baseUrl?: string;
  apiKey?: string;
  fetch?: typeof fetch;
  /** Sent as User-Agent from Node (browsers ignore it). Lets the API tell CLI, MCP and scripts apart. */
  userAgent?: string;
}

/** Production API. Override with RENDA_API_URL. (Domain may move to renda.online.) */
export const DEFAULT_API_URL = "https://renda.vochsel.com";

export class RendaClient {
  baseUrl: string;
  apiKey?: string;
  private readonly fetchImpl: typeof fetch;
  private readonly userAgent: string;

  constructor({ baseUrl = DEFAULT_API_URL, apiKey, fetch: fetchImpl, userAgent = "renda-mcp/0.1.0" }: RendaClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.apiKey = apiKey;
    this.userAgent = userAgent;
    this.fetchImpl = fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  async request<T>(method: string, path: string, body?: unknown, opts: { auth?: boolean } = {}): Promise<T> {
    const auth = opts.auth ?? true;
    if (auth && !this.apiKey) {
      throw new RendaApiError(401, "unauthenticated", "No Renda API key configured.",
        "Start a free trial (renda_trial / `renda trial`), or call renda_signup with the user's email, ask the user for the 6-digit code from the email, then renda_verify. Or set RENDA_API_KEY.");
    }
    const headers: Record<string, string> = { accept: "application/json", "user-agent": this.userAgent };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (auth && this.apiKey) headers.authorization = `Bearer ${this.apiKey}`;
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}/api/v1${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new RendaApiError(0, "network_error", `Could not reach ${this.baseUrl}: ${(e as Error).message}`,
        "Check network access and RENDA_API_URL, then retry.");
    }
    const text = await res.text();
    let data: any = undefined;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = undefined;
      }
    }
    if (!res.ok) {
      const err = data?.error;
      if (err && typeof err === "object") {
        throw new RendaApiError(res.status, err.code ?? `http_${res.status}`, err.message ?? res.statusText, err.hint);
      }
      // device flow uses OAuth-style string errors
      if (typeof err === "string") throw new RendaApiError(res.status, err, data?.error_description ?? err);
      throw new RendaApiError(res.status, `http_${res.status}`, text.slice(0, 500) || res.statusText);
    }
    return data as T;
  }

  // auth
  magicStart(email: string) {
    return this.request<{ ok: boolean }>("POST", "/auth/magic/start", { email }, { auth: false });
  }
  magicVerify(email: string, code: string, keyName?: string) {
    return this.request<VerifyResponse>("POST", "/auth/magic/verify", { email, code, keyName }, { auth: false });
  }
  /** Anonymous claimable trial: API key + a few free render-minutes, no signup. */
  trial() {
    return this.request<TrialResponse>("POST", "/auth/trial", {}, { auth: false });
  }
  deviceStart() {
    return this.request<DeviceAuth>("POST", "/auth/device", {}, { auth: false });
  }
  deviceToken(deviceCode: string) {
    return this.request<{ apiKey: string }>("POST", "/auth/device/token", { deviceCode }, { auth: false });
  }

  me() {
    return this.request<Me>("GET", "/me");
  }
  pricing() {
    return this.request<Pricing>("GET", "/pricing", undefined, { auth: false });
  }

  // files
  createUpload(input: { filename: string; size: number; sha256: string; contentType?: string }) {
    return this.request<UploadResponse>("POST", "/uploads", input);
  }
  async putUpload(uploadUrl: string, data: Uint8Array, contentType = "application/octet-stream") {
    const res = await this.fetchImpl(uploadUrl, {
      method: "PUT",
      headers: { "content-type": contentType },
      body: data as unknown as BodyInit,
    });
    if (!res.ok) throw new RendaApiError(res.status, "upload_failed", `Upload PUT failed: ${res.status} ${res.statusText}`, "Retry the upload; presigned URLs expire after a few minutes.");
  }

  // jobs
  estimate(pkg: RendaPackageInput | HyperframesPackage, kind: JobKind = "scene", priority?: "economy" | "standard" | "rush") {
    return this.request<Estimate>("POST", "/estimate", { package: pkg, kind, ...(priority ? { priority } : {}) });
  }

  /** Resolve `extends` / package references server-side (https and asset:// layers). */
  flattenPackage(pkg: Record<string, unknown>) {
    return this.request<{ package: Record<string, unknown>; provenance: Record<string, string>; layers: string[] }>(
      "POST", "/packages/flatten", { package: pkg });
  }
  createJob(input: { package: RendaPackageInput | HyperframesPackage; kind?: JobKind; priority?: Priority; idempotencyKey?: string }) {
    return this.request<Job>("POST", "/jobs", input);
  }
  getJob(id: string) {
    return this.request<Job>("GET", `/jobs/${encodeURIComponent(id)}`);
  }
  async listJobs(params: { limit?: number; status?: JobStatus } = {}): Promise<Job[]> {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.status) q.set("status", params.status);
    const qs = q.toString();
    const res = await this.request<Job[] | { jobs: Job[] }>("GET", `/jobs${qs ? `?${qs}` : ""}`);
    return Array.isArray(res) ? res : res.jobs;
  }
  cancelJob(id: string) {
    return this.request<Job>("POST", `/jobs/${encodeURIComponent(id)}/cancel`, {});
  }

  /** Poll until the job reaches a terminal status. */
  async waitForJob(id: string, opts: { intervalMs?: number; timeoutMs?: number; onProgress?: (job: Job) => void | Promise<void>; signal?: AbortSignal } = {}): Promise<Job> {
    const interval = opts.intervalMs ?? 2000;
    const deadline = Date.now() + (opts.timeoutMs ?? 30 * 60_000);
    for (;;) {
      const job = await this.getJob(id);
      await opts.onProgress?.(job);
      if (TERMINAL_STATUSES.has(job.status)) return job;
      if (Date.now() > deadline || opts.signal?.aborted) return job;
      await new Promise((r) => setTimeout(r, interval));
    }
  }

  // account
  usage(params: { from?: string; to?: string } = {}) {
    const q = new URLSearchParams(params as Record<string, string>).toString();
    return this.request<Usage>("GET", `/usage${q ? `?${q}` : ""}`);
  }
  billingCheckout() {
    return this.request<{ url: string }>("POST", "/billing/checkout", {});
  }
  billingPortal() {
    return this.request<{ url: string }>("GET", "/billing/portal");
  }
}
