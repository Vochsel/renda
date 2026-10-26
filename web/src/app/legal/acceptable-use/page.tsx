import type { Metadata } from "next";
import Link from "next/link";
import { securityEmail, supportEmail } from "@/lib/contact";
import { LegalPage } from "../legal-page";

export const metadata: Metadata = { title: "Acceptable Use Policy" };

export default function AcceptableUsePage() {
  const email = supportEmail();
  const security = securityEmail();
  return (
    <LegalPage
      slug="acceptable-use"
      intro={<p>This policy is part of the <Link href="/legal/terms">Terms of Service</Link>. It applies to you and to any agent or script using your account.</p>}
    >
      <h2 id="content">Content you must not render or upload</h2>
      <ul>
        <li>Child sexual abuse material, or any sexual content involving minors. We report it to the authorities.</li>
        <li>Content that is illegal where you are or in Australia, or that you don&apos;t have the rights to use.</li>
        <li>Content designed to harass, threaten or defame a real person, or sexual imagery of a real person without their consent.</li>
        <li>Malware, or files crafted to exploit the renderers, the workers or other users.</li>
      </ul>

      <h2 id="service">Using the service</h2>
      <ul>
        <li>Use the compute for rendering. Don&apos;t use render jobs to mine cryptocurrency, run unrelated workloads, or reach other systems from our workers.</li>
        <li>Don&apos;t try to get around trial limits, free allowances, rate limits or billing, for example by creating many trials or accounts.</li>
        <li>Don&apos;t try to access another account&apos;s data, or interfere with the service or other customers (including load or denial-of-service testing).</li>
        <li>Don&apos;t use <code>https://</code> asset URLs to probe or attack other systems.</li>
        <li>Don&apos;t resell access to the hosted service as your own product without our written agreement. Building products and agents that use Renda on your own account is fine.</li>
      </ul>

      <h2 id="security">Security research</h2>
      <p>
        Good-faith security testing against your own account is welcome. Follow{" "}
        <a href="https://github.com/vochsel/renda/blob/main/SECURITY.md">SECURITY.md</a> and report issues to{" "}
        <a href={`mailto:${security}`}>{security}</a>.
      </p>

      <h2 id="enforcement">What we do about breaches</h2>
      <p>
        We may refuse or cancel renders, remove content, revoke keys, or suspend or close accounts that break this policy, and
        report illegal content to the authorities. Where it&apos;s reasonable, we tell you first. To report abuse, email{" "}
        <a href={`mailto:${email}`}>{email}</a>.
      </p>
    </LegalPage>
  );
}
