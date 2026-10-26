import Link from "next/link";
import { supportEmail } from "@/lib/contact";
import { ENTITY, LEGAL_PAGES } from "@/lib/legal";

/** Site-wide footer: product links, legal pages and the support address. */
export function SiteFooter() {
  const email = supportEmail();
  return (
    <footer className="text-muted-foreground mx-auto w-full max-w-5xl space-y-3 px-4 py-8 text-xs">
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        <span>Apache-2.0 · AGPL-3.0 (cloud)</span>
        <Link href="/docs" className="hover:text-foreground">Docs</Link>
        <Link href="/pricing" className="hover:text-foreground">Pricing</Link>
        <Link href="/benchmarks" className="hover:text-foreground">Benchmarks</Link>
        <Link href="/llms.txt" className="hover:text-foreground">llms.txt</Link>
        <Link href="/auth.md" className="hover:text-foreground">auth.md</Link>
        <Link href="/api/v1/openapi.json" className="hover:text-foreground">OpenAPI</Link>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        <span>© {ENTITY.name}</span>
        {LEGAL_PAGES.map((p) => (
          <Link key={p.slug} href={`/legal/${p.slug}`} className="hover:text-foreground">{p.title}</Link>
        ))}
        <a href={`mailto:${email}`} className="hover:text-foreground">{email}</a>
      </div>
    </footer>
  );
}
