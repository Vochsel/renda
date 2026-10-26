import type { Metadata } from "next";
import Link from "next/link";
import { supportEmail } from "@/lib/contact";
import { ENTITY, RETENTION, TERMS_VERSION } from "@/lib/legal";
import { LegalPage, Todo } from "../legal-page";

export const metadata: Metadata = { title: "Terms of Service" };

export default function TermsPage() {
  const email = supportEmail();
  return (
    <LegalPage
      slug="terms"
      intro={
        <p>
          These terms are an agreement between you and {ENTITY.name} (ABN {ENTITY.abn}), an Australian company that
          operates Renda (&ldquo;Renda&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;). Version {TERMS_VERSION}.
        </p>
      }
    >
      <h2 id="agreement">1. Agreeing to these terms</h2>
      <p>
        You agree to these terms, the <Link href="/legal/privacy">Privacy Policy</Link>, the{" "}
        <Link href="/legal/acceptable-use">Acceptable Use Policy</Link> and the <Link href="/legal/refunds">Refund Policy</Link>{" "}
        when you create an account, claim a trial workspace, add a payment method, or use the Renda website, API, CLI or MCP
        server. If you use Renda for an organisation, you confirm you may bind it, and &ldquo;you&rdquo; includes it.
      </p>
      <p>
        An AI agent or script that you set up, or that holds one of your API keys, acts on your behalf. You are responsible for
        what it does with Renda, including the renders it submits and the charges they incur. Agents that create an account
        for you are told to show you these terms.
      </p>

      <h2 id="service">2. The service</h2>
      <p>
        Renda renders 3D scenes and videos in the cloud from files and packages you send, and returns the results. We
        improve and change the service over time. We may add, change or remove features; if a change materially reduces
        what you paid for, you can stop using the service and you won&apos;t be charged for usage after you stop.
      </p>
      <p>
        The Renda software is open source (Apache-2.0, with the cloud service under AGPL-3.0). Those licences govern the
        source code. These terms govern the hosted service at this website.
      </p>

      <h2 id="accounts">3. Accounts, API keys and trials</h2>
      <ul>
        <li>Keep your API keys secret. Anyone who has a key can use your account and incur charges. Revoke a leaked key at once in the dashboard.</li>
        <li>Give accurate account details and keep your email address current; we send notices there.</li>
        <li>
          Anonymous trials have limited free render time, resolution and priority, and stop working at their expiry time unless a
          person claims them. Don&apos;t create several trials to get around those limits.
        </li>
      </ul>

      <h2 id="content">4. Your content</h2>
      <p>
        &ldquo;Your content&rdquo; means the files, scene packages, settings and URLs you send to Renda, and the renders and
        other outputs produced from them. You keep all rights in your content.
      </p>
      <p>
        You grant us a limited licence to store, copy, process, render and transmit your content only as needed to provide
        the service to you: to run the renders you request, return the results, cache results so identical re-renders are
        free, and keep the service secure. We do not use your content to train machine-learning models, and we do not use
        it for any other purpose. We treat your content as confidential. Our staff do not look at it except when you ask us
        to (for example, to investigate a failed render), when needed to investigate a security incident or a breach of
        these terms, or when the law requires it. Our service providers handle it only to run the service for us (see{" "}
        <Link href="/legal/subprocessors">Subprocessors</Link>).
      </p>
      <p>
        You promise that you have the rights needed to send us your content and to have it rendered, and that doing so
        doesn&apos;t break the law or anyone else&apos;s rights.
      </p>

      <h2 id="retention">5. Keeping and deleting your content</h2>
      <ul>
        <li>Uploaded files are deleted {RETENTION.assetDays} days after they were last used in a render.</li>
        <li>Render outputs are deleted {RETENTION.outputDays} days after the render finished. Download anything you want to keep.</li>
        <li>
          You can delete a render&apos;s outputs or an upload at any time through the API, and delete your whole account in the
          dashboard (Settings) or through the API. Details are in the <Link href="/legal/privacy">Privacy Policy</Link>.
        </li>
        <li>
          We keep billing records (what was rendered on which hardware, for how long, and what it cost) after deletion, without
          your files or personal details, because we need them for invoicing and tax.
        </li>
      </ul>

      <h2 id="acceptable-use">6. Acceptable use</h2>
      <p>
        You must follow the <Link href="/legal/acceptable-use">Acceptable Use Policy</Link>. We may refuse to render, remove
        content, or suspend an account that breaks it.
      </p>

      <h2 id="fees">7. Fees and payment</h2>
      <ul>
        <li>
          Renders are charged per second of render time at the prices on the <Link href="/pricing">pricing page</Link> when the
          render runs. Each account gets a free allowance first. Identical re-renders served from cache are free.
        </li>
        <li>Usage is billed monthly in arrears to the card on file, through our payment processor, Stripe.</li>
        <li>
          Prices are in US dollars. <Todo>[OWNER: state whether prices include GST / other taxes, and how GST applies to Australian customers.]</Todo>
        </li>
        <li>We will give at least 30 days&apos; notice of a price increase. The new prices apply only to renders after that date.</li>
        <li>Estimates are a guide, not a quote. You pay for the render time actually used, unless the <Link href="/legal/refunds">Refund Policy</Link> says otherwise.</li>
        <li>If a payment fails, we may pause new renders until it is paid.</li>
      </ul>

      <h2 id="suspension">8. Suspension and ending these terms</h2>
      <p>
        You can stop using Renda and delete your account at any time. You still owe for usage up to then. We may suspend or
        close an account if you seriously or repeatedly break these terms, don&apos;t pay, or your use puts the service or other
        customers at risk. Where it&apos;s reasonable, we will tell you first and give you time to fix the problem and download
        your outputs.
      </p>

      <h2 id="acl">9. Your rights under the Australian Consumer Law</h2>
      <p>
        Our services come with guarantees that cannot be excluded under the Australian Consumer Law (Schedule 2 of the
        Competition and Consumer Act 2010 (Cth)) and similar laws. Nothing in these terms excludes, restricts or modifies any
        right or remedy, or any guarantee, warranty or other term or condition, implied or imposed by the Australian Consumer
        Law or any other law that cannot lawfully be excluded, restricted or modified.
      </p>
      <p>
        Where the law lets us limit our liability for failing to meet such a guarantee, and the services are not of a kind
        ordinarily acquired for personal, domestic or household use, our liability is limited, at our option, to supplying the
        services again or paying the cost of having them supplied again.
      </p>

      <h2 id="warranties">10. Other warranties</h2>
      <p>
        Subject to section 9, and to the extent the law allows, the service is provided as it is and as available. We
        don&apos;t promise it will be uninterrupted or error-free, or that a render will look exactly as you expect.
      </p>

      <h2 id="liability">11. Limits on liability</h2>
      <p>Subject to section 9, and to the extent the law allows:</p>
      <ul>
        <li>neither of us is liable to the other for indirect or consequential loss, or for loss of profit, revenue or data, however it arises;</li>
        <li>
          our total liability to you for all claims about the service is limited to the greater of the fees you paid us in the 12
          months before the claim arose and US$100.
        </li>
      </ul>
      <p>These limits don&apos;t apply to liability that cannot be limited by law, including for fraud.</p>

      <h2 id="indemnity">12. Claims about your content</h2>
      <p>
        If someone makes a claim against us because your content, or your use of Renda, infringes their rights or breaks the
        law, you will cover our reasonable costs of that claim, to the extent it was caused by you.
      </p>

      <h2 id="changes">13. Changes to these terms</h2>
      <p>
        We may update these terms. For a material change we will give you notice by email or in the dashboard at least 30
        days before it takes effect, and ask you to accept the new version. If you don&apos;t agree, you can stop using Renda
        and delete your account before the change takes effect.
      </p>

      <h2 id="law">14. Governing law</h2>
      <p>
        These terms are governed by the laws of {ENTITY.state}, {ENTITY.country}. Each of us submits to the non-exclusive
        jurisdiction of the courts of {ENTITY.state} and the courts that can hear appeals from them.
      </p>

      <h2 id="general">15. General</h2>
      <p>
        If part of these terms can&apos;t be enforced, the rest still applies. If we don&apos;t enforce a right straight away, we
        haven&apos;t given it up. You may not transfer these terms without our agreement; we may transfer them to a company that
        takes over the service, and we will tell you if that happens.
      </p>

      <h2 id="contact">16. Contact</h2>
      <p>
        {ENTITY.name}, <Todo>[OWNER: registered address]</Todo>. Email <a href={`mailto:${email}`}>{email}</a>.
      </p>
    </LegalPage>
  );
}
