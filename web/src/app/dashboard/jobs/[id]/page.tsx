import Link from "next/link";
import { notFound } from "next/navigation";
import { requireAccount } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { getJob, serializeJob, TERMINAL } from "@/lib/jobs";
import { fmtBytes, fmtDuration, fmtUsd } from "@/lib/format";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { AutoRefresh } from "@/components/auto-refresh";
import { LiveCost } from "@/components/live-cost";
import { Code } from "@/components/code";
import { StatusBadge } from "@/components/status-badge";
import { cancelJobAction } from "./actions";

export default async function JobPage({ params }: PageProps<"/dashboard/jobs/[id]">) {
  const { id } = await params;
  const { org } = await requireAccount();
  const row = await getJob(org.id, id).catch((e) => {
    if (e instanceof ApiError && e.code === "not_found") notFound();
    throw e;
  });
  const job = await serializeJob(row);
  const active = !TERMINAL.has(job.status);
  const est = job.estimate as { estCostUsd?: [number, number]; estWallSeconds?: [number, number] } | undefined;

  return (
    <div className="space-y-6">
      <AutoRefresh active={active} />
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/dashboard/jobs" className="text-muted-foreground text-sm hover:underline">Jobs</Link>
        <span className="text-muted-foreground">/</span>
        <h1 className="font-mono text-lg font-semibold">{job.name ?? job.id}</h1>
        <StatusBadge status={job.status} />
        {active && (
          <form action={cancelJobAction.bind(null, job.id)} className="ml-auto">
            <Button variant="outline" size="sm">Cancel</Button>
          </form>
        )}
      </div>

      {active && (
        <div className="space-y-1">
          <Progress value={job.progress * 100} />
          <p className="text-muted-foreground text-xs">{Math.round(job.progress * 100)}% · {job.status}</p>
        </div>
      )}

      {job.error && (
        <Alert variant="destructive"><AlertTitle>Render failed</AlertTitle><AlertDescription className="font-mono text-xs whitespace-pre-wrap">{job.error}</AlertDescription></Alert>
      )}
      {job.warnings.length > 0 && (
        <Alert>
          <AlertTitle>Warnings</AlertTitle>
          <AlertDescription><ul className="list-disc pl-4 text-xs">{job.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 sm:grid-cols-4">
        <Meta label="Engine" value={job.engine ?? "—"} />
        <Meta label="Hardware" value={job.hardware ?? "—"} />
        <Meta label="Render time" value={job.billedSeconds ? fmtDuration(job.billedSeconds) : "—"} />
        <Meta label="Cost" value={job.cached ? "$0 (cached)" : <LiveCost job={job} />}
          sub={est?.estCostUsd ? `est. ${fmtUsd(est.estCostUsd[0])}–${fmtUsd(est.estCostUsd[1])}` : undefined} />
      </div>

      {job.outputs.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Outputs</CardTitle></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            {job.outputs.map((o) => (
              <figure key={o.name} className="space-y-2">
                {o.contentType.startsWith("image/") && o.contentType !== "image/x-exr" ? (
                  // eslint-disable-next-line @next/next/no-img-element -- presigned, short-lived R2 URLs; next/image would cache them
                  <img src={o.url} alt={o.name} className="bg-muted w-full rounded-md border object-contain" />
                ) : o.contentType.startsWith("video/") ? (
                  <video src={o.url} controls loop playsInline className="bg-muted w-full rounded-md border" />
                ) : null}
                <figcaption className="flex items-center justify-between text-xs">
                  <span className="font-mono">{o.name}</span>
                  <a href={o.url} download={o.name} className="text-muted-foreground hover:underline">{fmtBytes(o.bytes)} · download</a>
                </figcaption>
              </figure>
            ))}
          </CardContent>
        </Card>
      )}

      <Separator />
      <div>
        <h2 className="mb-2 text-sm font-medium">Package</h2>
        <Code>{JSON.stringify(row.package, null, 2)}</Code>
      </div>
    </div>
  );
}

function Meta({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="border-border rounded-lg border p-3">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 font-mono text-sm">{value}</div>
      {sub && <div className="text-muted-foreground mt-0.5 text-[11px]">{sub}</div>}
    </div>
  );
}
