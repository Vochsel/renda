import type { Metadata } from "next";
import { withAuth } from "@workos-inc/authkit-nextjs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { TermsNotice } from "@/components/terms-notice";
import { claimAction } from "./actions";

export const metadata: Metadata = { title: "Claim your Renda trial" };

export default async function ClaimPage({ searchParams }: PageProps<"/claim">) {
  const { token, result } = await searchParams;
  const { user } = await withAuth({ ensureSignedIn: true });
  const invalid = result === "invalid" || typeof token !== "string" || !token;
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Claim this trial workspace</CardTitle>
          <CardDescription>
            An agent started a Renda trial for you. Claiming it as {user.email} keeps its renders and API key, and tops
            you up to the full free tier.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {invalid ? (
            <Alert variant="destructive">
              <AlertDescription>
                This claim link is invalid, already used, or expired. Ask your agent to run <code>renda trial</code> again, or
                go to your dashboard.
              </AlertDescription>
            </Alert>
          ) : (
            <form action={claimAction} className="space-y-3">
              <input type="hidden" name="token" value={token} />
              <Button type="submit" className="w-full">Claim workspace</Button>
              <TermsNotice action="claiming" />
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
