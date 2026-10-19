"use client";

import Script from "next/script";
import { useEffect, useRef, useState, type HTMLAttributes } from "react";
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

type ModelViewerEl = HTMLElement & {
  loaded?: boolean;
  cameraOrbit: string;
  cameraTarget: string;
  fieldOfView: string;
  turntableRotation: number;
  getCameraOrbit(): { theta: number; phi: number; radius: number };
  getCameraTarget(): { x: number; y: number; z: number };
  getFieldOfView(): number;
  resetTurntableRotation(theta?: number): void;
  jumpCameraToGoal(): void;
};

/** Copy the viewpoint (orbit, target, zoom) from one viewer to another. No auto-rotate: comparisons hold still. */
function syncCamera(from: ModelViewerEl, to: ModelViewerEl) {
  try {
    const o = from.getCameraOrbit();
    const t = from.getCameraTarget();
    to.cameraOrbit = `${o.theta}rad ${o.phi}rad ${o.radius}m`;
    to.cameraTarget = `${t.x}m ${t.y}m ${t.z}m`;
    to.fieldOfView = `${from.getFieldOfView()}deg`;
    to.resetTurntableRotation(from.turntableRotation);
    to.jumpCameraToGoal();
  } catch {
    // a viewer that hasn't initialised yet keeps its own default framing
  }
}

function DemoCard({ demo }: { demo: Demo }) {
  const [active, setActive] = useState(demo.defaultVariant);
  const [inView, setInView] = useState(false);
  const [loaded, setLoaded] = useState<Record<string, boolean>>({});
  const box = useRef<HTMLDivElement>(null);
  const viewers = useRef<Record<string, ModelViewerEl | null>>({});

  // Load every variant once the card is near the viewport, so toggling is instant.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => e.isIntersecting && (setInView(true), io.disconnect()), { rootMargin: "300px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!inView) return;
    const cleanups = demo.variants.map((x) => {
      const el = viewers.current[x.id];
      if (!el) return () => {};
      const onLoad = () => setLoaded((l) => ({ ...l, [x.id]: true }));
      if (el.loaded) onLoad();
      el.addEventListener("load", onLoad);
      return () => el.removeEventListener("load", onLoad);
    });
    return () => cleanups.forEach((c) => c());
  }, [inView, demo.variants]);

  const select = (id: string) => {
    if (id === active) return;
    const from = viewers.current[active];
    const to = viewers.current[id];
    if (from && to) syncCamera(from, to);
    setActive(id);
  };

  const v = demo.variants.find((x) => x.id === active) ?? demo.variants[0];
  const b = v.bake;
  const stats = b
    ? [HW[b.hardware], `${b.textureSize}² ${b.mode}`, `${b.billedSeconds.toFixed(1)}s billed`, `$${b.costUsd.toFixed(4)}`, `GLB ${mb(v.bytes)}`]
    : ["original PBR materials", "lit by the viewer", `GLB ${mb(v.bytes)}`];
  return (
    <figure>
      <div ref={box} className="bg-muted relative overflow-hidden rounded-xl" style={{ aspectRatio: "4 / 3" }}>
        {inView &&
          demo.variants.map((x) => (
            <model-viewer
              key={x.id}
              ref={(el: ModelViewerEl | null) => {
                viewers.current[x.id] = el;
              }}
              src={x.src}
              alt={`${demo.credit?.title ?? demo.id} — ${x.label}`}
              camera-controls=""
              interaction-prompt="none"
              environment-image="neutral"
              shadow-intensity="0"
              exposure="1"
              loading="eager"
              aria-hidden={x.id !== active}
              className={cn("absolute inset-0 transition-opacity duration-150", x.id === active ? "opacity-100" : "pointer-events-none opacity-0")}
              style={{ width: "100%", height: "100%", background: "transparent", ["--poster-color" as string]: "transparent" }}
            />
          ))}
        <div className="bg-background/80 absolute top-3 left-3 flex gap-0.5 rounded-md p-0.5 backdrop-blur">
          {demo.variants.map((x) => (
            <button
              key={x.id}
              type="button"
              onClick={() => select(x.id)}
              aria-pressed={x.id === active}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 font-mono text-[11px] transition-colors",
                x.id === active ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {x.label}
              {inView && !loaded[x.id] && <span className="size-1 animate-pulse rounded-full bg-current opacity-60" aria-label="loading" />}
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
