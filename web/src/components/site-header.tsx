import Link from "next/link";
import { Button } from "@/components/ui/button";

export function SiteHeader() {
  return (
    <header className="border-border/60 border-b">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
        <Link href="/" className="font-mono text-sm font-semibold tracking-tight">
          renda
        </Link>
        <nav className="text-muted-foreground hidden items-center gap-4 text-sm sm:flex">
          <Link href="/docs" className="hover:text-foreground">Docs</Link>
          <Link href="/pricing" className="hover:text-foreground">Pricing</Link>
          <Link href="/benchmarks" className="hover:text-foreground">Benchmarks</Link>
          <a href="https://github.com/vochsel/renda" className="hover:text-foreground">GitHub</a>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {/* No prefetch: signed-out, both redirect to WorkOS, which a background fetch can't follow. */}
          <Button asChild variant="ghost" size="sm"><Link href="/sign-in" prefetch={false}>Sign in</Link></Button>
          <Button asChild size="sm"><Link href="/dashboard" prefetch={false}>Dashboard</Link></Button>
        </div>
      </div>
    </header>
  );
}
