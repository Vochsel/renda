import type { Metadata } from "next";
import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";
import { TermsNotice } from "@/components/terms-notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { pricingTable } from "@/lib/pricing";

export const metadata: Metadata = { title: "Pricing" };

export default function PricingPage() {
  const p = pricingTable();
  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-16">
        <h1 className="text-3xl font-semibold tracking-tight">Pricing</h1>
        <p className="text-muted-foreground mt-3">
          Buy credit up front, then pay for render-minutes from it, metered per second. No subscription fee, no free
          render time.
        </p>
        <Card className="mt-8">
          <CardHeader><CardTitle>Per render-minute</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow><TableHead>Hardware</TableHead><TableHead>Used for</TableHead><TableHead className="text-right">USD / min</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {p.classes.map((c) => (
                  <TableRow key={c.hardware}>
                    <TableCell className="font-mono">{c.label}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {c.hardware === "cpu" ? "Mitsuba stills, Workbench, encoding, HyperFrames" : c.hardware === "gpu-l4" ? "Cycles, Eevee, most jobs" : "Large rush jobs"}
                    </TableCell>
                    <TableCell className="text-right font-mono">${c.perMinute.toFixed(3)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <ul className="text-muted-foreground mt-6 list-disc space-y-1 pl-5 text-sm">
          {p.notes.map((n) => <li key={n}>{n}</li>)}
          <li>The engine picks the cheapest hardware that meets your priority. Use <span className="font-mono">priority: &quot;economy&quot;</span> to always take the cheapest.</li>
          <li>Machine-readable: <a className="underline" href="/api/v1/pricing">/api/v1/pricing</a></li>
          <li>
            Credit is prepaid in USD: buy $5 to $1,000 at a time (default $25) and it&apos;s added as soon as the payment
            succeeds. It doesn&apos;t expire, and a render is stopped if the credit runs out.
          </li>
          <li>
            Unused credit is refundable within 30 days of purchase. Renders that fail because of a fault on our side are
            credited back; see the <Link className="underline" href="/legal/refunds">refund policy</Link>.
          </li>
        </ul>
        <div className="mt-8 flex flex-col items-start gap-2">
          {/* No prefetch: signed out, /sign-up redirects to WorkOS, which a background fetch can't follow. */}
          <Button asChild><Link href="/sign-up" prefetch={false}>Create an account</Link></Button>
          <TermsNotice action="creating an account" />
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
