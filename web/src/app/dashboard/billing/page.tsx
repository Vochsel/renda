import { requireAccount } from "@/lib/auth";
import { getBilling } from "@/lib/accounts";
import { monthRange, usageSummary } from "@/lib/billing";
import { fmtMinutes, fmtUsd } from "@/lib/format";
import { HARDWARE_LABEL, PRICE_PER_MINUTE, type Hardware } from "@/lib/pricing";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { addCardAction, portalAction } from "./actions";

export default async function BillingPage({ searchParams }: PageProps<"/dashboard/billing">) {
  const { org } = await requireAccount();
  const { setup } = await searchParams;
  const range = monthRange();
  const [billing, usage] = await Promise.all([getBilling(org.id), usageSummary(org.id, range.from, range.to)]);
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Billing</h1>
      {setup === "success" && <Alert><AlertDescription>Card added. Renders past the free tier are now billed per second.</AlertDescription></Alert>}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardDescription>Free tier</CardDescription>
            <CardTitle className="font-mono text-2xl">{fmtMinutes(billing.freeSecondsRemaining)} left</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm">Used before any paid minutes.</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Payment method</CardDescription>
            <CardTitle className="text-lg">{billing.hasPaymentMethod ? "Card on file" : "None"}</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-2">
            {billing.hasPaymentMethod ? (
              <form action={portalAction}><Button variant="outline">Manage in Stripe</Button></form>
            ) : (
              <form action={addCardAction}><Button>Add a card</Button></form>
            )}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-sm">This month</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Hardware</TableHead><TableHead className="text-right">Render time</TableHead><TableHead className="text-right">Rate / min</TableHead><TableHead className="text-right">Billed</TableHead></TableRow></TableHeader>
            <TableBody>
              {usage.byHardware.length === 0 && <TableRow><TableCell colSpan={4} className="text-muted-foreground text-sm">No usage yet.</TableCell></TableRow>}
              {usage.byHardware.map((r) => (
                <TableRow key={r.hardware}>
                  <TableCell className="font-mono text-xs">{HARDWARE_LABEL[r.hardware as Hardware] ?? r.hardware}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{fmtMinutes(r.seconds)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{fmtUsd(PRICE_PER_MINUTE[r.hardware as Hardware] ?? 0)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{fmtUsd(r.costUsd)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
            <TableFooter>
              <TableRow><TableCell colSpan={3}>Total ({usage.minutesBilled} billed min)</TableCell><TableCell className="text-right font-mono">{fmtUsd(usage.costUsd)}</TableCell></TableRow>
            </TableFooter>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
