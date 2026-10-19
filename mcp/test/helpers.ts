import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { RendaClient } from "../src/client.js";
import { createRendaServer } from "../src/server.js";

export type Route = (req: { method: string; url: string; body: any; headers: Record<string, string> }) => { status?: number; json?: unknown; text?: string } | undefined;

/** A fetch stub that records calls and answers from `route`. */
export function fakeFetch(route: Route) {
  const calls: { method: string; url: string; body: any; headers: Record<string, string> }[] = [];
  const fn = (async (input: any, init: any = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    const headers = (init.headers ?? {}) as Record<string, string>;
    let body: any = init.body;
    if (typeof body === "string") {
      try { body = JSON.parse(body); } catch { /* raw */ }
    }
    const req = { method, url, body, headers };
    calls.push(req);
    const r = route(req) ?? { status: 404, json: { error: { code: "not_found", message: `no route ${method} ${url}` } } };
    const text = r.text ?? (r.json === undefined ? "" : JSON.stringify(r.json));
    return new Response(text, { status: r.status ?? 200, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
  return { fetch: fn, calls };
}

export async function connect(client: RendaClient, mode: "local" | "remote" = "local", onApiKey?: (k: string) => void, extra: { onTrial?: (t: any) => void } = {}) {
  const server = createRendaServer({ client, mode, onApiKey, onTrial: extra.onTrial, pollIntervalMs: 1 });
  const [a, b] = InMemoryTransport.createLinkedPair();
  const mcp = new Client({ name: "test", version: "0" });
  await Promise.all([server.connect(a), mcp.connect(b)]);
  return mcp;
}

export const job = (over: Record<string, unknown> = {}) => ({
  id: "job_1", kind: "scene", status: "queued", progress: 0, createdAt: "2026-10-19T00:00:00Z",
  billedSeconds: 0, costUsd: 0, cached: false, warnings: [], outputs: [], ...over,
});
