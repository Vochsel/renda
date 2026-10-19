"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { OutputItem, OutputsPage } from "@/lib/outputs";
import { columnsFor, distribute } from "@/lib/masonry";
import { GalleryTile } from "./tile";
import { Lightbox } from "./lightbox";

const GAP = 12; // px, matches gap-3

export function aspectOf(item: Pick<OutputItem, "width" | "height" | "kind">): number {
  if (item.width && item.height) return item.height / item.width;
  return item.kind === "model" ? 1 : 0.75;
}

/**
 * Masonry gallery of render outputs: shortest-column placement (newest across the top row), infinite scroll
 * from a cursor, tiles that reserve their aspect ratio so nothing shifts as thumbnails stream in.
 */
export function Gallery({
  initial,
  loadMore,
  empty,
}: {
  initial: OutputsPage;
  loadMore: (cursor: string) => Promise<OutputsPage>;
  empty?: React.ReactNode;
}) {
  const [items, setItems] = useState(initial.items);
  const [cursor, setCursor] = useState(initial.nextCursor);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [columns, setColumns] = useState(3);
  const [width, setWidth] = useState(0);
  const container = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const inflight = useRef(false);

  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width;
      setWidth(w);
      setColumns(columnsFor(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const next = useCallback(async () => {
    if (!cursor || inflight.current) return;
    inflight.current = true;
    setLoading(true);
    setError(null);
    try {
      const page = await loadMore(cursor);
      setItems((prev) => {
        const seen = new Set(prev.map((i) => i.id));
        return [...prev, ...page.items.filter((i) => !seen.has(i.id))];
      });
      setCursor(page.nextCursor);
    } catch {
      setError("Couldn't load more renders.");
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, [cursor, loadMore]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el || !cursor) return;
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && void next(), { rootMargin: "800px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [cursor, next]);

  const cols = useMemo(() => {
    const indexed = items.map((item, index) => ({ item, index }));
    // Columns share one width, so a tile's height is its aspect ratio; the gap is the same fraction of it.
    const colWidth = width ? (width - GAP * (columns - 1)) / columns : 300;
    return distribute(indexed, columns, ({ item }) => aspectOf(item), GAP / colWidth);
  }, [items, columns, width]);

  if (!items.length && !cursor) return <>{empty ?? <p className="text-muted-foreground text-sm">No renders yet.</p>}</>;

  return (
    <div>
      <div ref={container} className="flex items-start gap-3">
        {cols.map((col, c) => (
          <div key={c} className="flex min-w-0 flex-1 flex-col gap-3">
            {col.map(({ item, index }) => (
              <GalleryTile key={item.id} item={item} aspect={aspectOf(item)} priority={index < columns * 2} onOpen={() => setOpen(index)} />
            ))}
          </div>
        ))}
      </div>
      <div ref={sentinel} aria-hidden className="h-px" />
      <div className="text-muted-foreground py-6 text-center font-mono text-xs" aria-live="polite">
        {loading ? "loading…" : error ? (
          <button type="button" onClick={() => void next()} className="underline underline-offset-2">{error} Retry</button>
        ) : cursor ? null : items.length > 0 ? `${items.length} render${items.length === 1 ? "" : "s"}` : null}
      </div>
      {open !== null && items[open] && (
        <Lightbox
          items={items}
          index={open}
          onIndex={(i) => {
            setOpen(i);
            if (i >= items.length - 3) void next();
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}
