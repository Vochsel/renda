import { after } from "next/server";
import { PostHog } from "posthog-node";

/**
 * Server-side product analytics + error tracking (PostHog). No-op without NEXT_PUBLIC_POSTHOG_KEY, so
 * self-hosters, previews and tests send nothing. Events are keyed by Renda user id when there is one
 * (matching posthog-js `identify` in the dashboard) and grouped by organization; trial/anonymous
 * activity is keyed by org id. Never put package contents or secrets in properties.
 */
let client: PostHog | null | undefined;

function ph(): PostHog | null {
  if (client !== undefined) return client;
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  client = key
    ? new PostHog(key, {
        host: process.env.POSTHOG_HOST ?? "https://us.i.posthog.com",
        // Serverless: send right away and flush after the response instead of batching in memory.
        flushAt: 1,
        flushInterval: 0,
      })
    : null;
  return client;
}

const BASE = { app: "renda", source: "server", environment: process.env.VERCEL_ENV ?? process.env.NODE_ENV };

/** Flush after the response is sent (or immediately when outside a request scope, e.g. scripts). */
function flushLater(p: PostHog) {
  try {
    after(() => p.flush());
  } catch {
    void p.flush().catch(() => {});
  }
}

export type Actor = { userId?: string | null; orgId?: string | null };

function distinctId(a: Actor): string {
  return a.userId ?? (a.orgId ? `org:${a.orgId}` : "anonymous");
}

export function track(event: string, actor: Actor, properties: Record<string, unknown> = {}) {
  const p = ph();
  if (!p) return;
  try {
    p.capture({
      distinctId: distinctId(actor),
      event,
      properties: { ...BASE, ...properties, ...(actor.userId ? {} : { $process_person_profile: false }) },
      groups: actor.orgId ? { organization: actor.orgId } : undefined,
    });
    flushLater(p);
  } catch (e) {
    console.warn("posthog track", e);
  }
}

/** Person/org properties, set once at signup or when they change. */
export function identify(actor: Required<Pick<Actor, "userId">> & Actor, person: Record<string, unknown>, org?: Record<string, unknown>) {
  const p = ph();
  if (!p || !actor.userId) return;
  try {
    p.identify({ distinctId: actor.userId, properties: person });
    if (actor.orgId && org) p.groupIdentify({ groupType: "organization", groupKey: actor.orgId, properties: org });
    flushLater(p);
  } catch (e) {
    console.warn("posthog identify", e);
  }
}

export function captureError(error: unknown, context: Record<string, unknown> & Actor = {}) {
  const p = ph();
  if (!p) return;
  try {
    const { userId, orgId, ...props } = context;
    p.captureException(error, distinctId({ userId, orgId }), { ...BASE, ...props, ...(orgId ? { $groups: { organization: orgId } } : {}) });
    flushLater(p);
  } catch (e) {
    console.warn("posthog captureException", e);
  }
}

/** Which client made an API call, from the User-Agent our CLIs/MCP send (`renda-cli/0.1.0 …`). */
export function clientOf(req: Request): string {
  const ua = req.headers.get("user-agent") ?? "";
  const m = ua.match(/\b(renda-(?:cli|mcp|py|playground))\//);
  if (m) return m[1];
  if (/Mozilla\//.test(ua)) return "browser";
  if (/^(curl|python-requests|httpx|node|undici|axios)/i.test(ua)) return "http";
  return ua ? "other" : "unknown";
}
