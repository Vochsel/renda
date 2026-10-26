import type { Metadata } from "next";
import Link from "next/link";
import { supportEmail } from "@/lib/contact";
import { LegalPage } from "../legal-page";

export const metadata: Metadata = { title: "Refund Policy" };

export default function RefundsPage() {
  const email = supportEmail();
  return (
    <LegalPage slug="refunds" intro={<p>How prepaid credit, failed renders and refunds work. This policy is part of the <Link href="/legal/terms">Terms of Service</Link>.</p>}>
      <h2 id="billing">How you pay</h2>
      <ul>
        <li>Renda is prepaid. You buy credit in US dollars through Stripe ($5 to $1,000 at a time), and it is added as soon as the payment succeeds. There is no free allowance and no subscription fee.</li>
        <li>Renders draw your credit down per second of render time, at the prices on the <Link href="/pricing">pricing page</Link>. A render is stopped if your credit runs out.</li>
        <li>Identical re-renders served from cache cost nothing. Time spent queued or starting a machine isn&apos;t charged.</li>
        <li>Credit doesn&apos;t expire. The dashboard (Billing), <code>GET /api/v1/me</code> and <code>GET /api/v1/usage</code> show your balance and what you&apos;ve spent.</li>
      </ul>

      <h2 id="unused-credit">Refunds of unused credit</h2>
      <p>
        You can get a refund of unused credit within 30 days of buying it. Email <a href={`mailto:${email}`}>{email}</a> from
        your account&apos;s email address and we will refund the unused part of that purchase to the original payment method in
        full, with nothing deducted. Credit already spent on renders isn&apos;t refunded, except as described below.
      </p>

      <h2 id="faults">Renders that fail because of us</h2>
      <p>
        You shouldn&apos;t pay for a render that fails because of a fault in Renda&apos;s systems, for example a worker crash, an
        outage, or a render that times out on our side. Email <a href={`mailto:${email}`}>{email}</a> with the job id and we
        will credit the render&apos;s cost back to your balance.
      </p>

      <h2 id="charged">What is charged</h2>
      <ul>
        <li>Renders that run as requested, even if the result isn&apos;t what you wanted. Run a draft or an estimate first.</li>
        <li>Renders that fail because of the scene or settings you sent (for example a broken file, a missing texture, or a limit you set such as a maximum duration), for the render time used.</li>
        <li>Renders you cancel, for the render time used before they stopped.</li>
      </ul>

      <h2 id="disputes">Billing questions</h2>
      <p>
        If a charge or a render&apos;s cost looks wrong, email <a href={`mailto:${email}`}>{email}</a> before disputing the charge with your bank, and
        we&apos;ll look into it.
      </p>

      <h2 id="acl">Your consumer rights</h2>
      <p>
        Nothing in this policy excludes, restricts or modifies your rights under the Australian Consumer Law, including any
        right to a refund or other remedy for a major failure of the service.
      </p>
    </LegalPage>
  );
}
