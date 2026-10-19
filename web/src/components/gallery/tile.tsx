"use client";

import { useState } from "react";
import { Box, Play } from "lucide-react";
import type { OutputItem } from "@/lib/outputs";
import { cn } from "@/lib/utils";

export function GalleryTile({
  item,
  aspect,
  priority,
  onOpen,
}: {
  item: OutputItem;
  aspect: number;
  priority?: boolean;
  onOpen: () => void;
}) {
  const [loaded, setLoaded] = useState(false);
  const [hover, setHover] = useState(false);
  const label = `${item.name}${item.engine ? ` · ${item.engine}` : ""}`;

  return (
    <button
      type="button"
      onClick={onOpen}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)}
      onBlur={() => setHover(false)}
      aria-label={`Open ${label}`}
      className="group bg-muted focus-visible:ring-ring relative block w-full overflow-hidden rounded-lg text-left outline-none focus-visible:ring-2"
      style={{ aspectRatio: `1 / ${aspect}` }}
    >
      {item.placeholder && (
        // eslint-disable-next-line @next/next/no-img-element -- inline data URI placeholder
        <img src={item.placeholder} alt="" aria-hidden className="absolute inset-0 h-full w-full scale-110 object-cover blur-xl" />
      )}
      {item.kind === "model" ? (
        <div className="text-muted-foreground absolute inset-0 flex flex-col items-center justify-center gap-2">
          <Box className="size-8" strokeWidth={1.25} />
          <span className="font-mono text-[11px]">{item.name}</span>
        </div>
      ) : item.thumbUrl ? (
        // eslint-disable-next-line @next/next/no-img-element -- presigned R2 URLs, already sized by the worker
        <img
          src={item.thumbUrl}
          alt={label}
          loading={priority ? "eager" : "lazy"}
          decoding="async"
          // Cached / server-rendered images can finish before hydration attaches onLoad.
          ref={(el) => {
            if (el?.complete && el.naturalWidth > 0) setLoaded(true);
          }}
          onLoad={() => setLoaded(true)}
          className={cn("absolute inset-0 h-full w-full object-cover transition-opacity duration-500", loaded ? "opacity-100" : "opacity-0")}
        />
      ) : null}
      {item.kind === "video" && hover && (
        // Mounted only while hovered: nothing plays (or downloads) off-screen or when idle.
        <video src={item.url} muted loop playsInline autoPlay preload="auto" className="absolute inset-0 h-full w-full object-cover" />
      )}
      {item.kind === "video" && !hover && (
        <span className="absolute top-2 right-2 rounded-full bg-black/50 p-1 text-white">
          <Play className="size-3" fill="currentColor" />
        </span>
      )}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent px-2.5 pt-6 pb-2 font-mono text-[11px] text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        {item.engine ?? item.name}
        {item.width && item.height ? ` · ${item.width}×${item.height}` : ""}
      </span>
    </button>
  );
}
