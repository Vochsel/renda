import Link from "next/link";
import type { ReactNode } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { LEGAL_DRAFT, LEGAL_PAGES, TERMS_EFFECTIVE } from "@/lib/legal";

/** Shared frame for /legal pages: draft banner (LEGAL_DRAFT), title, effective date, sibling links, prose styles. */
export function LegalPage({ slug, intro, children }: { slug: (typeof LEGAL_PAGES)[number]["slug"]; intro?: ReactNode; children: ReactNode }) {
  const page = LEGAL_PAGES.find((p) => p.slug === slug)!;
  return (
    <article>
      {LEGAL_DRAFT && (
        <Alert className="mb-8">
          <AlertDescription>Draft — pending legal review. This document may change before it takes effect.</AlertDescription>
        </Alert>
      )}
      <nav aria-label="Legal" className="text-muted-foreground mb-8 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {LEGAL_PAGES.map((p) => (
          <Link key={p.slug} href={`/legal/${p.slug}`} className={cn("hover:text-foreground", p.slug === slug && "text-foreground font-medium")}>
            {p.title}
          </Link>
        ))}
      </nav>
      <h1 className="text-3xl font-semibold tracking-tight">{page.title}</h1>
      <p className="text-muted-foreground mt-2 text-sm">Effective {TERMS_EFFECTIVE}</p>
      {intro && <div className="text-muted-foreground mt-6">{intro}</div>}
      <div
        className={cn(
          "mt-8 text-sm leading-relaxed",
          "[&_h2]:mt-10 [&_h2]:scroll-mt-20 [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:tracking-tight",
          "[&_h3]:mt-6 [&_h3]:font-medium",
          "[&_p]:mt-3 [&_ul]:mt-3 [&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-5",
          "[&_a]:underline [&_a]:underline-offset-2 [&_code]:font-mono [&_code]:text-xs",
        )}
      >
        {children}
      </div>
    </article>
  );
}

/** Placeholder the owner must fill before publishing, rendered so it can't be missed. */
export function Todo({ children }: { children: ReactNode }) {
  return <mark className="bg-amber-200/60 text-foreground rounded px-1 dark:bg-amber-500/30">{children}</mark>;
}
