import type { Metadata } from "next";
import Link from "next/link";
import { requireAccount } from "@/lib/auth";
import { supportEmail } from "@/lib/contact";
import { PRIVACY_PATH, RETENTION, TERMS_PATH } from "@/lib/legal";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DeleteAccountForm } from "./delete-account-form";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const { org, user } = await requireAccount();
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Settings</h1>
      <Card>
        <CardHeader><CardTitle className="text-sm">Account</CardTitle></CardHeader>
        <CardContent className="space-y-1 text-sm">
          <p><span className="text-muted-foreground">Email </span><span data-ph-mask>{user.email}</span></p>
          <p><span className="text-muted-foreground">Workspace </span>{org.name} <span className="text-muted-foreground font-mono text-xs">({org.id})</span></p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Your data</CardTitle>
          <CardDescription>
            Uploaded scene files are deleted {RETENTION.assetDays} days after they were last used, and render outputs{" "}
            {RETENTION.outputDays} days after the render finished. Your files are used only to perform your renders. See the{" "}
            <Link href={PRIVACY_PATH} className="underline underline-offset-2">Privacy Policy</Link> and{" "}
            <Link href={TERMS_PATH} className="underline underline-offset-2">Terms</Link>.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground space-y-1 text-sm">
          <p>Delete one render&apos;s outputs: <span className="font-mono text-xs">DELETE /api/v1/jobs/&lt;id&gt;</span></p>
          <p>Delete an upload: <span className="font-mono text-xs">DELETE /api/v1/uploads/&lt;sha256&gt;</span></p>
          <p>Questions or a copy of your data: <a className="underline underline-offset-2" href={`mailto:${supportEmail()}`}>{supportEmail()}</a></p>
        </CardContent>
      </Card>
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle className="text-sm">Delete account</CardTitle>
          <CardDescription>
            Permanently deletes this workspace: revokes every API key, cancels running jobs, deletes all renders and
            uploads, and removes your sign-in. Your card subscription is cancelled and usage up to now is invoiced.
            Billing records (usage, amounts, dates) are kept, without your files or personal details, as the law
            requires for tax. This can&apos;t be undone.
          </CardDescription>
        </CardHeader>
        <CardContent><DeleteAccountForm /></CardContent>
      </Card>
    </div>
  );
}
