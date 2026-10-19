import Link from "next/link";
import { Box, Play } from "lucide-react";
import type { OutputItem } from "@/lib/outputs";

/** Compact row of the latest renders for the dashboard overview; each links into the gallery. */
export function RecentStrip({ items }: { items: OutputItem[] }) {
  if (!items.length) return null;
  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {items.map((item) => (
        <Link
          key={item.id}
          href="/dashboard/gallery"
          title={`${item.name}${item.engine ? ` · ${item.engine}` : ""}`}
          className="bg-muted relative h-24 shrink-0 overflow-hidden rounded-md"
          style={{ aspectRatio: item.width && item.height ? `${item.width} / ${item.height}` : "4 / 3" }}
        >
          {item.placeholder && (
            // eslint-disable-next-line @next/next/no-img-element -- inline data URI placeholder
            <img src={item.placeholder} alt="" aria-hidden className="absolute inset-0 h-full w-full scale-110 object-cover blur-lg" />
          )}
          {item.thumbUrl ? (
            // eslint-disable-next-line @next/next/no-img-element -- presigned R2 thumbnail
            <img src={item.thumbUrl} alt={item.name} loading="lazy" className="absolute inset-0 h-full w-full object-cover" />
          ) : (
            <Box className="text-muted-foreground absolute inset-0 m-auto size-6" strokeWidth={1.25} />
          )}
          {item.kind === "video" && (
            <span className="absolute top-1.5 right-1.5 rounded-full bg-black/50 p-0.5 text-white">
              <Play className="size-2.5" fill="currentColor" />
            </span>
          )}
        </Link>
      ))}
    </div>
  );
}
