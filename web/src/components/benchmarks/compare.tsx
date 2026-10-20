"use client";

import { useCallback, useRef, useState } from "react";
import { cn } from "@/lib/utils";

type Side = { src: string; label: string; meta: string };

/**
 * Before/after image slider. Drag (mouse or touch) anywhere on the image, or focus the handle and use
 * ←/→ (Shift for bigger steps), Home/End. The "after" image is revealed left of the divider.
 */
export function CompareSlider({ before, after, width, height, title }: { before: Side; after: Side; width: number; height: number; title: string }) {
  const [pos, setPos] = useState(50);
  const box = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const fromEvent = useCallback((clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    setPos(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  }, []);

  return (
    <figure>
      <div
        ref={box}
        className="bg-muted relative cursor-ew-resize touch-pan-y overflow-hidden rounded-xl select-none"
        style={{ aspectRatio: `${width} / ${height}` }}
        onPointerDown={(e) => {
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          fromEvent(e.clientX);
        }}
        onPointerMove={(e) => dragging.current && fromEvent(e.clientX)}
        onPointerUp={() => (dragging.current = false)}
        onPointerCancel={() => (dragging.current = false)}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- small pre-encoded webp */}
        <img src={before.src} alt={`${title} — ${before.label}`} className="absolute inset-0 h-full w-full object-cover" draggable={false} />
        {/* eslint-disable-next-line @next/next/no-img-element -- small pre-encoded webp */}
        <img
          src={after.src}
          alt={`${title} — ${after.label}`}
          className="absolute inset-0 h-full w-full object-cover"
          style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}
          draggable={false}
        />
        <span className="bg-background/80 absolute top-3 left-3 rounded px-2 py-0.5 font-mono text-[11px] backdrop-blur">{after.label}</span>
        <span className="bg-background/80 absolute top-3 right-3 rounded px-2 py-0.5 font-mono text-[11px] backdrop-blur">{before.label}</span>
        <div className="bg-background pointer-events-none absolute inset-y-0 w-0.5 shadow-[0_0_0_1px_rgba(0,0,0,0.15)]" style={{ left: `${pos}%`, transform: "translateX(-50%)" }} />
        <button
          type="button"
          role="slider"
          aria-label={`${title}: drag to compare ${after.label} and ${before.label}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pos)}
          aria-valuetext={`${Math.round(pos)}% ${after.label}`}
          onKeyDown={(e) => {
            const step = e.shiftKey ? 10 : 2;
            if (e.key === "ArrowLeft") setPos((p) => Math.max(0, p - step));
            else if (e.key === "ArrowRight") setPos((p) => Math.min(100, p + step));
            else if (e.key === "Home") setPos(0);
            else if (e.key === "End") setPos(100);
            else return;
            e.preventDefault();
          }}
          className="bg-background text-foreground focus-visible:ring-ring absolute top-1/2 grid size-8 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border text-xs shadow-sm focus-visible:ring-2 focus-visible:outline-none"
          style={{ left: `${pos}%` }}
        >
          <span aria-hidden>⇆</span>
        </button>
      </div>
      <figcaption className="mt-3">
        <p className="text-sm">{title}</p>
        <p className="text-muted-foreground mt-1 font-mono text-[11px] tracking-tight">
          {after.label}: {after.meta} · {before.label}: {before.meta}
        </p>
      </figcaption>
    </figure>
  );
}

/** Multi-way comparison: tabs over stacked images (all loaded up front, so switching is instant). */
export function CompareTabs({ items, width, height, title, note }: { items: Side[]; width: number; height: number; title: string; note?: string }) {
  const [active, setActive] = useState(0);
  return (
    <figure>
      <div className="bg-muted relative overflow-hidden rounded-xl" style={{ aspectRatio: `${width} / ${height}` }}>
        {items.map((it, i) => (
          // eslint-disable-next-line @next/next/no-img-element -- small pre-encoded webp
          <img
            key={it.src}
            src={it.src}
            alt={`${title} — ${it.label}`}
            aria-hidden={i !== active}
            className={cn("absolute inset-0 h-full w-full object-cover transition-opacity duration-150", i === active ? "opacity-100" : "opacity-0")}
          />
        ))}
        <div role="tablist" aria-label={title} className="bg-background/80 absolute top-3 left-3 flex flex-wrap gap-0.5 rounded-md p-0.5 backdrop-blur">
          {items.map((it, i) => (
            <button
              key={it.label}
              type="button"
              role="tab"
              aria-selected={i === active}
              onClick={() => setActive(i)}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight") setActive((a) => (a + 1) % items.length);
                else if (e.key === "ArrowLeft") setActive((a) => (a - 1 + items.length) % items.length);
              }}
              className={cn(
                "rounded px-2 py-0.5 font-mono text-[11px] transition-colors",
                i === active ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {it.label}
            </button>
          ))}
        </div>
      </div>
      <figcaption className="mt-3">
        <p className="text-sm">{title}</p>
        <p className="text-muted-foreground mt-1 font-mono text-[11px] tracking-tight">{items[active].label}: {items[active].meta}</p>
        {note && <p className="text-muted-foreground mt-0.5 text-[11px]">{note}</p>}
      </figcaption>
    </figure>
  );
}
