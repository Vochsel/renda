import type { Metadata } from "next";
import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { PageToc, type TocItem } from "@/components/page-toc";
import { DataTable, Legend, LineChart, MultiDot, StatTile, VIZ_STYLE, type SeriesSpec } from "@/components/benchmarks/charts";
import { CompareSlider, CompareTabs } from "@/components/benchmarks/compare";
import {
  ASSUMPTIONS,
  HOURLY,
  JOB_COSTS,
  JOB_WALL,
  JOBS,
  LOCAL_MACHINE,
  PAIRS,
  SETS,
  SOURCES,
  SPLAT_DIY,
  VOLUME,
  type Basis,
} from "@/lib/benchmarks";

export const metadata: Metadata = {
  title: "Benchmarks",
  description:
    "Renda against doing it yourself: the same render jobs on your own machine, an on-demand cloud GPU VM and render farms. Measured runs and published prices, with every assumption stated.",
};

const TOC: TocItem[] = [
  { id: "summary", label: "Summary" },
  { id: "cost", label: "Cost per job" },
  { id: "volume", label: "Monthly cost" },
  { id: "time", label: "Time per job" },
  { id: "splat", label: "Mesh → splat" },
  { id: "options", label: "Options compared" },
  { id: "quality", label: "Side by side" },
  { id: "sources", label: "Sources" },
];

const S: Record<string, SeriesSpec> = {
  renda: { id: "renda", label: "Renda (measured)", color: "var(--viz-s1)", shape: "circle" },
  vm: { id: "vm", label: "AWS L4 VM per job (computed)", color: "var(--viz-s2)", shape: "square" },
  farm: { id: "farm", label: "Render farm, 4090 node (computed)", color: "var(--viz-s3)", shape: "diamond" },
  local: { id: "local", label: `This Mac (measured)`, color: "var(--viz-s4)", shape: "triangle" },
  always: { id: "always", label: "AWS L4 VM always on (computed)", color: "var(--viz-s4)", shape: "triangle", dashed: true },
};

const usd = (v: number) => (v < 0.01 ? `$${v.toFixed(4)}` : v < 1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`);
const secs = (v: number) => (v >= 60 ? `${(v / 60).toFixed(1)} min` : `${v.toFixed(1)} s`);
const times = (a: number, b: number) => `${(b / a).toFixed(1)}×`;

function BasisTag({ basis }: { basis: Basis }) {
  return <span className="border-border text-muted-foreground rounded border px-1 py-px font-mono text-[10px]">{basis}</span>;
}

function Section({ id, title, intro, children }: { id: string; title: string; intro?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section id={id} className="border-border scroll-mt-20 border-t pt-12 pb-4">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      {intro && <div className="text-muted-foreground mt-2 max-w-2xl text-sm text-pretty">{intro}</div>}
      <div className="mt-6">{children}</div>
    </section>
  );
}

function Cite({ k }: { k: keyof typeof SOURCES }) {
  return (
    <a href={SOURCES[k].url} className="underline underline-offset-2" rel="noreferrer">
      source
    </a>
  );
}

const OPTIONS: { name: string; price: string; billing: string; setup: string; idle: string; agents: string; wins: string }[] = [
  {
    name: "Renda",
    price: `$${HOURLY.renda.toFixed(2)} per L4-hour ($0.035/min); CPU $0.90/h`,
    billing: "Per second, no minimum; cached re-renders free",
    setup: "One command (npx / uvx); 30 free minutes",
    idle: "None",
    agents: "CLI, MCP, REST, skills",
    wins: "Bursty or unpredictable volume; agents; many formats/engines without installing them",
  },
  {
    name: "Your own machine",
    price: "$0 marginal (you already own it)",
    billing: "—",
    setup: "Install Blender / Mitsuba, drivers, importers; write scripts",
    idle: "None",
    agents: "Whatever you build",
    wins: "Small stills while you iterate: no upload, no cold start",
  },
  {
    name: "AWS / GCP L4 VM",
    price: `$${HOURLY.aws.toFixed(2)}/h (AWS g6.xlarge) · $${HOURLY.gcp.toFixed(2)}/h (GCP g2-standard-4)`,
    billing: "Per second, 60 s minimum",
    setup: "GPU quota, image with drivers + Blender, job runner, storage",
    idle: "Billed while running, busy or not",
    agents: "Whatever you build",
    wins: `Steady load above ~${Math.round(VOLUME.crossover / 1000)}k stills a month on one always-busy VM`,
  },
  {
    name: "Render farms (RebusFarm, Ranch, GarageFarm)",
    price: `GPU ≈ $${HOURLY.farm.toFixed(2)} per RTX 4090-hour (RebusFarm, 0.53 ¢/OB-h)`,
    billing: "Per OctaneBench-hour or GHz-hour; prepaid credits",
    setup: "Account + top-up, desktop plugin, upload the project",
    idle: "None",
    agents: "Plugin / web UI",
    wins: "Long animations on many GPUs at once; managed support",
  },
  {
    name: "SheepIt",
    price: "Free",
    billing: "Points earned by rendering for others",
    setup: "Account + client; Blender only",
    idle: "Your machine renders others' frames",
    agents: "No",
    wins: "Hobby projects where waiting in a queue is fine",
  },
];

export default function BenchmarksPage() {
  const still = JOB_COSTS[0];
  const stillVm = still.values.vm!;
  const stillFarm = still.values.farm!;
  const stillRenda = still.values.renda!;
  const sp = SPLAT_DIY;

  return (
    <>
      <SiteHeader />
      <style>{VIZ_STYLE}</style>
      <main className="viz mx-auto w-full max-w-5xl flex-1 px-4 xl:grid xl:max-w-6xl xl:grid-cols-[9rem_minmax(0,1fr)] xl:gap-12">
        <PageToc items={TOC} />
        <div className="min-w-0">
          <header id="summary" className="scroll-mt-20 pt-20 pb-10 sm:pt-28">
            <h1 className="text-4xl font-semibold tracking-tight">Benchmarks</h1>
            <p className="text-muted-foreground mt-4 max-w-2xl text-pretty">
              The same jobs on Renda, on your own machine, on an on-demand cloud GPU and on render farms. Renda and local numbers are{" "}
              <BasisTag basis="measured" /> runs; VM and farm numbers are <BasisTag basis="computed" /> from published prices, with every
              assumption written next to them. Where doing it yourself wins, we say so.
            </p>
            <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatTile label="Cycles still vs an AWS L4 VM started for it" value={`${times(stillRenda, stillVm)} cheaper`} note={`${usd(stillRenda)} vs ${usd(stillVm)} (VM computed)`} />
              <StatTile label="Mesh → splat vs a DIY pipeline" value={`≥${times(sp.renda.costUsd, sp.diy.costUsd)} cheaper`} note={`${usd(sp.renda.costUsd)} in ${sp.renda.wallMinutes} min vs ≥${usd(sp.diy.costUsd)} in ≥${Math.round(sp.diy.wallMinutes)} min`} />
              <StatTile label="Setup" value="One command" note="vs installing Blender, drivers, importers and a job runner" />
              <StatTile label="Where DIY wins" value={`>${Math.round(VOLUME.crossover / 1000)}k stills/mo`} note="steady load on one always-on VM you run yourself; small stills on a laptop you own" />
            </div>
          </header>

          <Section
            id="cost"
            title="Cost per job"
            intro={
              <>
                Log scale; lower is better. Renda is what each job was billed. The VM is an AWS g6.xlarge (one L4, $
                {ASSUMPTIONS.awsL4PerHour}/h) launched for the job: ~{ASSUMPTIONS.vmBootSeconds} s to boot a pre-baked image (estimate) plus the same
                render time, with the 60 s minimum. The farm is one RTX 4090 at RebusFarm&apos;s GPU rate, assuming it renders{" "}
                {ASSUMPTIONS.farmSpeedupVsL4}× faster than an L4 (our assumption). Your own machine is $0 per job, so it isn&apos;t on the log axis.
              </>
            }
          >
            <Legend series={[S.renda, S.vm, S.farm]} />
            <div className="mt-4">
              <MultiDot rows={JOB_COSTS} series={[S.renda, S.vm, S.farm]} fmt="usd" ariaLabel="Cost per job on Renda, a per-job AWS L4 VM and a render farm, log scale" />
            </div>
            <DataTable
              head={["Job", "Renda (measured)", "AWS L4 VM per job (computed)", "Farm, 4090 (computed)", "This Mac"]}
              rows={JOB_COSTS.map((r) => [r.label, usd(r.values.renda!), usd(r.values.vm!), usd(r.values.farm!), "$0 marginal"])}
            />
            <p className="text-muted-foreground mt-3 text-[11px]">
              The farm row is close to Renda for small jobs ({usd(stillFarm)} vs {usd(stillRenda)} for the Cycles still) — and it depends heavily on the
              3× speed assumption. Farms add an upload step and prepaid credits; Renda adds nothing to install.
            </p>
          </Section>

          <Section
            id="volume"
            title="Monthly cost by volume"
            intro={
              <>
                N Cycles stills a month, log–log. Per-job options scale with volume; an always-on VM is flat at{" "}
                {usd(VOLUME.alwaysOn)}/month ({ASSUMPTIONS.hoursPerMonth} h) whether it&apos;s busy or not. If you can keep one busy, it becomes
                cheaper than Renda above about {VOLUME.crossover.toLocaleString("en-US")} stills a month — and you run the queue, retries, storage
                and upgrades. Past one VM&apos;s capacity you need more of them.
              </>
            }
          >
            <Legend series={[S.renda, S.vm, S.farm, S.always]} />
            <div className="mt-4">
              <LineChart
                series={[S.renda, S.vm, S.farm, S.always]}
                data={VOLUME.series}
                marker={{ x: VOLUME.crossover, label: "always-on VM cheaper from here →" }}
                ariaLabel="Monthly cost against stills per month for Renda, per-job VM, render farm and an always-on VM, log-log"
              />
            </div>
            <DataTable
              head={["Stills / month", ...VOLUME.series.map((s) => s.label)]}
              rows={VOLUME.series[0].points.map((p, i) => [p.x.toLocaleString("en-US"), ...VOLUME.series.map((s) => usd(s.points[i].y))])}
            />
          </Section>

          <Section
            id="time"
            title="Time per job"
            intro={
              <>
                Submit to finished output. Renda is a cold container on the isolated benchmark app (most of a small job&apos;s wall time is container
                start, which isn&apos;t billed). This Mac is {LOCAL_MACHINE}, with Blender already warm. The per-job VM is the boot estimate plus the
                same render time.
              </>
            }
          >
            <Legend series={[S.renda, S.local, S.vm]} />
            <div className="mt-4">
              <MultiDot rows={JOB_WALL} series={[S.renda, S.local, S.vm]} fmt="sec" ariaLabel="Wall time per job on Renda, this Mac and a per-job VM, log scale" />
            </div>
            <DataTable
              head={["Job", "Renda wall (measured)", "This Mac (measured)", "VM per job (computed)", "Renda billed"]}
              rows={JOBS.map((j, i) => [j.label, secs(j.renda.wallSeconds), secs(j.localSeconds), secs(JOB_WALL[i].values.vm!), secs(j.renda.billedSeconds)])}
            />
            <p className="text-muted-foreground mt-3 text-[11px]">
              Honest read: a warm laptop beats any cloud for a small still. Renda pulls ahead on long jobs (the turntable), on many jobs in
              parallel, and when the machine you&apos;re on can&apos;t render at all — a server, a CI runner or an agent sandbox.
            </p>
          </Section>

          <Section
            id="splat"
            title="Mesh → Gaussian splat"
            intro={
              <>
                Renda&apos;s standard bake of the table scene, <BasisTag basis="measured" />: {usd(sp.renda.costUsd)}, {sp.renda.wallMinutes} min,
                hold-out PSNR {sp.renda.psnr} dB. The usual DIY route renders views, recovers the camera poses with COLMAP, then trains — below on
                an AWS L4 VM. Renda skips structure-from-motion entirely because it knows the exact camera poses.
              </>
            }
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <StatTile label="Renda, standard bake (measured)" value={`${usd(sp.renda.costUsd)} · ${sp.renda.wallMinutes} min`} note={`${sp.renda.billedSeconds} s billed · PSNR ${sp.renda.psnr} dB`} />
              <StatTile label="DIY on an AWS L4 (computed, lower bound)" value={`≥${usd(sp.diy.costUsd)} · ≥${Math.round(sp.diy.wallMinutes)} min`} note="published timings, so not the same scene" />
            </div>
            <div className="mt-6 overflow-x-auto">
              <table className="w-full border-collapse text-[12px]">
                <thead>
                  <tr className="text-muted-foreground border-border border-b text-left">
                    <th className="py-1.5 pr-4 font-normal">DIY step</th>
                    <th className="py-1.5 pr-4 font-normal">Time</th>
                    <th className="py-1.5 pr-4 font-normal">Basis</th>
                    <th className="py-1.5 pr-4 font-normal">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {sp.steps.map((s) => (
                    <tr key={s.label} className="border-border/60 border-b">
                      <td className="py-1.5 pr-4">{s.label}</td>
                      <td className="py-1.5 pr-4 font-mono tabular-nums">{secs(s.seconds)}</td>
                      <td className="py-1.5 pr-4"><BasisTag basis={s.basis} /></td>
                      <td className="text-muted-foreground py-1.5 pr-4">{s.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-muted-foreground mt-3 text-[11px]">
              Training time is the gsplat paper&apos;s A100 average (<Cite k="gsplat" />); COLMAP is from the CuSfM paper (<Cite k="colmap" />). Both come
              from other scenes, and an L4 trains slower than an A100, so the DIY figure is a floor, not an estimate of your job.
            </p>
          </Section>

          <Section id="options" title="Options compared" intro="Prices checked 2026-10-20. Farm prices come from published rate cards as reported in 2026; check the vendor before budgeting.">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-[12px]">
                <thead>
                  <tr className="text-muted-foreground border-border border-b text-left align-bottom">
                    {["", "Price", "Billing", "Setup", "Idle cost", "Agent access", "Best for"].map((h) => (
                      <th key={h} className="py-1.5 pr-4 font-normal">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {OPTIONS.map((o) => (
                    <tr key={o.name} className="border-border/60 border-b align-top">
                      <td className="py-2 pr-4 font-medium">{o.name}</td>
                      <td className="py-2 pr-4">{o.price}</td>
                      <td className="text-muted-foreground py-2 pr-4">{o.billing}</td>
                      <td className="text-muted-foreground py-2 pr-4">{o.setup}</td>
                      <td className="text-muted-foreground py-2 pr-4">{o.idle}</td>
                      <td className="text-muted-foreground py-2 pr-4">{o.agents}</td>
                      <td className="py-2 pr-4">{o.wins}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section id="quality" title="Side by side" intro="What the knobs do, on production renders made for this page. Drag the sliders (or focus the handle and use the arrow keys).">
            <div className="grid gap-x-8 gap-y-12 sm:grid-cols-2">
              {PAIRS.map((p) => (
                <CompareSlider key={p.id} before={p.before} after={p.after} width={p.width} height={p.height} title={p.title} />
              ))}
              {SETS.map((s) => (
                <CompareTabs key={s.id} items={s.items} width={s.width} height={s.height} title={s.title} note={s.note} />
              ))}
            </div>
            <p className="text-muted-foreground mt-6 text-xs">
              Want to orbit the splats yourself? The <Link href="/#splats" className="underline underline-offset-2">homepage demo</Link> puts the
              source mesh, the fast preview and the standard bake in one viewer.
            </p>
          </Section>

          <Section id="sources" title="Sources and method">
            <ul className="text-muted-foreground space-y-1.5 text-xs">
              {Object.entries(SOURCES).map(([k, s]) => (
                <li key={k}>
                  <a href={s.url} className="hover:text-foreground underline underline-offset-2" rel="noreferrer">
                    {s.label}
                  </a>
                </li>
              ))}
            </ul>
            <ul className="text-muted-foreground mt-6 list-disc space-y-1.5 pl-4 text-xs">
              <li>Renda: same packages, isolated benchmark copy of the production worker, 2026-10-20, list prices per hardware class.</li>
              <li>Local: {LOCAL_MACHINE}; best of one run each, Blender warm; outputs discarded.</li>
              <li>
                VM: {ASSUMPTIONS.vmBootSeconds} s launch-to-ready is our estimate for an image with drivers and Blender pre-installed; a fresh
                instance that installs them is slower. GCP&apos;s L4 is ~12% cheaper than AWS&apos;s; spot instances are cheaper still but can be
                interrupted.
              </li>
              <li>
                Farms: RebusFarm&apos;s GPU rate × an RTX 4090&apos;s ~{ASSUMPTIONS.farmNodeOb.toLocaleString("en-US")} OctaneBench points, assuming a 4090 renders{" "}
                {ASSUMPTIONS.farmSpeedupVsL4}× faster than an L4 (we found no published L4 Blender benchmark). Ranch and GarageFarm publish similar
                OB-hour rates with priority tiers.
              </li>
            </ul>
          </Section>
        </div>
      </main>
      <footer className="text-muted-foreground mx-auto flex w-full max-w-5xl flex-wrap gap-x-5 gap-y-2 px-4 py-8 text-xs">
        <span>Apache-2.0 · AGPL-3.0 (cloud)</span>
        <Link href="/docs" className="hover:text-foreground">Docs</Link>
        <Link href="/benchmarks" className="hover:text-foreground">Benchmarks</Link>
        <Link href="/llms.txt" className="hover:text-foreground">llms.txt</Link>
        <Link href="/auth.md" className="hover:text-foreground">auth.md</Link>
        <Link href="/api/v1/openapi.json" className="hover:text-foreground">OpenAPI</Link>
      </footer>
    </>
  );
}
