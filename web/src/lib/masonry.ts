/**
 * Shortest-column masonry: each item goes to the column whose accumulated height is smallest, so the
 * first N items fill the top of the N columns (newest stays at the top) instead of CSS columns' top-to-bottom
 * order. Heights are aspect ratios (h / w) because columns share one width. Deterministic: appending items
 * never moves the ones already placed.
 */
export function distribute<T>(items: readonly T[], columns: number, aspect: (item: T) => number, gap = 0): T[][] {
  const n = Math.max(1, Math.floor(columns));
  const cols: T[][] = Array.from({ length: n }, () => []);
  const heights = new Array<number>(n).fill(0);
  for (const item of items) {
    let target = 0;
    for (let c = 1; c < n; c++) if (heights[c] < heights[target] - 1e-9) target = c;
    cols[target].push(item);
    const a = aspect(item);
    heights[target] += (Number.isFinite(a) && a > 0 ? a : 1) + gap;
  }
  return cols;
}

/** Responsive column count for a container width (px). */
export function columnsFor(width: number): number {
  if (width < 480) return 1;
  if (width < 760) return 2;
  if (width < 1100) return 3;
  return 4;
}
