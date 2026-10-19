"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { Box, ChevronLeft, ChevronRight, Download, X } from "lucide-react";
import type { OutputItem } from "@/lib/outputs";
import { fmtAgo, fmtBytes, fmtDuration, fmtUsd } from "@/lib/format";

const HW: Record<string, string> = { cpu: "CPU", "gpu-l4": "L4 GPU", "gpu-l40s": "L40S GPU" };

export function Lightbox({
  items,
  index,
  onIndex,
  onClose,
}: {
  items: OutputItem[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const item = items[index];
  const dialog = useRef<HTMLDivElement>(null);
  const hasPrev = index > 0;
  const hasNext = index < items.length - 1;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.focus();
    return () => {
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && hasPrev) onIndex(index - 1);
      else if (e.key === "ArrowRight" && hasNext) onIndex(index + 1);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, hasPrev, hasNext, onIndex, onClose]);

  const stats: [string, string][] = [
    ["engine", item.engine ?? "—"],
    ["hardware", item.hardware ? (HW[item.hardware] ?? item.hardware) : "—"],
    ["resolution", item.width && item.height ? `${item.width}×${item.height}` : "—"],
    ["billed", fmtDuration(item.billedSeconds)],
    ["cost", fmtUsd(item.costUsd)],
    ["size", fmtBytes(item.bytes)],
    ["created", fmtAgo(item.createdAt)],
  ];

  return (
    <div
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-label={item.name}
      tabIndex={-1}
      className="bg-background/95 fixed inset-0 z-50 flex flex-col outline-none backdrop-blur-sm lg:flex-row"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="relative flex min-h-0 flex-1 items-center justify-center p-4 sm:p-10" onClick={(e) => e.target === e.currentTarget && onClose()}>
        {item.kind === "image" && (
          // eslint-disable-next-line @next/next/no-img-element -- full-resolution presigned output
          <img key={item.id} src={item.url} alt={item.name} className="max-h-full max-w-full rounded-md object-contain shadow-sm" />
        )}
        {item.kind === "video" && (
          <video key={item.id} src={item.url} poster={item.thumbUrl ?? undefined} controls autoPlay loop muted playsInline className="max-h-full max-w-full rounded-md" />
        )}
        {item.kind === "model" && (
          <div className="text-muted-foreground flex flex-col items-center gap-3">
            <Box className="size-16" strokeWidth={1} />
            <span className="font-mono text-sm">{item.name}</span>
          </div>
        )}
        {hasPrev && (
          <button type="button" aria-label="Previous" onClick={() => onIndex(index - 1)}
            className="bg-background/80 hover:bg-muted absolute top-1/2 left-2 -translate-y-1/2 rounded-full border p-2">
            <ChevronLeft className="size-4" />
          </button>
        )}
        {hasNext && (
          <button type="button" aria-label="Next" onClick={() => onIndex(index + 1)}
            className="bg-background/80 hover:bg-muted absolute top-1/2 right-2 -translate-y-1/2 rounded-full border p-2">
            <ChevronRight className="size-4" />
          </button>
        )}
      </div>
      <aside className="border-border flex shrink-0 flex-col gap-4 border-t p-5 lg:w-72 lg:border-t-0 lg:border-l">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{item.name}</p>
            <p className="text-muted-foreground truncate font-mono text-xs">{item.jobName ?? item.jobId}</p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="text-muted-foreground hover:text-foreground -m-1 p-1">
            <X className="size-4" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 font-mono text-xs">
          {stats.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="truncate text-right">{v}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-auto flex gap-2">
          <a href={item.url} download={item.name} className="hover:bg-muted inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-xs">
            <Download className="size-3.5" /> Download
          </a>
          <Link href={`/dashboard/jobs/${item.jobId}`} className="hover:bg-muted inline-flex flex-1 items-center justify-center rounded-md border px-3 py-1.5 text-xs">
            Open job
          </Link>
        </div>
        <p className="text-muted-foreground hidden font-mono text-[10px] lg:block">← → to browse · esc to close</p>
      </aside>
    </div>
  );
}
