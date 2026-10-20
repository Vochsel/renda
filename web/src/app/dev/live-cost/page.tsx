import { notFound } from "next/navigation";
import { ActiveJobs } from "@/components/active-jobs";
import { LiveCost } from "@/components/live-cost";
import { liveCost, snapshotNow } from "@/lib/live";

/** Dev-only preview of live cost (fixture snapshots, no database). 404 in production builds. */
export default function DevLiveCost() {
  if (process.env.NODE_ENV === "production") notFound();
  const now = snapshotNow();
  const snap = (chunks: [string, string, number][], billedGpu = 0) =>
    liveCost(
      { updatedAt: now, chunks: Object.fromEntries(chunks.map(([c, hw, el]) => [c, { hardware: hw, elapsed: el, at: now }])) },
      { secondsCpu: 2, secondsGpuL4: billedGpu, secondsGpuL40s: 0 },
      now,
    );
  const jobs = [
    { id: "job_turntable", name: "sneaker turntable · Cycles", status: "rendering", progress: 0.42, engine: "blender-cycles", hardware: "gpu-l4",
      live: snap([["0", "gpu-l4", 12], ["1", "gpu-l4", 11], ["2", "gpu-l4", 12], ["3", "gpu-l4", 10], ["orchestrate", "cpu", 3]], 30) },
    { id: "job_splat", name: "porsche → splat (high)", status: "rendering", progress: 0.71, engine: "blender-cycles", hardware: "gpu-l40s",
      live: snap([["train", "gpu-l40s", 214]], 0) },
    { id: "job_still", name: "bracket.step · Mitsuba", status: "planning", progress: 0.05, engine: "mitsuba", hardware: "gpu-l4",
      live: snap([["orchestrate", "cpu", 1.2]]) },
  ];
  return (
    <main className="mx-auto max-w-3xl space-y-8 p-8">
      <section>
        <h1 className="mb-4 text-sm font-semibold">Active jobs</h1>
        <ActiveJobs jobs={jobs} />
      </section>
      <section className="space-y-2 text-sm">
        <h2 className="font-semibold">LiveCost</h2>
        <p>Running: <LiveCost job={{ status: "rendering", costUsd: 0, live: jobs[0].live }} /></p>
        <p>Finished: <LiveCost job={{ status: "succeeded", costUsd: 0.0231 }} /></p>
        <p>Cached: <LiveCost job={{ status: "succeeded", costUsd: 0, cached: true }} /></p>
      </section>
    </main>
  );
}
