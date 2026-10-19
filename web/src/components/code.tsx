import { cn } from "@/lib/utils";

export function Code({ children, className }: { children: string; className?: string }) {
  return (
    <pre className={cn("bg-muted/60 border-border overflow-x-auto rounded-lg border p-4 font-mono text-[13px] leading-relaxed", className)}>
      <code>{children}</code>
    </pre>
  );
}
