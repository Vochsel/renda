"use client";

import { useState } from "react";
import { CopyButton } from "@/components/copy-button";
import { cn } from "@/lib/utils";

export function InstallTabs({ tabs }: { tabs: { label: string; lines: readonly string[] }[] }) {
  const [active, setActive] = useState(0);
  const lines = tabs[active].lines;
  const plain = lines.map((l) => l.replace(/\s+#.*$/, "")).join("\n");
  return (
    <div className="border-border overflow-hidden rounded-lg border">
      <div className="border-border flex items-center gap-4 border-b px-4">
        {tabs.map((t, i) => (
          <button
            key={t.label}
            type="button"
            onClick={() => setActive(i)}
            className={cn(
              "-mb-px border-b py-2 font-mono text-xs transition-colors",
              i === active ? "border-foreground text-foreground" : "text-muted-foreground hover:text-foreground border-transparent",
            )}
          >
            {t.label}
          </button>
        ))}
        <CopyButton text={plain} className="ml-auto" />
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed">
        {lines.map((l) => {
          const [cmd, comment] = l.split(/(?=\s+#)/);
          return (
            <div key={l}>
              <span className="text-muted-foreground select-none">$ </span>
              {cmd}
              {comment && <span className="text-muted-foreground">{comment}</span>}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
