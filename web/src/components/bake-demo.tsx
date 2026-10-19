"use client";

import Script from "next/script";
import { useState, type HTMLAttributes } from "react";
import { BAKE_DEMOS, type BakeDemo as Demo } from "@/lib/bake-demos";
import { cn } from "@/lib/utils";

// <model-viewer> is a web component loaded lazily from a pinned CDN build.
declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": HTMLAttributes<HTMLElement> & Record<string, unknown>;
    }
  }
}

const MODEL_VIEWER = "https://unpkg.com/@google/model-viewer@4.0.0/dist/model-viewer.min.js";
const HW: Record<string, string> = { cpu: "CPU", "gpu-l4": "L4 GPU" };

function mb(bytes: number) {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} KB`;
}

function DemoCard({ demo }: { demo: Demo }) {
  const [active, setActive] = useState(demo.defaultVariant);
  const v = demo.variants.find((x) => x.id === active) ?? demo.variants[0];
  const b = v.bake;
  const stats = b
    ? [HW[b.hardware], `${b.textureSize}² ${b.mode}`, `${b.billedSeconds.toFixed(1)}s billed`, `$${b.costUsd.toFixed(4)}`, `GLB ${mb(v.bytes)}`]
    : ["original PBR materials", "lit by the viewer", `GLB ${mb(v.bytes)}`];
  return (
    <figure>
      <div className="bg-muted relative overflow-hidden rounded-xl" style={{ aspectRatio: "4 / 3" }}>
        <model-viewer
          key={v.src}
          src={v.src}
          alt={`${demo.credit?.title ?? demo.id} — ${v.label}`}
          camera-controls=""
          auto-rotate=""
          rotation-per-second="18deg"
          interaction-prompt="none"
          environment-image="neutral"
          shadow-intensity="0"
          exposure="1"
          loading="lazy"
          style={{ width: "100%", height: "100%", background: "transparent", ["--poster-color" as string]: "transparent" }}
        />
        <div className="bg-background/80 absolute top-3 left-3 flex gap-0.5 rounded-md p-0.5 backdrop-blur">
          {demo.variants.map((x) => (
            <button
              key={x.id}
              type="button"
              onClick={() => setActive(x.id)}
              className={cn(
                "rounded px-2 py-0.5 font-mono text-[11px] transition-colors",
                x.id === active ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {x.label}
            </button>
          ))}
        </div>
      </div>
      <figcaption className="mt-3">
        <p className="text-sm">
          <span className="font-mono text-xs">{demo.input}</span>
          <span className="text-muted-foreground"> → </span>
          {b ? `${b.engine} bake → .glb` : "plain .glb export"}
        </p>
        <p className="text-muted-foreground mt-1 font-mono text-[11px] tracking-tight">{stats.join("  ·  ")}</p>
        {demo.credit && (
          <p className="text-muted-foreground mt-0.5 truncate text-[11px]">
            <a href={demo.credit.url} className="hover:text-foreground underline-offset-2 hover:underline">
              {demo.credit.title}
            </a>{" "}
            by {demo.credit.author} · {demo.credit.license}
          </p>
        )}
      </figcaption>
    </figure>
  );
}

export function BakeDemo() {
  return (
    <>
      <Script src={MODEL_VIEWER} type="module" strategy="lazyOnload" />
      <div className="grid gap-x-8 gap-y-12 sm:grid-cols-2">
        {BAKE_DEMOS.map((d) => (
          <DemoCard key={d.id} demo={d} />
        ))}
      </div>
    </>
  );
}
