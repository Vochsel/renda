"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { interpolateCost, type LiveCost as LiveSnapshot } from "@/lib/live";
import { fmtLiveUsd } from "@/components/live-cost";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/status-badge";

export type ActiveJob = {
  id: string;
  name: string | null;
  status: string;
  progress: number;
  engine: string | null;
  hardware: string | null;
  live: LiveSnapshot;
};

/** Running jobs with progress and cost ticking up, plus the total being spent right now. */
export function ActiveJobs({ jobs }: { jobs: ActiveJob[] }) {
  // First paint (server and hydration) shows the snapshots as-is; ticking starts after mount. Values never go
  // backwards when a fresh snapshot lands slightly below the interpolated one.
  const [shown, setShown] = useState<Record<string, number>>({});

  useEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const step = () => {
      const t = Date.now();
      setShown((prev) => Object.fromEntries(jobs.map((j) => [j.id, Math.max(prev[j.id] ?? 0, interpolateCost(j.live, t))])));
    };
    step();
    const t = setInterval(step, reduce ? 1000 : 100);
    return () => clearInterval(t);
  }, [jobs]);

  const costs = jobs.map((j) => Math.max(shown[j.id] ?? 0, j.live.costUsd));
  const total = costs.reduce((a, b) => a + b, 0);
  const containers = jobs.reduce((n, j) => n + j.live.activeContainers, 0);
  const rate = jobs.reduce((n, j) => n + j.live.ratePerSecond, 0);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-mono text-2xl tabular-nums">
          <span className="text-muted-foreground text-base">≈ </span>
          {fmtLiveUsd(total)}
        </span>
        <span className="text-muted-foreground font-mono text-[11px] tabular-nums">
          {jobs.length} running · {containers} container{containers === 1 ? "" : "s"} · {fmtLiveUsd(rate * 60)}/min now · list price
        </span>
      </div>
      <ul className="divide-border divide-y">
        {jobs.map((j, i) => (
          <li key={j.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 py-2 sm:grid-cols-[minmax(0,1fr)_9rem_6rem]">
            <div className="min-w-0">
              <Link href={`/dashboard/jobs/${j.id}`} className="block truncate font-mono text-xs hover:underline">{j.name ?? j.id}</Link>
              <div className="text-muted-foreground font-mono text-[11px]">{[j.engine, j.hardware].filter(Boolean).join(" · ") || "planning"}</div>
            </div>
            <div className="hidden sm:block">
              <StatusBadge status={j.status} />
              <Progress value={j.progress * 100} className="mt-1.5 h-1" />
            </div>
            <span className="text-right font-mono text-xs tabular-nums">{fmtLiveUsd(costs[i])}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
