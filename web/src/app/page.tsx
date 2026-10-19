import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { CopyButton } from "@/components/copy-button";
import { InstallTabs } from "@/components/install-tabs";
import { installSnippets } from "@/lib/install-snippets";
import { FREE_MINUTES, HARDWARE_LABEL, PRICE_PER_MINUTE } from "@/lib/pricing";
import { SHOWCASE, type ShowcaseItem } from "@/lib/showcase";

const FORMATS = [".blend", ".usd(z)", ".glb", ".fbx", ".obj", ".stl", ".ply", ".dae", ".3mf", ".abc", "three.js", "splats (.ply .splat .spz)"];
const ENGINES = ["Cycles", "Eevee", "Workbench", "Mitsuba 3", "three.js", "gsplat", "HyperFrames"];

// Under the hero, in a 2-column grid with one shared aspect ratio.
const GRID = ["splat-mixed-cycles", "mitsuba-blend", "splat-gsplat", "threejs-eevee", "usdz-turntable", "splat-turntable"];

const GPU_SHORT: Record<ShowcaseItem["hardware"], string> = { cpu: "CPU", "gpu-l4": "L4 GPU", "gpu-l40s": "L40S GPU" };

function seconds(s: number) {
  return s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${s.toFixed(1)}s`;
}

function Stats({ item }: { item: ShowcaseItem }) {
  const parts = [
    GPU_SHORT[item.hardware],
    `${item.resolution[0]}×${item.resolution[1]}`,
    item.frames > 1 ? `${item.frames} frames` : null,
    `${seconds(item.billedSeconds)} billed`,
    `$${item.costUsd < 0.01 ? item.costUsd.toFixed(4) : item.costUsd.toFixed(2)}`,
  ].filter(Boolean);
  return <p className="text-muted-foreground mt-1 font-mono text-[11px] tracking-tight">{parts.join("  ·  ")}</p>;
}

function Render({ item, priority, aspect, className }: { item: ShowcaseItem; priority?: boolean; aspect?: string; className?: string }) {
  aspect ??= `${item.resolution[0]} / ${item.resolution[1]}`;
  return (
    <figure className={className}>
      <div className="bg-muted overflow-hidden rounded-xl" style={{ aspectRatio: aspect }}>
        {item.video ? (
          <video src={item.video} poster={item.poster} autoPlay loop muted playsInline className="h-full w-full object-cover" />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- already-optimised webp straight from the renderer
          <img src={item.image} alt={item.title} loading={priority ? "eager" : "lazy"} className="h-full w-full object-cover" />
        )}
      </div>
      <figcaption className="mt-3">
        <p className="text-sm">
          <span className="font-mono text-xs">{item.input}</span>
          <span className="text-muted-foreground"> → </span>
          {item.engine}
        </p>
        <Stats item={item} />
      </figcaption>
    </figure>
  );
}

export default function Home() {
  const byId = Object.fromEntries(SHOWCASE.map((s) => [s.id, s]));
  const hero = byId["hero-cycles"];
  const grid = GRID.map((id) => byId[id]);
  const shown = [hero, ...grid];
  const total = shown.reduce((n, s) => n + s.costUsd, 0);
  const cheapest = Math.min(...Object.values(PRICE_PER_MINUTE));

  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4">
        <section className="pt-20 pb-14 sm:pt-28">
          <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
            Render any 3D file from your agent.
          </h1>
          <p className="text-muted-foreground mt-5 max-w-xl text-lg text-pretty">
            Meshes and Gaussian splats through Blender, Mitsuba, three.js and gsplat, behind one CLI and MCP server. Cloud GPUs, billed per second. Open source.
          </p>

          <div className="mt-10 max-w-2xl space-y-3">
            <div className="border-border flex items-center gap-3 rounded-lg border px-4 py-3">
              <span className="text-muted-foreground font-mono text-xs">agent</span>
              <code className="min-w-0 flex-1 truncate font-mono text-[13px]">{installSnippets.agentPrompt}</code>
              <CopyButton text={installSnippets.agentPrompt} />
            </div>
            <InstallTabs
              tabs={[
                { label: "npx", lines: installSnippets.npx },
                { label: "uvx", lines: installSnippets.uvx },
                { label: "mcp", lines: [installSnippets.mcp.claudeCode] },
              ]}
            />
            <p className="text-muted-foreground text-xs">
              Free trial, no signup. Claim it to keep the workspace and get {FREE_MINUTES} free minutes.
            </p>
          </div>
        </section>

        <section aria-label="Example renders" className="pb-6">
          <Render item={hero} priority />
          <div className="mt-12 grid gap-x-8 gap-y-12 sm:grid-cols-2">
            {grid.map((item) => (
              <Render key={item.id} item={item} aspect="4 / 3" />
            ))}
          </div>
          <p className="text-muted-foreground mt-10 text-xs">
            Real jobs on the production pipeline, unretouched. All {shown.length} cost ${total.toFixed(2)} together. Packages in{" "}
            <a href="https://github.com/vochsel/renda/tree/main/testdata" className="underline underline-offset-2">
              testdata
            </a>
            .
          </p>
        </section>

        <section className="border-border mt-16 grid gap-10 border-t py-14 text-sm sm:grid-cols-3">
          <div>
            <h2 className="font-medium">Any format</h2>
            <p className="text-muted-foreground mt-2 font-mono text-xs leading-relaxed">{FORMATS.join("  ")}</p>
          </div>
          <div>
            <h2 className="font-medium">Any engine</h2>
            <p className="text-muted-foreground mt-2 font-mono text-xs leading-relaxed">{ENGINES.join("  ")}</p>
          </div>
          <div>
            <h2 className="font-medium">From ${cheapest.toFixed(3)} / minute</h2>
            <p className="text-muted-foreground mt-2 font-mono text-xs leading-relaxed">
              {Object.entries(PRICE_PER_MINUTE)
                .map(([h, p]) => `${HARDWARE_LABEL[h as keyof typeof HARDWARE_LABEL]} $${p.toFixed(3)}`)
                .join("  ")}
            </p>
            <p className="text-muted-foreground mt-2 text-xs">
              Per-second billing. Cached re-renders free. <Link href="/pricing" className="underline underline-offset-2">Pricing</Link>
            </p>
          </div>
        </section>
      </main>
      <footer className="text-muted-foreground mx-auto flex w-full max-w-5xl flex-wrap gap-x-5 gap-y-2 px-4 py-8 text-xs">
        <span>Apache-2.0 · AGPL-3.0 (cloud)</span>
        <Link href="/docs" className="hover:text-foreground">Docs</Link>
        <Link href="/llms.txt" className="hover:text-foreground">llms.txt</Link>
        <Link href="/auth.md" className="hover:text-foreground">auth.md</Link>
        <Link href="/api/v1/openapi.json" className="hover:text-foreground">OpenAPI</Link>
      </footer>
    </>
  );
}
