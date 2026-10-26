import { requireAccount } from "@/lib/auth";
import { getBilling } from "@/lib/accounts";
import { CREDIT_PURCHASE, monthRange, usageSummary } from "@/lib/billing";
import { fmtMinutes, fmtUsd } from "@/lib/format";
import { HARDWARE_LABEL, PRICE_PER_MINUTE, type Hardware } from "@/lib/pricing";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { TermsNotice } from "@/components/terms-notice";
import { buyCreditAction, portalAction } from "./actions";

export default async function BillingPage({ searchParams }: PageProps<"/dashboard/billing">) {
  const { org } = await requireAccount();
  const { credit } = await searchParams;
  const range = monthRange();
  const [billing, usage] = await Promise.all([getBilling(org.id), usageSummary(org.id, range.from, range.to)]);
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Billing</h1>
      {credit === "success" && <Alert><AlertDescription>Payment received. Your credit appears here as soon as Stripe confirms it, usually within a few seconds.</AlertDescription></Alert>}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardDescription>Render credit</CardDescription>
            <CardTitle className="font-mono text-2xl">{fmtUsd(Math.max(0, billing.creditUsd))}</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm">Prepaid. Renders draw it down per second; credit doesn&apos;t expire.</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Buy credit</CardDescription>
            <CardTitle className="text-lg">Pay as you go</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col items-start gap-3">
            <div className="flex flex-wrap gap-2">
              {[10, 25, 100].map((usd) => (
                <form key={usd} action={buyCreditAction}>
                  <input type="hidden" name="amountUsd" value={usd} />
                  <Button variant={usd === CREDIT_PURCHASE.defaultUsd ? "default" : "outline"}>${usd}</Button>
                </form>
              ))}
            </div>
            <p className="text-muted-foreground text-xs">Any amount from ${CREDIT_PURCHASE.minUsd} to ${CREDIT_PURCHASE.maxUsd} on the payment page.</p>
            <TermsNotice action="paying" />
            {billing.stripeCustomerId && <form action={portalAction}><Button variant="link" className="h-auto p-0 text-xs">Receipts and invoices</Button></form>}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-sm">This month</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Hardware</TableHead><TableHead className="text-right">Render time</TableHead><TableHead className="text-right">Rate / min</TableHead><TableHead className="text-right">Cost</TableHead></TableRow></TableHeader>
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
              <TableRow><TableCell colSpan={3}>Total ({usage.minutesBilled} min)</TableCell><TableCell className="text-right font-mono">{fmtUsd(usage.costUsd)}</TableCell></TableRow>
            </TableFooter>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
