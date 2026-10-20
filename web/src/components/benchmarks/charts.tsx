"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/*
 * Small hand-rolled SVG charts for /benchmarks. Series colours are categorical slots 1–4 of the validated
 * palette (blue, orange, aqua, yellow), stepped separately for light and dark in VIZ_STYLE. Slots 3–4 sit
 * under 3:1 on the light surface, so every series also has its own marker shape, a legend and a table view.
 * Text always uses text tokens, never a series colour.
 */

export const VIZ_STYLE = `
.viz {
  --viz-s1: #2a78d6;
  --viz-s2: #eb6834;
  --viz-s3: #1baf7a;
  --viz-s4: #eda100;
  --viz-grid: #e1e0d9;
  --viz-axis: #c3c2b7;
  --viz-good: #006300;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz {
    --viz-s1: #3987e5;
    --viz-s2: #d95926;
    --viz-s3: #199e70;
    --viz-s4: #c98500;
    --viz-grid: #2c2c2a;
    --viz-axis: #383835;
    --viz-good: #0ca30c;
  }
}
:root[data-theme="dark"] .viz {
  --viz-s1: #3987e5;
  --viz-s2: #d95926;
  --viz-s3: #199e70;
  --viz-s4: #c98500;
  --viz-grid: #2c2c2a;
  --viz-axis: #383835;
  --viz-good: #0ca30c;
}
`;

export type Shape = "circle" | "square" | "diamond" | "triangle";
export type SeriesSpec = { id: string; label: string; color: string; shape: Shape; dashed?: boolean };

/** Number formats by name (server components can't pass functions to these client charts). */
export type Fmt = "sec" | "usd";
const px = (v: number) => Math.round(v * 100) / 100;

const FORMATS: Record<Fmt, (v: number) => string> = {
  sec: (v) => (v >= 60 ? `${(v / 60).toFixed(v >= 600 ? 0 : 1)} min` : `${v >= 10 ? v.toFixed(0) : v.toFixed(1)} s`),
  usd: (v) => (v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : v < 1 ? `$${v.toFixed(3)}` : v < 100 ? `$${v.toFixed(2)}` : `$${Math.round(v).toLocaleString("en-US")}`),
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

/** Decade ticks (1, 10, 100…) covering [lo, hi], with 3× in-between ticks when there are few decades. */
function logTicks(lo: number, hi: number) {
  const out: number[] = [];
  const a = Math.floor(Math.log10(lo));
  const b = Math.ceil(Math.log10(hi));
  for (let e = a; e <= b; e++) {
    out.push(10 ** e);
    if (b - a <= 3 && e < b) out.push(3 * 10 ** e);
  }
  return out;
}

type Tip = { x: number; y: number; content: ReactNode } | null;

function Tooltip({ tip, width }: { tip: Tip; width: number }) {
  if (!tip) return null;
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

function Marker({ shape, x, y, color, r = 5 }: { shape: Shape; x: number; y: number; color: string; r?: number }) {
  const common = { fill: color, stroke: "var(--background)", strokeWidth: 2 };
  if (shape === "square") return <rect x={x - r} y={y - r} width={r * 2} height={r * 2} rx={1.5} {...common} />;
  if (shape === "diamond") return <path d={`M${x} ${y - r - 1}L${x + r + 1} ${y}L${x} ${y + r + 1}L${x - r - 1} ${y}Z`} {...common} />;
  if (shape === "triangle") return <path d={`M${x} ${y - r - 1}L${x + r + 1} ${y + r}L${x - r - 1} ${y + r}Z`} {...common} />;
  return <circle cx={x} cy={y} r={r} {...common} />;
}

export function Legend({ series }: { series: SeriesSpec[] }) {
  return (
    <ul className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {series.map((s) => (
        <li key={s.id} className="flex items-center gap-1.5">
          <svg width={14} height={14} aria-hidden>
            {s.dashed ? (
              <line x1={1} x2={13} y1={7} y2={7} stroke={s.color} strokeWidth={2} strokeDasharray="3 2" />
            ) : (
              <Marker shape={s.shape} x={7} y={7} color={s.color} r={4.5} />
            )}
          </svg>
          {s.label}
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

const LABEL_W = 170;

/** One row per job, one marker per option, on a shared log axis. Lower is better. */
export function MultiDot({
  rows,
  series,
  fmt,
  ariaLabel,
}: {
  rows: { label: string; values: Partial<Record<string, number>> }[];
  series: SeriesSpec[];
  fmt: Fmt;
  ariaLabel: string;
}) {
  const format = FORMATS[fmt];
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const [hover, setHover] = useState<number | null>(null);
  const narrow = width < 520;
  const labelW = narrow ? 0 : LABEL_W;
  const rowH = narrow ? 48 : 32;
  const top = 6;
  const right = 16;
  const plotW = width - labelW - right;
  const all = rows.flatMap((r) => series.map((s) => r.values[s.id]).filter((v): v is number => v != null && v > 0));
  const tk = logTicks(Math.min(...all), Math.max(...all));
  const lo = Math.log10(tk[0]);
  const hi = Math.log10(tk[tk.length - 1]);
  // rounded: server and browser Math.log10 can differ in the last digit, which breaks hydration
  const x = (v: number) => px(labelW + ((Math.log10(v) - lo) / (hi - lo)) * plotW);
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
          const present = series.filter((s) => r.values[s.id] != null && r.values[s.id]! > 0);
          const xs = present.map((s) => x(r.values[s.id]!));
          return (
            <g
              key={r.label}
              onMouseEnter={() => {
                setHover(i);
                setTip({
                  x: (Math.min(...xs) + Math.max(...xs)) / 2,
                  y: cy - 6,
                  content: (
                    <>
                      <p className="font-medium">{r.label}</p>
                      {present.map((s) => (
                        <p key={s.id} className="flex justify-between gap-4 font-mono tabular-nums">
                          <span className="text-muted-foreground">{s.label}</span>
                          <span>{format(r.values[s.id]!)}</span>
                        </p>
                      ))}
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
                <text x={0} y={cy - 13} className="fill-foreground text-[11px]">{r.label}</text>
              ) : (
                <text x={labelW - 12} y={cy + 4} textAnchor="end" className={cn("text-[12px]", hover === i ? "fill-foreground" : "fill-muted-foreground")}>
                  {r.label}
                </text>
              )}
              <line x1={Math.min(...xs)} x2={Math.max(...xs)} y1={cy} y2={cy} stroke="var(--viz-axis)" strokeWidth={2} strokeLinecap="round" />
              {present.map((s) => (
                <Marker key={s.id} shape={s.shape} x={x(r.values[s.id]!)} y={cy} color={s.color} />
              ))}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  );
}

/** Log–log lines with a crosshair tooltip at the nearest x. */
export function LineChart({
  series,
  data,
  ariaLabel,
  marker,
}: {
  series: SeriesSpec[];
  data: { id: string; points: { x: number; y: number }[] }[];
  ariaLabel: string;
  marker?: { x: number; label: string };
}) {
  const format = FORMATS.usd;
  const xLabel = (v: number) => (v >= 1000 ? `${v / 1000}k` : `${v}`);
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hx, setHx] = useState<number | null>(null);
  const h = 300;
  const left = 56;
  const right = 16;
  const top = 10;
  const bottom = 34;
  const xs = data[0].points.map((p) => p.x);
  const ys = data.flatMap((d) => d.points.map((p) => p.y));
  const xt = logTicks(Math.min(...xs), Math.max(...xs)).filter((t) => t >= Math.min(...xs) && t <= Math.max(...xs));
  const yt = logTicks(Math.min(...ys), Math.max(...ys)).filter((_, i, a) => a.length <= 7 || i % 2 === 0);
  const [x0, x1] = [Math.log10(Math.min(...xs)), Math.log10(Math.max(...xs))];
  const [y0, y1] = [Math.log10(Math.min(yt[0], Math.min(...ys))), Math.log10(Math.max(yt[yt.length - 1], Math.max(...ys)))];
  const X = (v: number) => px(left + ((Math.log10(v) - x0) / (x1 - x0)) * (width - left - right));
  const Y = (v: number) => px(top + (1 - (Math.log10(v) - y0) / (y1 - y0)) * (h - top - bottom));
  const spec = Object.fromEntries(series.map((s) => [s.id, s]));
  // End-of-line labels, pushed apart so they never overlap (≥12px).
  const ends = data
    .map((d) => ({ id: d.id, y: Y(d.points[d.points.length - 1].y) - 6 }))
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 12);
  const labelY = Object.fromEntries(ends.map((e) => [e.id, e.y]));
  const nearest = hx == null ? null : xs.reduce((a, b) => (Math.abs(X(b) - hx) < Math.abs(X(a) - hx) ? b : a));

  return (
    <div ref={ref} className="relative">
      <svg
        width={width}
        height={h}
        role="img"
        aria-label={ariaLabel}
        className="block overflow-visible"
        onMouseMove={(e) => setHx(e.clientX - e.currentTarget.getBoundingClientRect().left)}
        onMouseLeave={() => setHx(null)}
      >
        {yt.map((t) => (
          <g key={`y${t}`}>
            <line x1={left} x2={width - right} y1={Y(t)} y2={Y(t)} stroke="var(--viz-grid)" strokeWidth={1} />
            <text x={left - 8} y={Y(t) + 3} textAnchor="end" className="fill-muted-foreground text-[10px] tabular-nums">
              {format(t)}
            </text>
          </g>
        ))}
        {xt.map((t) => (
          <text key={`x${t}`} x={X(t)} y={h - bottom + 16} textAnchor="middle" className="fill-muted-foreground text-[10px] tabular-nums">
            {xLabel(t)}
          </text>
        ))}
        <text x={width - right} y={h - 4} textAnchor="end" className="fill-muted-foreground text-[10px]">
          jobs per month →
        </text>
        {marker && (
          <g>
            <line x1={X(marker.x)} x2={X(marker.x)} y1={top} y2={h - bottom} stroke="var(--viz-axis)" strokeWidth={1} strokeDasharray="2 3" />
            <text x={X(marker.x) - 6} y={h - bottom - 6} textAnchor="end" className="fill-muted-foreground text-[10px]">
              {marker.label}
            </text>
          </g>
        )}
        {data.map((d) => {
          const s = spec[d.id];
          return (
            <g key={d.id}>
              <path
                d={d.points.map((p, i) => `${i ? "L" : "M"}${X(p.x)} ${Y(p.y)}`).join("")}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeDasharray={s.dashed ? "5 4" : undefined}
                strokeLinejoin="round"
              />
              <text
                x={width - right}
                y={labelY[d.id]}
                textAnchor="end"
                className="fill-foreground text-[10px]"
                style={{ paintOrder: "stroke", stroke: "var(--background)", strokeWidth: 3, strokeLinejoin: "round" }}
              >
                {s.label}
              </text>
            </g>
          );
        })}
        {nearest != null && (
          <g>
            <line x1={X(nearest)} x2={X(nearest)} y1={top} y2={h - bottom} stroke="var(--viz-axis)" strokeWidth={1} />
            {data.map((d) => {
              const p = d.points.find((q) => q.x === nearest)!;
              return <Marker key={d.id} shape={spec[d.id].shape} x={X(p.x)} y={Y(p.y)} color={spec[d.id].color} r={4.5} />;
            })}
          </g>
        )}
      </svg>
      {nearest != null && (
        <Tooltip
          width={width}
          tip={{
            x: X(nearest),
            y: top + 24,
            content: (
              <>
                <p className="font-medium">{xLabel(nearest)} stills / month</p>
                {data.map((d) => (
                  <p key={d.id} className="flex justify-between gap-4 font-mono tabular-nums">
                    <span className="text-muted-foreground">{spec[d.id].label}</span>
                    <span>{format(d.points.find((q) => q.x === nearest)!.y)}</span>
                  </p>
                ))}
              </>
            ),
          }}
        />
      )}
    </div>
  );
}

export function StatTile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="border-border rounded-lg border p-4">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {note && <p className="text-muted-foreground mt-1 text-[11px]">{note}</p>}
    </div>
  );
}
