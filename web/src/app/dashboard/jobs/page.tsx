import Link from "next/link";
import { requireAccount } from "@/lib/auth";
import { listJobs, TERMINAL } from "@/lib/jobs";
import { fmtAgo, fmtDuration, fmtUsd } from "@/lib/format";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AutoRefresh } from "@/components/auto-refresh";
import { StatusBadge } from "@/components/status-badge";

export default async function JobsPage() {
  const { org } = await requireAccount();
  const jobs = await listJobs(org.id, { limit: 100 });
  const anyActive = jobs.some((j) => !TERMINAL.has(j.status));
  return (
    <div className="space-y-4">
      <AutoRefresh active={anyActive} ms={3000} />
      <h1 className="text-xl font-semibold">Jobs</h1>
      <Card>
        <CardContent className="pt-6">
          {jobs.length === 0 ? (
            <p className="text-muted-foreground text-sm">No jobs yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job</TableHead><TableHead>Status</TableHead><TableHead>Engine</TableHead><TableHead>Hardware</TableHead>
                  <TableHead className="text-right">Time</TableHead><TableHead className="text-right">Cost</TableHead><TableHead className="text-right">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((j) => {
                  const secs = j.secondsCpu + j.secondsGpuL4 + j.secondsGpuL40s;
                  return (
                    <TableRow key={j.id}>
                      <TableCell>
                        <Link href={`/dashboard/jobs/${j.id}`} className="font-mono text-xs hover:underline">{j.name ?? j.id}</Link>
                        <div className="text-muted-foreground text-[11px]">{j.kind}</div>
                      </TableCell>
                      <TableCell className="min-w-28">
                        <StatusBadge status={j.status} />
                        {!TERMINAL.has(j.status) && <Progress value={j.progress * 100} className="mt-1.5 h-1" />}
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">{j.engine}</TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">{j.hardware}</TableCell>
                      <TableCell className="text-right font-mono text-xs">{secs ? fmtDuration(secs) : "—"}</TableCell>
                      <TableCell className="text-right font-mono text-xs">{j.cached ? "cached" : fmtUsd(j.costUsd)}</TableCell>
                      <TableCell className="text-muted-foreground text-right text-xs">{fmtAgo(j.createdAt)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
