import Link from "next/link";
import { requireAccount } from "@/lib/auth";
import { DashboardNav } from "@/components/dashboard-nav";
import { Button } from "@/components/ui/button";
import { PostHogIdentify } from "@/components/posthog-identify";
import { TermsNotice } from "@/components/terms-notice";
import { hasAcceptedCurrentTerms } from "@/lib/terms";
import { acceptTermsAction } from "./settings/actions";

export default async function DashboardLayout({ children }: LayoutProps<"/dashboard">) {
  const { user, org } = await requireAccount();
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <PostHogIdentify userId={user.id} email={user.email} orgId={org.id} orgName={org.name} />
      <header className="border-border/60 border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <Link href="/" className="font-mono text-sm font-semibold">renda</Link>
          <span className="text-muted-foreground text-sm">/</span>
          <span className="truncate text-sm">{org.name}</span>
          <div className="ml-auto flex items-center gap-3">
            <span data-ph-mask className="text-muted-foreground hidden text-sm sm:inline">{user.email}</span>
            <form action="/sign-out" method="post"><Button variant="ghost" size="sm">Sign out</Button></form>
          </div>
        </div>
      </header>
      {!hasAcceptedCurrentTerms(user) && (
        // Accounts created before the Terms existed, or after a new version (TERMS_VERSION in lib/legal.ts).
        <div className="border-border/60 bg-muted/40 border-b">
          <form action={acceptTermsAction} className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-2">
            <TermsNotice action="continuing to use Renda" className="flex-1" />
            <Button type="submit" size="sm" variant="outline">Accept</Button>
          </form>
        </div>
      )}
      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6 md:flex-row">
        <aside className="md:w-44 md:shrink-0"><DashboardNav /></aside>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
