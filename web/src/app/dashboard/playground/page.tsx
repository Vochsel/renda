import type { Metadata } from "next";
import Link from "next/link";
import { requireAccount } from "@/lib/auth";
import { listJobs } from "@/lib/jobs";
import { fmtAgo, fmtUsd } from "@/lib/format";
import { Playground } from "@/components/playground/playground";
import { StatusBadge } from "@/components/status-badge";
import { estimatePlayground, getPlaygroundJob, registerUploads, submitPlayground } from "./actions";

export const metadata: Metadata = { title: "Playground" };

export default async function PlaygroundPage() {
  const { org } = await requireAccount();
  const recent = await listJobs(org.id, { limit: 8 });
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Playground</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Drop a 3D file, pick what to make, and see the cost before you run it. The same job is one CLI command or MCP call away.
        </p>
      </div>
      <Playground actions={{ registerUploads, estimate: estimatePlayground, submit: submitPlayground, getJob: getPlaygroundJob }} />
      {recent.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">Recent jobs</h2>
            <Link href="/dashboard/gallery" className="text-muted-foreground text-xs hover:underline">Gallery →</Link>
          </div>
          <ul className="divide-border border-border divide-y rounded-lg border text-sm">
            {recent.map((j) => (
              <li key={j.id} className="flex items-center gap-3 px-3 py-2">
                <StatusBadge status={j.status} />
                <Link href={`/dashboard/jobs/${j.id}`} className="min-w-0 flex-1 truncate hover:underline">{j.name ?? j.id}</Link>
                <span className="text-muted-foreground hidden font-mono text-[11px] sm:inline">{j.engine ?? ""}</span>
                <span className="text-muted-foreground w-16 text-right font-mono text-[11px]">{fmtUsd(Number(j.costUsd ?? 0))}</span>
                <span className="text-muted-foreground w-16 text-right text-[11px]">{fmtAgo(j.createdAt)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
