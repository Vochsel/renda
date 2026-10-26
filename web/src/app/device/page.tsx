import type { Metadata } from "next";
import { requireAccount } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { TermsNotice } from "@/components/terms-notice";
import { decideDevice } from "./actions";

export const metadata: Metadata = { title: "Connect a device" };

const RESULT: Record<string, string> = {
  approved: "Approved. You can return to your terminal — it will pick up the key automatically.",
  denied: "Denied. The device did not get access.",
  invalid: "That code is invalid, expired or already used. Start the login again in your terminal.",
  rate_limited: "Too many attempts. Wait a few minutes, then try again.",
};

export default async function DevicePage({ searchParams }: PageProps<"/device">) {
  const { org, user } = await requireAccount();
  const { code, result } = await searchParams;
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Connect a CLI or agent</CardTitle>
          <CardDescription>
            Signed in as {user.email}. Approving creates an API key for <span className="text-foreground">{org.name}</span>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {typeof result === "string" && RESULT[result] && (
            <Alert variant={result === "approved" ? "default" : "destructive"}><AlertDescription>{RESULT[result]}</AlertDescription></Alert>
          )}
          <form action={decideDevice} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="userCode">Code shown in your terminal</Label>
              <Input id="userCode" name="userCode" defaultValue={typeof code === "string" ? code : ""} placeholder="ABCD-EFGH"
                className="font-mono text-lg tracking-widest uppercase" autoComplete="off" required />
            </div>
            <div className="flex gap-2">
              <Button type="submit" name="decision" value="approve" className="flex-1">Approve</Button>
              <Button type="submit" name="decision" value="deny" variant="outline">Deny</Button>
            </div>
            <TermsNotice action="approving" />
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
