import { describe, expect, it } from "vitest";
import { columnsFor, distribute } from "@/lib/masonry";

const ratio = (x: { a: number }) => x.a;

describe("distribute", () => {
  it("puts the first items across the top row, then fills the shortest column", () => {
    const items = [{ id: 1, a: 1 }, { id: 2, a: 0.5 }, { id: 3, a: 0.75 }, { id: 4, a: 1 }, { id: 5, a: 1 }];
    const cols = distribute(items, 3, ratio).map((c) => c.map((i) => i.id));
    expect(cols.map((c) => c[0])).toEqual([1, 2, 3]); // newest stay at the top
    expect(cols).toEqual([[1], [2, 4], [3, 5]]); // 4 → col 1 (0.5), 5 → col 2 (0.75)
  });

  it("never moves existing items when more are appended", () => {
    const items = Array.from({ length: 40 }, (_, i) => ({ id: i, a: 0.4 + ((i * 37) % 10) / 10 }));
    const before = distribute(items.slice(0, 24), 3, ratio).map((c) => c.map((i) => i.id));
    const after = distribute(items, 3, ratio).map((c) => c.map((i) => i.id));
    before.forEach((col, c) => expect(after[c].slice(0, col.length)).toEqual(col));
  });

  it("treats unknown aspect ratios as square and handles 1 column", () => {
    const cols = distribute([{ a: NaN }, { a: 0 }, { a: 2 }], 1, ratio);
    expect(cols).toHaveLength(1);
    expect(cols[0]).toHaveLength(3);
  });

  it("keeps columns balanced", () => {
    const items = Array.from({ length: 200 }, (_, i) => ({ a: 0.5 + ((i * 7) % 5) / 5 }));
    const heights = distribute(items, 4, ratio).map((c) => c.reduce((s, i) => s + i.a, 0));
    expect(Math.max(...heights) - Math.min(...heights)).toBeLessThan(1.5);
  });
});

describe("columnsFor", () => {
  it("maps widths to 1–4 columns", () => {
    expect([360, 600, 900, 1300].map(columnsFor)).toEqual([1, 2, 3, 4]);
  });
});
