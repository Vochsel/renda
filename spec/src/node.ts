/**
 * Node loaders for {@link flattenPackage}: local files and SSRF-safe https.
 *
 * The https fetcher resolves the host itself and refuses private, loopback, link-local, CGNAT,
 * multicast and reserved addresses. The validated address is the one the socket connects to (custom
 * `lookup`), so DNS rebinding between check and connect can't redirect it. Redirects are followed
 * manually (https only, re-validated), with a size cap and timeout.
 */
import { lookup as dnsLookup, type LookupAddress } from "node:dns";
import { readFile } from "node:fs/promises";
import https from "node:https";
import { isIP } from "node:net";
import { dirname, resolve } from "node:path";
import { flattenPackage, LAYER_LIMITS, type FlattenOptions, type FlattenResult } from "./layers.js";

function v4ToInt(ip: string): number {
  return ip.split(".").reduce((n, o) => (n << 8) + Number(o), 0) >>> 0;
}

const V4_BLOCKED: [string, number][] = [
  ["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8], ["169.254.0.0", 16],
  ["172.16.0.0", 12], ["192.0.0.0", 24], ["192.0.2.0", 24], ["192.88.99.0", 24], ["192.168.0.0", 16],
  ["198.18.0.0", 15], ["198.51.100.0", 24], ["203.0.113.0", 24], ["224.0.0.0", 4], ["240.0.0.0", 4],
];

function v4Public(ip: string): boolean {
  const n = v4ToInt(ip);
  return !V4_BLOCKED.some(([net, bits]) => (n >>> (32 - bits)) === (v4ToInt(net) >>> (32 - bits)));
}

function expandV6(ip: string): number[] | null {
  let s = ip.toLowerCase().split("%")[0];
  // dotted IPv4 tail (::ffff:1.2.3.4) → two hex groups
  const v4 = s.match(/(\d+\.\d+\.\d+\.\d+)$/);
  if (v4) {
    const n = v4ToInt(v4[1]);
    s = s.slice(0, -v4[1].length) + `${(n >>> 16).toString(16)}:${(n & 0xffff).toString(16)}`;
  }
  const parse = (part: string) => (part ? part.split(":").map((x) => parseInt(x, 16)) : []);
  let groups: number[];
  if (s.includes("::")) {
    const [head, tail] = s.split("::");
    const h = parse(head);
    const t = parse(tail);
    if (h.length + t.length > 7) return null;
    groups = [...h, ...new Array(8 - h.length - t.length).fill(0), ...t];
  } else {
    groups = parse(s);
  }
  if (groups.length !== 8 || groups.some((g) => Number.isNaN(g) || g < 0 || g > 0xffff)) return null;
  return groups;
}

/** True for globally routable unicast addresses only. */
export function isPublicAddress(ip: string): boolean {
  const fam = isIP(ip);
  if (fam === 4) return v4Public(ip);
  if (fam !== 6) return false;
  const g = expandV6(ip);
  if (!g) return false;
  if (g.every((x) => x === 0)) return false; // ::
  if (g.slice(0, 7).every((x) => x === 0) && g[7] === 1) return false; // ::1
  const embedded = (hi: number, lo: number) => `${hi >>> 8}.${hi & 255}.${lo >>> 8}.${lo & 255}`;
  if (g.slice(0, 5).every((x) => x === 0) && g[5] === 0xffff) return v4Public(embedded(g[6], g[7])); // ::ffff:v4
  if (g[0] === 0x64 && g[1] === 0xff9b) return v4Public(embedded(g[6], g[7])); // NAT64
  if ((g[0] & 0xfe00) === 0xfc00) return false; // fc00::/7 unique local
  if ((g[0] & 0xffc0) === 0xfe80) return false; // fe80::/10 link local
  if ((g[0] & 0xff00) === 0xff00) return false; // multicast
  if (g[0] === 0x2001 && g[1] === 0x0db8) return false; // documentation
  if (g[0] === 0x2002) return v4Public(embedded(g[1], g[2])); // 6to4
  if (g[0] === 0x2001 && g[1] === 0x0000) return false; // Teredo tunnels (2001::/32)
  return true;
}

export interface SafeFetchOptions {
  timeoutMs?: number;
  maxBytes?: number;
  maxRedirects?: number;
  /** Whole-fetch budget incl. redirects (timeoutMs only bounds socket idle time). Default 30 s. */
  totalTimeoutMs?: number;
  /** For tests: allow these hostnames to resolve to non-public addresses. */
  allowHosts?: string[];
}

export class UnsafeUrlError extends Error {}

/** Resolve-and-check lookup: every address the name resolves to must be public. */
function guardedLookup(allow: Set<string>) {
  return (hostname: string, options: { all?: boolean; family?: number }, cb: (...args: any[]) => void) => {
    dnsLookup(hostname, { all: true, family: options.family }, (err, addrs: LookupAddress[]) => {
      if (err) return cb(err);
      const bad = addrs.find((a) => !isPublicAddress(a.address));
      if (bad && !allow.has(hostname)) return cb(new UnsafeUrlError(`${hostname} resolves to a non-public address (${bad.address})`));
      if (options.all) return cb(null, addrs);
      cb(null, addrs[0].address, addrs[0].family);
    });
  };
}

/** GET an https URL as text with SSRF protections. */
export async function safeFetchText(url: string, opts: SafeFetchOptions = {}): Promise<string> {
  const timeoutMs = opts.timeoutMs ?? 10_000;
  const maxBytes = opts.maxBytes ?? LAYER_LIMITS.maxBytes;
  const allow = new Set(opts.allowHosts ?? []);
  const deadline = Date.now() + (opts.totalTimeoutMs ?? 30_000);
  let current = url;
  for (let hop = 0; hop <= (opts.maxRedirects ?? 3); hop++) {
    const u = new URL(current);
    if (u.protocol !== "https:") throw new UnsafeUrlError(`only https:// URLs are allowed, got ${u.protocol}`);
    if (u.username || u.password) throw new UnsafeUrlError("credentials in URLs are not allowed");
    const host = u.hostname.replace(/^\[|\]$/g, "");
    if (isIP(host) && !isPublicAddress(host) && !allow.has(host)) throw new UnsafeUrlError(`${host} is not a public address`);
    const res = await new Promise<{ status: number; location?: string; body: string }>((ok, fail) => {
      const req = https.get(u, { lookup: guardedLookup(allow) as any, timeout: timeoutMs, headers: { "user-agent": "renda-layers/1", accept: "application/json" } }, (r) => {
        const status = r.statusCode ?? 0;
        if (status >= 300 && status < 400) {
          r.resume();
          return ok({ status, location: r.headers.location, body: "" });
        }
        const len = Number(r.headers["content-length"] ?? 0);
        if (len > maxBytes) {
          r.destroy();
          return fail(new Error(`response is ${len} bytes (limit ${maxBytes})`));
        }
        const chunks: Buffer[] = [];
        let size = 0;
        r.on("data", (c: Buffer) => {
          size += c.length;
          if (size > maxBytes) { r.destroy(); fail(new Error(`response exceeds ${maxBytes} bytes`)); return; }
          chunks.push(c);
        });
        r.on("end", () => ok({ status, body: Buffer.concat(chunks).toString("utf8") }));
        r.on("error", fail);
      });
      req.on("timeout", () => req.destroy(new Error(`timed out after ${timeoutMs} ms`)));
      req.on("error", fail);
      // A server that drips bytes never trips the idle timeout; cap the whole exchange too.
      const left = deadline - Date.now();
      const timer = setTimeout(() => req.destroy(new Error("fetch exceeded its total time budget")), Math.max(0, left));
      req.on("close", () => clearTimeout(timer));
    });
    if (res.status >= 300 && res.status < 400) {
      if (!res.location) throw new Error(`HTTP ${res.status} without a Location header`);
      current = new URL(res.location, current).href;
      continue;
    }
    if (res.status < 200 || res.status >= 300) throw new Error(`HTTP ${res.status}`);
    return res.body;
  }
  throw new Error("too many redirects");
}

/** Loaders for local tools (CLI, MCP stdio): files + safe https. asset:// needs the API. */
export function nodeLoaders(extra: Partial<FlattenOptions> = {}): FlattenOptions {
  return {
    readFile: (p) => readFile(p, "utf8"),
    fetchHttps: (u) => safeFetchText(u),
    ...extra,
  };
}

/** Read and flatten a package file. */
export async function flattenFile(path: string, extra: Partial<FlattenOptions> = {}): Promise<FlattenResult> {
  const abs = resolve(path);
  const doc = JSON.parse(await readFile(abs, "utf8"));
  return flattenPackage(doc, { ...nodeLoaders(), base: dirname(abs), label: abs, ...extra });
}
