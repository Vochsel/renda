import { fmtMinutes, fmtUsd } from "@/lib/format";

type Day = { day: string; seconds: number; costUsd: number };

/** Daily render-minutes for the current month: one bar per day, value on hover. */
export function SpendChart({ days, monthStart }: { days: Day[]; monthStart: Date }) {
  const n = new Date(Date.UTC(monthStart.getUTCFullYear(), monthStart.getUTCMonth() + 1, 0)).getUTCDate();
  const byDay = new Map(days.map((d) => [d.day, d]));
  const series = Array.from({ length: n }, (_, i) => {
    const key = new Date(Date.UTC(monthStart.getUTCFullYear(), monthStart.getUTCMonth(), i + 1)).toISOString().slice(0, 10);
    const d = byDay.get(key);
    return { key, dom: i + 1, seconds: d?.seconds ?? 0, costUsd: d?.costUsd ?? 0 };
  });
  const max = Math.max(60, ...series.map((s) => s.seconds));
  const W = 600, H = 140, gap = 2, bw = W / n - gap;
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full" role="img" aria-label="Render minutes per day this month">
        <line x1={0} x2={W} y1={H} y2={H} className="stroke-border" />
        {series.map((s, i) => {
          const h = (s.seconds / max) * H;
          return (
            <g key={s.key}>
              <title>{`${s.key}: ${fmtMinutes(s.seconds)} · ${fmtUsd(s.costUsd)}`}</title>
              <rect x={i * (bw + gap)} y={H - h} width={bw} height={Math.max(h, s.seconds ? 1 : 0)} rx={2} className="fill-primary" />
              <rect x={i * (bw + gap)} y={0} width={bw + gap} height={H} fill="transparent" />
              {(s.dom === 1 || s.dom % 5 === 0) && (
                <text x={i * (bw + gap) + bw / 2} y={H + 13} textAnchor="middle" className="fill-muted-foreground text-[10px]">{s.dom}</text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="text-muted-foreground mt-1 text-xs">Day of month · bar height scaled to {fmtMinutes(max)}</figcaption>
    </figure>
  );
}
