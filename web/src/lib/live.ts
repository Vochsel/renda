import type { JobLive } from "./db/schema";
import { HARDWARE, PRICE_PER_MINUTE, type Hardware } from "./pricing";

/** A heartbeat older than this means the container is gone (or the event got lost): stop extrapolating it. */
export const STALE_MS = 15_000;
/** Never extrapolate a running container more than this past its last heartbeat. */
export const MAX_EXTRAPOLATE_MS = 6_000;
/** Accept at most one meter write per job this often (a chunk's first heartbeat always lands). */
export const METER_WRITE_INTERVAL_MS = 2_500;

export type LiveCost = {
  /** Billed seconds so far + running containers' elapsed time, per hardware class. */
  seconds: Record<Hardware, number>;
  /** At list price: what the job is drawing from prepaid credit. */
  costUsd: number;
  activeContainers: number;
  /** $/s the running containers are adding now — clients interpolate with it between snapshots. */
  ratePerSecond: number;
  updatedAt: string;
};

type Billed = { secondsCpu: number; secondsGpuL4: number; secondsGpuL40s: number };

const perSecond = (hw: Hardware) => PRICE_PER_MINUTE[hw] / 60;
const isHardware = (h: string): h is Hardware => (HARDWARE as readonly string[]).includes(h);

/** Snapshot of what a running job has cost so far, as of `now`. */
export function liveCost(live: JobLive | null | undefined, billed: Billed, now: number): LiveCost {
  const seconds: Record<Hardware, number> = { cpu: billed.secondsCpu, "gpu-l4": billed.secondsGpuL4, "gpu-l40s": billed.secondsGpuL40s };
  let active = 0;
  let rate = 0;
  for (const c of Object.values(live?.chunks ?? {})) {
    if (c.final || !isHardware(c.hardware)) continue; // finished chunks are already in the billed columns
    const age = now - c.at;
    seconds[c.hardware] += c.elapsed + Math.max(0, Math.min(age, MAX_EXTRAPOLATE_MS)) / 1000;
    if (age <= STALE_MS) {
      active += 1;
      rate += perSecond(c.hardware);
    }
  }
  const costUsd = HARDWARE.reduce((sum, hw) => sum + seconds[hw] * perSecond(hw), 0);
  for (const hw of HARDWARE) seconds[hw] = Math.round(seconds[hw] * 10) / 10;
  return { seconds, costUsd: Math.round(costUsd * 1e5) / 1e5, activeContainers: active, ratePerSecond: rate, updatedAt: new Date(now).toISOString() };
}

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "canceled"]);

/** The moment a page takes its live snapshots (server components render once per request). */
export function snapshotNow(): number {
  return Date.now();
}

/** Props for <LiveCost> from a jobs row (server side). */
export function liveCostProps(j: Billed & { status: string; costUsd: number; cached: boolean; live: JobLive | null }, now = Date.now()) {
  return { status: j.status, costUsd: j.costUsd, cached: j.cached, live: TERMINAL_STATUSES.has(j.status) ? null : liveCost(j.live, j, now) };
}

/** Client-side: cost at time `t` interpolated from a snapshot, capped so a lost heartbeat can't run away. */
export function interpolateCost(snap: Pick<LiveCost, "costUsd" | "ratePerSecond" | "updatedAt">, t: number): number {
  const dt = Math.max(0, Math.min(t - Date.parse(snap.updatedAt), MAX_EXTRAPOLATE_MS + 4_000)) / 1000;
  return snap.costUsd + snap.ratePerSecond * dt;
}
