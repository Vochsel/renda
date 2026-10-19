"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export type TocItem = { id: string; label: string };

/** Sticky section list for wide screens; highlights the section currently in view. */
export function PageToc({ items }: { items: TocItem[] }) {
  const [active, setActive] = useState(items[0]?.id);

  useEffect(() => {
    const els = items.map((i) => document.getElementById(i.id)).filter((e): e is HTMLElement => !!e);
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setActive(top.target.id);
      },
      { rootMargin: "-20% 0px -65% 0px" },
    );
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [items]);

  return (
    <aside className="hidden xl:block" aria-label="On this page">
      <nav className="sticky top-24 pt-20 sm:pt-28">
        <ul className="border-border space-y-1 border-l text-[13px]">
          {items.map((i) => (
            <li key={i.id}>
              <a
                href={`#${i.id}`}
                className={cn(
                  "-ml-px block border-l py-1 pl-3 transition-colors",
                  i.id === active ? "border-foreground text-foreground" : "text-muted-foreground hover:text-foreground border-transparent",
                )}
              >
                {i.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
