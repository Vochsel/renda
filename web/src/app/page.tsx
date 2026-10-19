import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { PageToc, type TocItem } from "@/components/page-toc";
import { CopyButton } from "@/components/copy-button";
import { InstallTabs } from "@/components/install-tabs";
import { installSnippets } from "@/lib/install-snippets";
import { FREE_MINUTES, HARDWARE_LABEL, PRICE_PER_MINUTE } from "@/lib/pricing";
import { SHOWCASE, type ShowcaseItem } from "@/lib/showcase";
import { BakeDemo } from "@/components/bake-demo";
import { SplatDemo } from "@/components/splat-demo";

const FORMATS = [".blend", ".usd(z)", ".glb", ".fbx", ".obj", ".stl", ".ply", ".dae", ".3mf", ".abc", "three.js", "splats (.ply .splat .spz)"];
const ENGINES = ["Cycles", "Eevee", "Workbench", "Mitsuba 3", "three.js", "gsplat", "HyperFrames"];

// Under the hero, in a 2-column grid with one shared aspect ratio.
const HERO = "porsche-cycles";
const GRID = [
  "courtyard-mixed-cycles", "lizard-gsplat",
  "camera-mitsuba", "hall-turntable",
  "samurai-eevee", "raccoon-gsplat",
  "craftsman-threejs", "venus-turntable",
];

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
        <p className="text-muted-foreground mt-0.5 truncate text-[11px]">
          <a href={item.credit.url} className="hover:text-foreground underline-offset-2 hover:underline">
            {item.credit.title}
          </a>{" "}
          · {item.credit.author} · {item.credit.license}
        </p>
      </figcaption>
    </figure>
  );
}

const TOC: TocItem[] = [
  { id: "start", label: "Get started" },
  { id: "renders", label: "Example renders" },
  { id: "bake", label: "Light baking → GLB" },
  { id: "splats", label: "Mesh → splat" },
  { id: "formats", label: "Formats & pricing" },
];

export default function Home() {
  const byId = Object.fromEntries(SHOWCASE.map((s) => [s.id, s]));
  const hero = byId[HERO];
  const grid = GRID.map((id) => byId[id]);
  const shown = [hero, ...grid];
  const total = shown.reduce((n, s) => n + s.costUsd, 0);
  const cheapest = Math.min(...Object.values(PRICE_PER_MINUTE));

  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 xl:grid xl:max-w-6xl xl:grid-cols-[9rem_minmax(0,1fr)] xl:gap-12">
        <PageToc items={TOC} />
        <div className="min-w-0">
        <section id="start" className="scroll-mt-20 pt-20 pb-14 sm:pt-28">
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

        <section id="renders" aria-label="Example renders" className="scroll-mt-20 pb-6">
          <Render item={hero} priority />
          <div className="mt-12 grid gap-x-8 gap-y-12 sm:grid-cols-2">
            {grid.map((item) => (
              <Render key={item.id} item={item} aspect="4 / 3" />
            ))}
          </div>
          <p className="text-muted-foreground mt-10 text-xs">
            Real jobs on the production pipeline, unretouched. All {shown.length} cost ${total.toFixed(2)} together. Models from
            Sketchfab, splats from SuperSplat and Niantic, under CC0 / CC BY 4.0 / MIT — see{" "}
            <a href="https://github.com/vochsel/renda/blob/main/testdata/showcase-public/CREDITS.md" className="underline underline-offset-2">
              credits
            </a>
            .
          </p>
        </section>

        <section id="bake" aria-label="Light baking" className="scroll-mt-20 border-border mt-16 border-t pt-14 pb-6">
          <h2 className="text-2xl font-semibold tracking-tight">Bake lighting into any model → GLB</h2>
          <p className="text-muted-foreground mt-3 mb-10 max-w-xl text-pretty">
            <span className="text-foreground font-mono text-sm">renda bake model.blend</span> — Cycles, Mitsuba or three.js bake the
            HDRI and lights into the textures, so the GLB looks lit in any viewer or engine, with no lights. Drag to orbit.
          </p>
          <BakeDemo />
        </section>

        <section id="splats" aria-label="Mesh to Gaussian splat" className="scroll-mt-20 border-border mt-16 border-t pt-14 pb-6">
          <h2 className="text-2xl font-semibold tracking-tight">Turn any mesh into a Gaussian splat</h2>
          <p className="text-muted-foreground mt-3 mb-10 max-w-xl text-pretty">
            <span className="text-foreground font-mono text-sm">renda splat model.glb</span> — cameras are fitted around the
            model, views render in parallel, and gsplat trains from an exact COLMAP dataset, so lighting and reflections
            are baked into the splat. Switch between the source mesh and the splat; drag to orbit.
          </p>
          <SplatDemo />
        </section>

        <section id="formats" className="border-border mt-16 grid scroll-mt-20 gap-10 border-t py-14 text-sm sm:grid-cols-3">
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
        </div>
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
