import type { Metadata } from "next";
import Link from "next/link";
import { supportEmail } from "@/lib/contact";
import { LegalPage, Todo } from "../legal-page";

export const metadata: Metadata = { title: "Refund Policy" };

export default function RefundsPage() {
  const email = supportEmail();
  return (
    <LegalPage slug="refunds" intro={<p>How billing, failed renders and refunds work. This policy is part of the <Link href="/legal/terms">Terms of Service</Link>.</p>}>
      <h2 id="billing">How you are billed</h2>
      <ul>
        <li>Renders are charged per second of render time at the prices on the <Link href="/pricing">pricing page</Link>, after your free allowance.</li>
        <li>Usage is billed monthly in arrears to your card, through Stripe. There is no subscription fee.</li>
        <li>Identical re-renders served from cache cost nothing. Time spent queued or starting a machine isn&apos;t charged.</li>
        <li>The dashboard (Billing) and <code>GET /api/v1/usage</code> show usage and cost so far this month.</li>
      </ul>

      <h2 id="faults">Renders that fail because of us</h2>
      <p>
        You aren&apos;t charged for a render that fails because of a fault in Renda&apos;s systems, for example a worker crash, an
        outage, or a render that times out on our side. If you were charged for one, email{" "}
        <a href={`mailto:${email}`}>{email}</a> with the job id and we will credit the render time to your account.{" "}
        <Todo>[OWNER: time limit for requests, e.g. within 60 days of the invoice; credit vs. refund to card.]</Todo>
      </p>

      <h2 id="charged">What is charged</h2>
      <ul>
        <li>Renders that run as requested, even if the result isn&apos;t what you wanted. Run a draft or an estimate first.</li>
        <li>Renders that fail because of the scene or settings you sent (for example a broken file, a missing texture, or a limit you set such as a maximum duration), for the render time used.</li>
        <li>Renders you cancel, for the render time used before they stopped.</li>
      </ul>

      <h2 id="disputes">Billing questions</h2>
      <p>
        If an invoice looks wrong, email <a href={`mailto:${email}`}>{email}</a> before disputing the charge with your bank, and
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
