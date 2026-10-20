"use client";

import { useEffect, useState } from "react";
import { interpolateCost, type LiveCost as LiveSnapshot } from "@/lib/live";
import { cn } from "@/lib/utils";

const TERMINAL = new Set(["succeeded", "failed", "canceled"]);

export type LiveCostJob = {
  status: string;
  /** Billed cost (final once the job is finished; can be below list price on free minutes). */
  costUsd: number;
  cached?: boolean;
  live?: LiveSnapshot | null;
};

function tickMs() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 1000 : 100;
}

/**
 * Cost of a job right now. While running it ticks up between server snapshots at the running containers' rate
 * (never backwards); once finished it is the billed amount. First paint (server + hydration) is the snapshot.
 */
export function useLiveCost(job: LiveCostJob): { value: number; running: boolean } {
  const live = !TERMINAL.has(job.status) ? job.live ?? null : null;
  const [shown, setShown] = useState(() => live?.costUsd ?? 0);

  useEffect(() => {
    if (!live) return;
    const step = () => setShown((prev) => Math.max(prev, interpolateCost(live, Date.now())));
    step();
    const t = setInterval(step, tickMs());
    return () => clearInterval(t);
  }, [live]);

  if (!live) return { value: job.costUsd, running: false };
  return { value: Math.max(shown, live.costUsd), running: true };
}

export function fmtLiveUsd(v: number) {
  return v < 0.01 ? `$${v.toFixed(4)}` : v < 10 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;
}

/** `≈ $0.0123 so far` while running, the billed cost once done. */
export function LiveCost({ job, className, suffix = true }: { job: LiveCostJob; className?: string; suffix?: boolean }) {
  const { value, running } = useLiveCost(job);
  if (job.cached && !running) return <span className={cn("font-mono tabular-nums", className)}>cached</span>;
  return (
    <span
      className={cn("font-mono tabular-nums", className)}
      title={running ? "Billed so far plus running containers, at list price. The final bill can be lower (free minutes)." : undefined}
    >
      {running && <span className="text-muted-foreground">≈ </span>}
      {fmtLiveUsd(value)}
      {running && suffix && <span className="text-muted-foreground"> so far</span>}
    </span>
  );
}
