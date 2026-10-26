import Link from "next/link";
import { requireAccount } from "@/lib/auth";
import { getBilling } from "@/lib/accounts";
import { monthRange, usageSummary } from "@/lib/billing";
import { listActiveJobs, listJobs, TERMINAL } from "@/lib/jobs";
import { liveCostProps, snapshotNow } from "@/lib/live";
import { listOutputs } from "@/lib/outputs";
import { fmtAgo, fmtMinutes, fmtUsd } from "@/lib/format";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Code } from "@/components/code";
import { SpendChart } from "@/components/spend-chart";
import { StatusBadge } from "@/components/status-badge";
import { RecentStrip } from "@/components/gallery/recent-strip";
import { ActiveJobs } from "@/components/active-jobs";
import { AutoRefresh } from "@/components/auto-refresh";
import { LiveCost } from "@/components/live-cost";

export default async function Overview() {
  const { org } = await requireAccount();
  const range = monthRange();
  const [usage, billing, recent, renders, running] = await Promise.all([
    usageSummary(org.id, range.from, range.to),
    getBilling(org.id),
    listJobs(org.id, { limit: 8 }),
    listOutputs(org.id, { limit: 10 }),
    listActiveJobs(org.id),
  ]);
  const now = snapshotNow();
  const active = running.map((j) => ({
    id: j.id, name: j.name, status: j.status, progress: j.progress, engine: j.engine, hardware: j.hardware,
    live: liveCostProps(j, now).live!,
  }));
  const totalSeconds = usage.seconds.cpu + usage.seconds.gpu;
  // Prepaid credit in USD. Read defensively: the billing row gains `creditUsd` with the prepaid-credit backend.
  const credit = Number((billing as { creditUsd?: unknown }).creditUsd ?? 0) || 0;

  return (
    <div className="space-y-6">
      <AutoRefresh active={active.length > 0} ms={3000} />
      <h1 className="text-xl font-semibold">Overview</h1>
      {active.length > 0 && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-sm">Active jobs</CardTitle>
            <Link href="/dashboard/jobs" className="text-muted-foreground text-sm hover:underline">All jobs</Link>
          </CardHeader>
          <CardContent><ActiveJobs jobs={active} /></CardContent>
        </Card>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Render time this month" value={fmtMinutes(totalSeconds)} sub={`${fmtMinutes(usage.seconds.gpu)} GPU · ${fmtMinutes(usage.seconds.cpu)} CPU`} />
        <Stat label="Spend this month" value={fmtUsd(usage.costUsd)} sub={`${usage.minutesBilled} billed minutes`} />
        <Stat label="Credit left" value={fmtUsd(credit)}
          sub={credit > 0 ? "Prepaid; renders draw it down per second" : "Buy credit to render"}
          href={{ to: "/dashboard/billing", label: credit > 0 ? "Add credit" : "Buy credit" }} />
      </div>
      {renders.items.length > 0 && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-sm">Recent renders</CardTitle>
            <Link href="/dashboard/gallery" className="text-muted-foreground text-sm hover:underline">Gallery</Link>
          </CardHeader>
          <CardContent><RecentStrip items={renders.items} /></CardContent>
        </Card>
      )}
      <Card>
        <CardHeader><CardTitle className="text-sm">Daily render time</CardTitle></CardHeader>
        <CardContent><SpendChart days={usage.byDay} monthStart={range.from} /></CardContent>
      </Card>
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="text-sm">Recent jobs</CardTitle>
          <Link href="/dashboard/jobs" className="text-muted-foreground text-sm hover:underline">All jobs</Link>
        </CardHeader>
        <CardContent>
          {recent.length ? (
            <Table>
              <TableHeader><TableRow><TableHead>Job</TableHead><TableHead>Status</TableHead><TableHead>Engine</TableHead><TableHead className="text-right">Cost</TableHead><TableHead className="text-right">Created</TableHead></TableRow></TableHeader>
              <TableBody>
                {recent.map((j) => (
                  <TableRow key={j.id}>
                    <TableCell><Link href={`/dashboard/jobs/${j.id}`} className="font-mono text-xs hover:underline">{j.name ?? j.id}</Link></TableCell>
                    <TableCell><StatusBadge status={j.status} /></TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">{j.engine}</TableCell>
                    <TableCell className="text-right text-xs">
                      {TERMINAL.has(j.status) ? <span className="font-mono">{j.cached ? "cached" : fmtUsd(j.costUsd)}</span> : <LiveCost job={liveCostProps(j, now)} suffix={false} />}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-right text-xs">{fmtAgo(j.createdAt)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="space-y-3">
              <CardDescription>No renders yet. Create an API key, then:</CardDescription>
              <Code>{`npx @rendahq/cli render model.glb        # or: uvx renda render model.glb --cloud\nexport RENDA_API_KEY=rnd_live_…            # CI / servers`}</Code>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value, sub, href }: { label: string; value: string; sub: string; href?: { to: string; label: string } }) {
  return (
    <Card>
      <CardHeader className="pb-2"><CardDescription>{label}</CardDescription></CardHeader>
      <CardContent>
        <div className="font-mono text-2xl font-semibold">{value}</div>
        <div className="text-muted-foreground mt-1 text-xs">
          {sub}
          {href && <>{" · "}<Link href={href.to} className="underline underline-offset-2">{href.label}</Link></>}
        </div>
      </CardContent>
    </Card>
  );
}
