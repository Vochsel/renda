"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/*
 * Small hand-rolled SVG charts for /benchmarks. Colour roles come from CSS custom properties on `.viz`
 * (see VIZ_STYLE): `--viz-before` / `--viz-after` are categorical slots 2 and 1 of the validated palette,
 * stepped separately for light and dark. Text always uses text tokens, never a series colour.
 */

export const VIZ_STYLE = `
.viz {
  --viz-after: #2a78d6;
  --viz-before: #eb6834;
  --viz-grid: #e1e0d9;
  --viz-axis: #c3c2b7;
  --viz-band: rgba(42, 120, 214, 0.16);
  --viz-good: #006300;
}
@media (prefers-color-scheme: dark) {
  :root:not(.light) .viz {
    --viz-after: #3987e5;
    --viz-before: #d95926;
    --viz-grid: #2c2c2a;
    --viz-axis: #383835;
    --viz-band: rgba(57, 135, 229, 0.22);
    --viz-good: #0ca30c;
  }
}
.dark .viz {
  --viz-after: #3987e5;
  --viz-before: #d95926;
  --viz-grid: #2c2c2a;
  --viz-axis: #383835;
  --viz-band: rgba(57, 135, 229, 0.22);
  --viz-good: #0ca30c;
}
`;

/** Number formats by name (server components can't pass functions to these client charts). */
export type Fmt = "sec" | "usd" | "usd3" | "min" | "db";
const FORMATS: Record<Fmt, (v: number) => string> = {
  sec: (v) => (v >= 100 ? v.toFixed(0) : v.toFixed(1)).replace(/\.0$/, ""),
  usd: (v) => (v === 0 ? "$0" : v < 0.1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`),
  usd3: (v) => `$${v.toFixed(3)}`,
  min: (v) => `${v.toFixed(1)} min`,
  db: (v) => `${v} dB`,
};

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Clean ticks for a linear 0..max scale. */
function ticks(max: number, count = 4) {
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [0];
  // Always end on a tick at or past the max, so every mark sits inside the plot.
  while (out[out.length - 1] < max - step * 1e-6) out.push(Number((out[out.length - 1] + step).toFixed(6)));
  return out;
}

type Tip = { x: number; y: number; content: ReactNode } | null;

function Tooltip({ tip, width }: { tip: Tip; width: number }) {
  if (!tip) return null;
  // Keep the 16rem-wide tooltip inside the chart box.
  const half = 128;
  const left = Math.min(Math.max(tip.x, half), Math.max(half, width - half));
  return (
    <div
      role="status"
      className="bg-popover text-popover-foreground border-border pointer-events-none absolute z-10 w-max max-w-64 -translate-x-1/2 -translate-y-full rounded-md border px-2.5 py-1.5 text-xs shadow-sm"
      style={{ left, top: tip.y - 10 }}
    >
      {tip.content}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; shape?: "dot" | "band" }[] }) {
  return (
    <ul className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          {i.shape === "band" ? (
            <span className="inline-block h-2 w-4 rounded-sm" style={{ background: i.color }} />
          ) : (
            <span className="inline-block size-2.5 rounded-full" style={{ background: i.color }} />
          )}
          {i.label}
        </li>
      ))}
    </ul>
  );
}

export function DataTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <details className="mt-3 text-xs">
      <summary className="text-muted-foreground hover:text-foreground cursor-pointer select-none">Show table</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[11px] tabular-nums">
          <thead>
            <tr className="text-muted-foreground border-border border-b text-left">
              {head.map((h) => (
                <th key={h} className="py-1.5 pr-4 font-normal">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-border/60 border-b">
                {r.map((c, j) => (
                  <td key={j} className="py-1.5 pr-4">{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

const LABEL_W = 210;
const ROW_H = 30;

/** Before → after dumbbell per row on one linear axis. Lower is better. */
export function Dumbbell({
  rows,
  fmt,
  unit,
  ariaLabel,
}: {
  rows: { label: string; before: number; after: number; note?: string }[];
  fmt: Fmt;
  unit: string;
  ariaLabel: string;
}) {
  const format = FORMATS[fmt];
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const [hover, setHover] = useState<number | null>(null);
  const narrow = width < 520;
  const labelW = narrow ? 0 : LABEL_W;
  const rowH = narrow ? ROW_H + 18 : ROW_H;
  const top = 8;
  const right = 64;
  const plotW = width - labelW - right;
  const max = Math.max(...rows.flatMap((r) => [r.before, r.after]));
  const tk = ticks(max);
  const xmax = tk[tk.length - 1];
  const x = (v: number) => labelW + (v / xmax) * plotW;
  const h = top + rows.length * rowH + 24;

  return (
    <div ref={ref} className="relative">
      <svg width={width} height={h} role="img" aria-label={ariaLabel} className="block overflow-visible">
        {tk.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={top} y2={h - 22} stroke="var(--viz-grid)" strokeWidth={1} />
            <text x={x(t)} y={h - 6} textAnchor="middle" className="fill-muted-foreground text-[10px] tabular-nums">
              {format(t)}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const cy = top + i * rowH + (narrow ? rowH - 12 : rowH / 2);
          const pct = Math.round(((r.after - r.before) / r.before) * 100);
          return (
            <g
              key={r.label}
              onMouseEnter={() => {
                setHover(i);
                setTip({
                  x: (x(r.before) + x(r.after)) / 2,
                  y: cy,
                  content: (
                    <>
                      <p className="font-medium">{r.label}</p>
                      <p className="font-mono tabular-nums">
                        {format(r.before)} → {format(r.after)} {unit} · {pct > 0 ? "+" : ""}{pct}%
                      </p>
                      {r.note && <p className="text-muted-foreground mt-0.5">{r.note}</p>}
                    </>
                  ),
                });
              }}
              onMouseLeave={() => {
                setHover(null);
                setTip(null);
              }}
            >
              <rect x={0} y={cy - rowH / 2} width={width} height={rowH} fill="transparent" />
              {narrow ? (
                <text x={0} y={cy - 12} className="fill-foreground text-[11px]">{r.label}</text>
              ) : (
                <text x={labelW - 12} y={cy + 4} textAnchor="end" className={cn("text-[12px]", hover === i ? "fill-foreground" : "fill-muted-foreground")}>
                  {r.label}
                </text>
              )}
              <line x1={x(r.before)} x2={x(r.after)} y1={cy} y2={cy} stroke="var(--viz-axis)" strokeWidth={2} strokeLinecap="round" />
              <circle cx={x(r.before)} cy={cy} r={5} fill="var(--viz-before)" stroke="var(--background)" strokeWidth={2} />
              <circle cx={x(r.after)} cy={cy} r={5} fill="var(--viz-after)" stroke="var(--background)" strokeWidth={2} />
              <text x={x(Math.max(r.before, r.after)) + 10} y={cy + 4} className="fill-muted-foreground font-mono text-[10px] tabular-nums">
                {pct > 0 ? "+" : ""}{pct}%
              </text>
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  );
}

/** One row per item: sample dots on a log axis with a median tick. Single series, so no legend. */
export function StripPlot({
  rows,
  ariaLabel,
}: {
  rows: { label: string; variant: string; samples: number[]; warm: number }[];
  ariaLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const narrow = width < 520;
  const labelW = narrow ? 0 : LABEL_W;
  const rowH = narrow ? 50 : 38;
  const top = 8;
  const right = narrow ? 8 : 70;
  const plotW = width - labelW - right;
  const lo = 1;
  const hi = 200;
  const x = (v: number) => labelW + ((Math.log10(Math.max(lo, v)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * plotW;
  const tk = [1, 3, 10, 30, 100, 200];
  const h = top + rows.length * rowH + 24;
  const median = (s: number[]) => {
    const a = [...s].sort((p, q) => p - q);
    return a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2;
  };

  return (
    <div ref={ref} className="relative">
      <svg width={width} height={h} role="img" aria-label={ariaLabel} className="block overflow-visible">
        {tk.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={top} y2={h - 22} stroke="var(--viz-grid)" strokeWidth={1} />
            <text x={x(t)} y={h - 6} textAnchor="middle" className="fill-muted-foreground text-[10px] tabular-nums">{t} s</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const cy = top + i * rowH + (narrow ? rowH - 12 : rowH / 2);
          const m = median(r.samples);
          return (
            <g key={r.label + r.variant}>
              {narrow ? (
                <text x={0} y={cy - 14} className="fill-foreground text-[11px]">{r.label} · {r.variant} · median {m} s</text>
              ) : (
                <>
                  <text x={labelW - 12} y={cy - 2} textAnchor="end" className="fill-foreground text-[12px]">{r.label}</text>
                  <text x={labelW - 12} y={cy + 11} textAnchor="end" className="fill-muted-foreground text-[10px]">{r.variant}</text>
                </>
              )}
              <line x1={x(m)} x2={x(m)} y1={cy - 9} y2={cy + 9} className="stroke-foreground" strokeWidth={2} strokeLinecap="round" />
              {r.samples.map((s, j) => (
                <circle
                  key={j}
                  cx={x(s)}
                  cy={cy}
                  r={4.5}
                  fill="var(--viz-after)"
                  fillOpacity={0.8}
                  stroke="var(--background)"
                  strokeWidth={2}
                  onMouseEnter={() =>
                    setTip({
                      x: x(s),
                      y: cy,
                      content: (
                        <>
                          <p className="font-medium">{r.label} · {r.variant}</p>
                          <p className="font-mono tabular-nums">cold start {s} s · median {m} s · warm {r.warm} s</p>
                        </>
                      ),
                    })
                  }
                  onMouseLeave={() => setTip(null)}
                />
              ))}
              {!narrow && <text x={width - right + 10} y={cy + 4} className="fill-muted-foreground font-mono text-[10px] tabular-nums">median {m} s</text>}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  );
}

/** Estimate range (band) vs the billed value (dot). */
export function RangeChart({
  rows,
  unit,
  ariaLabel,
}: {
  rows: { label: string; lo: number; hi: number; actual: number; note?: string }[];
  unit: string;
  ariaLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const narrow = width < 520;
  const labelW = narrow ? 0 : LABEL_W;
  const rowH = narrow ? 44 : ROW_H;
  const top = 8;
  const right = narrow ? 12 : 72;
  const plotW = width - labelW - right;
  const max = Math.max(...rows.flatMap((r) => [r.hi, r.actual]));
  const tk = ticks(max);
  const xmax = tk[tk.length - 1];
  const x = (v: number) => labelW + (v / xmax) * plotW;
  const h = top + rows.length * rowH + 24;

  return (
    <div ref={ref} className="relative">
      <svg width={width} height={h} role="img" aria-label={ariaLabel} className="block overflow-visible">
        {tk.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={top} y2={h - 22} stroke="var(--viz-grid)" strokeWidth={1} />
            <text x={x(t)} y={h - 6} textAnchor="middle" className="fill-muted-foreground text-[10px] tabular-nums">{t} {unit}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const cy = top + i * rowH + (narrow ? rowH - 12 : rowH / 2);
          const inside = r.actual >= r.lo && r.actual <= r.hi;
          return (
            <g
              key={r.label}
              onMouseEnter={() =>
                setTip({
                  x: x(r.actual),
                  y: cy,
                  content: (
                    <>
                      <p className="font-medium">{r.label}</p>
                      <p className="font-mono tabular-nums">estimate {r.lo}–{r.hi} {unit} · billed {r.actual} {unit}</p>
                      <p className="text-muted-foreground">{inside ? "within the estimate" : "outside the estimate"}{r.note ? ` — ${r.note}` : ""}</p>
                    </>
                  ),
                })
              }
              onMouseLeave={() => setTip(null)}
            >
              <rect x={0} y={cy - rowH / 2} width={width} height={rowH} fill="transparent" />
              {narrow ? (
                <text x={0} y={cy - 12} className="fill-foreground text-[11px]">{r.label} · {inside ? "in range" : "outside"}</text>
              ) : (
                <text x={labelW - 12} y={cy + 4} textAnchor="end" className="fill-muted-foreground text-[12px]">{r.label}</text>
              )}
              <rect x={x(r.lo)} y={cy - 5} width={Math.max(2, x(r.hi) - x(r.lo))} height={10} rx={4} fill="var(--viz-band)" />
              <circle cx={x(r.actual)} cy={cy} r={5} fill={inside ? "var(--viz-after)" : "var(--viz-before)"} stroke="var(--background)" strokeWidth={2} />
              {!narrow && (
                <text x={width - right + 10} y={cy + 4} className="fill-muted-foreground font-mono text-[10px] tabular-nums">
                  {inside ? "in range" : "outside"}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  );
}

/** Small column chart (zero baseline), single series, value on each cap. */
export function Columns({
  title,
  points,
  fmt,
  ariaLabel,
}: {
  title: string;
  points: { label: string; value: number; sub?: string }[];
  fmt: Fmt;
  ariaLabel: string;
}) {
  const format = FORMATS[fmt];
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const h = 170;
  const top = 22;
  const bottom = 34;
  const max = Math.max(...points.map((p) => p.value)) * 1.1;
  const band = width / points.length;
  const bw = Math.min(24, band * 0.5);
  const y = (v: number) => top + (1 - v / max) * (h - top - bottom);
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1 text-sm">{title}</figcaption>
      <div ref={ref} className="relative">
        <svg width={width} height={h} role="img" aria-label={ariaLabel} className="block overflow-visible">
          <line x1={0} x2={width} y1={h - bottom} y2={h - bottom} stroke="var(--viz-axis)" strokeWidth={1} />
          {points.map((p, i) => {
            const cx = band * i + band / 2;
            const y0 = y(p.value);
            const bh = h - bottom - y0;
            const r = Math.min(4, bh / 2);
            return (
              <g
                key={p.label}
                onMouseEnter={() => setTip({ x: cx, y: y0, content: <p className="font-mono tabular-nums">{p.label}: {format(p.value)}{p.sub ? ` · ${p.sub}` : ""}</p> })}
                onMouseLeave={() => setTip(null)}
              >
                <rect x={band * i} y={top} width={band} height={h - top - bottom} fill="transparent" />
                <path
                  d={`M${cx - bw / 2},${h - bottom} V${y0 + r} Q${cx - bw / 2},${y0} ${cx - bw / 2 + r},${y0} H${cx + bw / 2 - r} Q${cx + bw / 2},${y0} ${cx + bw / 2},${y0 + r} V${h - bottom} Z`}
                  fill="var(--viz-after)"
                />
                <text x={cx} y={y0 - 6} textAnchor="middle" className="fill-foreground font-mono text-[10px] tabular-nums">{format(p.value)}</text>
                <text x={cx} y={h - bottom + 14} textAnchor="middle" className="fill-muted-foreground text-[10px]">{p.label}</text>
                {p.sub && <text x={cx} y={h - bottom + 26} textAnchor="middle" className="fill-muted-foreground text-[9px]">{p.sub}</text>}
              </g>
            );
          })}
        </svg>
        <Tooltip tip={tip} width={width} />
      </div>
    </figure>
  );
}

/** Dots on a non-zero axis (for quality metrics like PSNR, where bars from zero would mislead). */
export function DotSeries({
  title,
  points,
  domain,
  fmt,
  ariaLabel,
}: {
  title: string;
  points: { label: string; value: number; sub?: string }[];
  domain: [number, number];
  fmt: Fmt;
  ariaLabel: string;
}) {
  const format = FORMATS[fmt];
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const h = 170;
  const top = 22;
  const bottom = 34;
  const band = width / points.length;
  const y = (v: number) => top + (1 - (v - domain[0]) / (domain[1] - domain[0])) * (h - top - bottom);
  const gridVals = [domain[0], (domain[0] + domain[1]) / 2, domain[1]];
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1 text-sm">{title}</figcaption>
      <div ref={ref} className="relative">
        <svg width={width} height={h} role="img" aria-label={ariaLabel} className="block overflow-visible">
          {gridVals.map((g) => (
            <g key={g}>
              <line x1={0} x2={width} y1={y(g)} y2={y(g)} stroke="var(--viz-grid)" strokeWidth={1} />
              <text x={0} y={y(g) - 3} className="fill-muted-foreground text-[9px] tabular-nums">{g} dB</text>
            </g>
          ))}
          <polyline
            points={points.map((p, i) => `${band * i + band / 2},${y(p.value)}`).join(" ")}
            fill="none"
            stroke="var(--viz-after)"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {points.map((p, i) => {
            const cx = band * i + band / 2;
            return (
              <g
                key={p.label}
                onMouseEnter={() => setTip({ x: cx, y: y(p.value), content: <p className="font-mono tabular-nums">{p.label}: {format(p.value)}{p.sub ? ` · ${p.sub}` : ""}</p> })}
                onMouseLeave={() => setTip(null)}
              >
                <rect x={band * i} y={top} width={band} height={h - top - bottom} fill="transparent" />
                <circle cx={cx} cy={y(p.value)} r={5} fill="var(--viz-after)" stroke="var(--background)" strokeWidth={2} />
                <text x={cx} y={y(p.value) - 9} textAnchor="middle" className="fill-foreground font-mono text-[10px] tabular-nums">{format(p.value)}</text>
                <text x={cx} y={h - bottom + 14} textAnchor="middle" className="fill-muted-foreground text-[10px]">{p.label}</text>
                {p.sub && <text x={cx} y={h - bottom + 26} textAnchor="middle" className="fill-muted-foreground text-[9px]">{p.sub}</text>}
              </g>
            );
          })}
        </svg>
        <Tooltip tip={tip} width={width} />
      </div>
    </figure>
  );
}

export function StatTile({ label, value, delta, note }: { label: string; value: string; delta?: string; note?: string }) {
  return (
    <div className="border-border rounded-lg border p-4">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {delta && (
        <p className="mt-1 flex items-center gap-1 font-mono text-[11px]" style={{ color: "var(--viz-good)" }}>
          <span aria-hidden>↓</span>
          <span>{delta}</span>
        </p>
      )}
      {note && <p className="text-muted-foreground mt-1 text-[11px]">{note}</p>}
    </div>
  );
}
