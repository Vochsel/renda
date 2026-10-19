import Link from "next/link";
import { requireAccount } from "@/lib/auth";
import { DashboardNav } from "@/components/dashboard-nav";
import { Button } from "@/components/ui/button";

export default async function DashboardLayout({ children }: LayoutProps<"/dashboard">) {
  const { user, org } = await requireAccount();
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="border-border/60 border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <Link href="/" className="font-mono text-sm font-semibold">renda</Link>
          <span className="text-muted-foreground text-sm">/</span>
          <span className="truncate text-sm">{org.name}</span>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-muted-foreground hidden text-sm sm:inline">{user.email}</span>
            <form action="/sign-out" method="post"><Button variant="ghost" size="sm">Sign out</Button></form>
          </div>
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6 md:flex-row">
        <aside className="md:w-44 md:shrink-0"><DashboardNav /></aside>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
