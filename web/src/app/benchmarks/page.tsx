import type { Metadata } from "next";
import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { PageToc, type TocItem } from "@/components/page-toc";
import { Columns, DataTable, DotSeries, Dumbbell, Legend, RangeChart, StatTile, StripPlot, VIZ_STYLE } from "@/components/benchmarks/charts";
import { CompareSlider, CompareTabs } from "@/components/benchmarks/compare";
import {
  BILLING_FIXES,
  COLD_START,
  ESTIMATES,
  OPTIMISATION,
  PAIRS,
  SETS,
  SPLAT_PROGRESSION,
  SPLAT_VARIANTS,
  type Source,
} from "@/lib/benchmarks";

export const metadata: Metadata = {
  title: "Benchmarks",
  description: "Measured render cost, time and quality on Renda: before/after optimisation, cold starts, splat bakes and side-by-side renders.",
};

const TOC: TocItem[] = [
  { id: "summary", label: "Summary" },
  { id: "compare", label: "Side by side" },
  { id: "optimisation", label: "Render cost" },
  { id: "billing", label: "Billing fixes" },
  { id: "splat", label: "Mesh → splat" },
  { id: "cold-start", label: "Cold start" },
  { id: "estimates", label: "Estimates" },
];

const sec = (v: number) => (v >= 100 ? v.toFixed(0) : v.toFixed(1)).replace(/\.0$/, "");
const usd = (v: number) => (v === 0 ? "$0" : v < 0.1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`);

function SourceTag({ source, date }: { source: Source; date?: string }) {
  return (
    <span className="text-muted-foreground font-mono text-[11px]">
      {source === "production" ? "production jobs" : "isolated benchmark app"}
      {date ? ` · ${date}` : ""}
    </span>
  );
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

export default function BenchmarksPage() {
  const opt = OPTIMISATION;
  const sum = (a: { before: number; after: number }[], k: "before" | "after") => a.reduce((n, r) => n + r[k], 0);
  const optBefore = sum(opt.billedSeconds, "before");
  const optAfter = sum(opt.billedSeconds, "after");
  const first = SPLAT_PROGRESSION[0];
  const now = SPLAT_PROGRESSION[1];
  const blenderMedian = 6.5;

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
              Measured runs, not projections. Cost is list price for the seconds billed on each hardware class (CPU $0.015, L4 $0.035, L40S
              $0.075 per minute). Each chart says whether it came from production jobs or from an isolated benchmark copy of the same worker.
            </p>
            <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatTile label="Turntable, 24 frames" value="12.5 s billed" delta="−91% from 133 s" note="production" />
              <StatTile label="Benchmark set, billed time" value={`${sec(optAfter)} s`} delta={`−${Math.round((1 - optAfter / optBefore) * 100)}% from ${sec(optBefore)} s`} note="9 jobs, isolated" />
              <StatTile label="Mesh → splat, standard" value={usd(now.costUsd)} delta={`−${Math.round((1 - now.costUsd / first.costUsd) * 100)}% from ${usd(first.costUsd)}`} note={`PSNR ${first.psnr} → ${now.psnr} dB`} />
              <StatTile label="Blender on L4, median cold start" value={`${blenderMedian} s`} note="not billed to you; 0.1 s when warm" />
            </div>
          </header>

          <Section id="compare" title="Side by side" intro="Drag the sliders (or focus the handle and use the arrow keys). Production renders made for this page.">
            <div className="grid gap-x-8 gap-y-12 sm:grid-cols-2">
              {PAIRS.slice(0, 2).map((p) => (
                <CompareSlider key={p.id} before={p.before} after={p.after} width={p.width} height={p.height} title={p.title} />
              ))}
              {SETS.map((s) => (
                <CompareTabs key={s.id} items={s.items} width={s.width} height={s.height} title={s.title} note={s.note} />
              ))}
            </div>
            <p className="text-muted-foreground mt-6 text-xs">
              Want to orbit the splats yourself? The <Link href="/#splats" className="underline underline-offset-2">homepage demo</Link> puts the source mesh, the fast
              preview and the standard bake in one viewer.
            </p>
          </Section>

          <Section
            id="optimisation"
            title="Render cost, before and after the optimisation pass"
            intro={
              <>
                Same nine packages run before and after: frames encoded in the render container, events sent on one connection, pooled storage uploads, and splat
                data uploaded to the GPU once per job. <SourceTag source={opt.source} date={opt.date} />
              </>
            }
          >
            <Legend items={[{ label: "Before", color: "var(--viz-before)" }, { label: "After", color: "var(--viz-after)" }]} />
            <h3 className="mt-5 text-sm">Billed seconds</h3>
            <Dumbbell rows={opt.billedSeconds} fmt="sec" unit="s" ariaLabel="Billed seconds per benchmark, before and after the optimisation pass" />
            <h3 className="mt-8 text-sm">Cost per job</h3>
            <Dumbbell rows={opt.costUsd} fmt="usd3" unit="" ariaLabel="Cost per benchmark job, before and after" />
            <DataTable
              head={["Benchmark", "Billed before (s)", "Billed after (s)", "Cost before", "Cost after", "Note"]}
              rows={opt.billedSeconds.map((r, i) => [r.label, r.before, r.after, `$${opt.costUsd[i].before.toFixed(4)}`, `$${opt.costUsd[i].after.toFixed(4)}`, r.note ?? ""])}
            />
          </Section>

          <Section
            id="billing"
            title="Billing fixes found by measuring real jobs"
            intro={
              <>
                The coordinator was billing the time it spent waiting on the render and encode containers, and frames moved through storage one at a time.
                Both fixed. <SourceTag source={BILLING_FIXES.source} date="2026-10-19 → 20" />
              </>
            }
          >
            <Legend items={[{ label: "Before", color: "var(--viz-before)" }, { label: "After", color: "var(--viz-after)" }]} />
            <div className="mt-5">
              <Dumbbell rows={BILLING_FIXES.rows} fmt="sec" unit="s" ariaLabel="Billed seconds before and after the billing fixes" />
            </div>
            <DataTable head={["Job", "Billed before (s)", "Billed after (s)", "Note"]} rows={BILLING_FIXES.rows.map((r) => [r.label, r.before, r.after, r.note ?? ""])} />
          </Section>

          <Section
            id="splat"
            title="Mesh → Gaussian splat"
            intro="The table scene's standard bake over time: views rendered inside the trainer for small jobs, coverage-driven cameras, positions frozen early, and pruning before export. PSNR is measured on views held out from training."
          >
            <div className="grid gap-8 sm:grid-cols-3">
              <Columns
                title="Cost per bake"
                points={SPLAT_PROGRESSION.map((r) => ({ label: r.label, value: r.costUsd, sub: r.source === "production" ? "production" : "isolated" }))}
                fmt="usd"
                ariaLabel="Cost per standard splat bake over time"
              />
              <Columns
                title="Wall time (minutes)"
                points={SPLAT_PROGRESSION.map((r) => ({ label: r.label, value: r.wallSeconds / 60, sub: r.date.slice(5) }))}
                fmt="min"
                ariaLabel="Wall time per standard splat bake over time"
              />
              <DotSeries
                title="Hold-out PSNR"
                points={SPLAT_PROGRESSION.map((r) => ({ label: r.label, value: r.psnr, sub: r.note ? "*" : undefined }))}
                domain={[30, 40]}
                fmt="db"
                ariaLabel="Hold-out PSNR per standard splat bake over time"
              />
            </div>
            <p className="text-muted-foreground mt-3 text-[11px]">* PSNR is scored on 12 random hold-out views per bake, so runs aren&apos;t strictly comparable.</p>
            <h3 className="mt-8 text-sm">Configurations</h3>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full border-collapse font-mono text-[11px] tabular-nums">
                <thead>
                  <tr className="text-muted-foreground border-border border-b text-left">
                    {["Scene", "Config", "Billed", "Cost", "Wall", "PSNR", ".spz", "Where"].map((h) => (
                      <th key={h} className="py-1.5 pr-4 font-normal">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {SPLAT_VARIANTS.map((v) => (
                    <tr key={v.scene + v.config} className="border-border/60 border-b">
                      <td className="py-1.5 pr-4">{v.scene}</td>
                      <td className="py-1.5 pr-4">{v.config}</td>
                      <td className="py-1.5 pr-4">{sec(v.billedSeconds)} s</td>
                      <td className="py-1.5 pr-4">{usd(v.costUsd)}</td>
                      <td className="py-1.5 pr-4">{v.wall}</td>
                      <td className="py-1.5 pr-4">{v.psnr ? `${v.psnr} dB` : "—"}</td>
                      <td className="py-1.5 pr-4">{v.size}</td>
                      <td className="text-muted-foreground py-1.5 pr-4">{v.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-muted-foreground mt-2 text-[11px]">The Canon camera&apos;s hold-out set includes close-ups, so its PSNR reads lower than the table&apos;s.</p>
          </Section>

          <Section
            id="cold-start"
            title="Cold start"
            intro={
              <>
                Seconds from calling a worker to our code running, per image (log scale). You aren&apos;t billed for it, but it&apos;s most of a small job&apos;s wall time.
                GPU snapshots didn&apos;t move the median and produced the two worst waits, so they stay off. <SourceTag source={COLD_START.source} date={COLD_START.date} />
              </>
            }
          >
            <StripPlot rows={COLD_START.images} ariaLabel="Cold-start samples per worker image with medians, log scale" />
            <DataTable
              head={["Image", "Variant", "Samples (s)", "Warm (s)"]}
              rows={COLD_START.images.map((r) => [r.label, r.variant, r.samples.join(", "), r.warm])}
            />
          </Section>

          <Section
            id="estimates"
            title="Estimates vs what was billed"
            intro={
              <>
                <code className="font-mono text-xs">renda estimate</code> returns a range before anything runs. Below: the stored estimate for the nine production jobs behind
                this page, against their billed seconds. <SourceTag source={ESTIMATES.source} date={ESTIMATES.date} />
              </>
            }
          >
            <Legend
              items={[
                { label: "Estimated range", color: "var(--viz-band)", shape: "band" },
                { label: "Billed, within range", color: "var(--viz-after)" },
                { label: "Billed, outside range", color: "var(--viz-before)" },
              ]}
            />
            <div className="mt-5">
              <RangeChart rows={ESTIMATES.rows} unit="s" ariaLabel="Estimated seconds range versus billed seconds per job" />
            </div>
            <DataTable
              head={["Job", "Estimate low (s)", "Estimate high (s)", "Billed (s)", "Note"]}
              rows={ESTIMATES.rows.map((r) => [r.label, r.lo, r.hi, r.actual, r.note ?? ""])}
            />
            <div className="mt-8 grid gap-3 sm:grid-cols-2">
              <StatTile
                label="Planner check across the benchmark set"
                value={`${ESTIMATES.plannerInRange.after[0]}/${ESTIMATES.plannerInRange.after[1]} in range`}
                note={`was ${ESTIMATES.plannerInRange.before[0]}/${ESTIMATES.plannerInRange.before[1]} before calibration (isolated)`}
              />
              <StatTile
                label="USDZ turntable, standard priority"
                value={`${usd(ESTIMATES.usdz.actual)} billed`}
                note={`estimate ${usd(ESTIMATES.usdz.lo)}–${usd(ESTIMATES.usdz.hi)} (production)`}
              />
            </div>
            <p className="text-muted-foreground mt-3 text-[11px]">
              Misses are mostly fixed startup time — a cold shader cache on a fresh container, or loading a splat file — which the next calibration pass will add.
            </p>
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
