import type { Metadata } from "next";
import Link from "next/link";
import { supportEmail } from "@/lib/contact";
import { ENTITY, RETENTION } from "@/lib/legal";
import { LegalPage, Todo } from "../legal-page";

export const metadata: Metadata = { title: "Privacy Policy" };

export default function PrivacyPage() {
  const email = supportEmail();
  return (
    <LegalPage
      slug="privacy"
      intro={
        <p>
          How {ENTITY.name} (ABN {ENTITY.abn}), which operates Renda, collects, uses, keeps and deletes information. We
          handle personal information under the Australian Privacy Act 1988 (Cth) and the Australian Privacy Principles.
        </p>
      }
    >
      <h2 id="summary">Summary</h2>
      <ul>
        <li>Your files and renders are used only to perform your renders. We never use them to train models or for any other purpose.</li>
        <li>Uploaded files are deleted {RETENTION.assetDays} days after their last use; outputs {RETENTION.outputDays} days after the render finished. You can delete either sooner, or delete your whole account.</li>
        <li>We use PostHog for product analytics, error tracking and session replay on the website. Typed input is masked in replays.</li>
        <li>Card details go to Stripe; we never see or store them.</li>
      </ul>

      <h2 id="content">1. Your content</h2>
      <p>
        Your content is what you send to render (3D files, textures, environment maps, videos, scene packages and their
        settings, and any <code>https://</code> URLs you ask us to fetch) and the outputs produced from it (images, videos,
        models, splats, thumbnails).
      </p>
      <ul>
        <li>
          <strong>Use.</strong> Only to perform the renders you request and return the results. We do not use your content to train
          machine-learning models, to improve other products, for advertising, or for any other purpose. Staff don&apos;t look at
          it except at your request, to investigate a security incident or abuse, or where the law requires.
        </li>
        <li>
          <strong>Where it goes.</strong> Uploads and outputs are stored in Cloudflare R2. Render workers on Modal download the files
          a render needs through short-lived links and write outputs back to R2. The scene package and job details are stored in
          our database (Neon).
        </li>
        <li>
          <strong>Caching.</strong> Files are stored once per unique content (by SHA-256 hash). Another account can only use a stored
          file after uploading the same bytes itself. Identical re-renders are served from a cached result instead of rendering
          again. If another account submits a render whose files and settings are byte-for-byte identical to one of yours, the
          stored result may be reused for it; that account already holds the same files.
        </li>
        <li>
          <strong>Render workers.</strong> Workers keep a cache of input files, keyed by content hash, on Modal storage so the same file
          isn&apos;t downloaded for every render. <Todo>[OWNER: this worker cache currently has no expiry, so the {RETENTION.assetDays}-day
          deletion and account deletion below don&apos;t yet reach it. Add eviction to the worker before publishing, or describe
          the actual retention here.]</Todo>
        </li>
      </ul>

      <h2 id="account">2. Account and billing information</h2>
      <ul>
        <li><strong>Account.</strong> Email address and name (from sign-in), your workspace name, and which version of the Terms you accepted and when.</li>
        <li>
          <strong>Sign-in.</strong> Handled by WorkOS. When you sign in or request an email code, your email address, IP address and
          browser user agent are sent to WorkOS. We keep a session cookie for the dashboard.
        </li>
        <li><strong>API keys.</strong> We store only a SHA-256 hash of each key, plus its name, prefix and when it was last used.</li>
        <li>
          <strong>Billing.</strong> Stripe collects and stores your card details and billing contact. We store your Stripe customer id,
          whether a card is on file, your free-allowance balance, and per-render usage (hardware, seconds, cost).
        </li>
        <li>
          <strong>Abuse limits.</strong> To rate-limit sign-ins, trials and API calls we store IP addresses, email addresses and keys only
          as keyed hashes, not in readable form.
        </li>
        <li><strong>Jobs.</strong> For each render: status, engine, hardware, timings, cost, warnings and error messages.</li>
      </ul>

      <h2 id="analytics">3. Analytics, error tracking and session replay (PostHog)</h2>
      <p>We use PostHog (US cloud) to understand how Renda is used and to find bugs.</p>
      <ul>
        <li>
          <strong>On the website:</strong> pages viewed and left, clicks and other interactions, browser and device details, referrer,
          IP address (which PostHog may use to estimate approximate location), and errors in the page. When you&apos;re signed in to
          the dashboard we link this to your account, including your email address and workspace name.
        </li>
        <li>
          <strong>Session replay:</strong> recordings of how pages render and respond while you use the site. Everything you type into
          form fields is masked, as are your email address and newly created API keys. Other text on the page, such as job
          names, file names and workspace names, can appear in recordings.
        </li>
        <li>
          <strong>From our servers:</strong> events such as sign-up, API key created (with its name), upload registered (file extension
          and size only), estimate requested, job submitted and finished (format, engine, hardware, duration, cost and, for
          failed jobs, the start of the error message, which can name files in your scene), and server errors. We never send file contents or full scene
          packages to PostHog.
        </li>
        <li>PostHog stores a cookie / local storage entry to recognise your browser between visits.</li>
      </ul>
      <p>
        <Todo>[OWNER: confirm PostHog project settings (IP capture, data retention period, replay retention) and state them here.]</Todo>
      </p>

      <h2 id="hosting">4. Hosting logs and third-party scripts</h2>
      <p>
        The site and API run on Vercel, which keeps request logs (IP address, URL, user agent, time). The 3D viewers in the
        dashboard load scripts from unpkg.com and decoders from www.gstatic.com, so those services see your IP address when
        the viewer loads. If a scene package references an <code>https://</code> URL, our workers fetch it from that site.
      </p>

      <h2 id="retention">5. How long we keep things</h2>
      <ul>
        <li><strong>Uploaded files:</strong> deleted {RETENTION.assetDays} days after they were last used in a render (or registered again). The stored copy is deleted once no account uses that file.</li>
        <li><strong>Render outputs:</strong> deleted {RETENTION.outputDays} days after the render finished. Intermediate frames: about {RETENTION.workDays} day.</li>
        <li><strong>Job records:</strong> kept while your account exists, so you can see your history. Their output lists are cleared when the outputs are deleted.</li>
        <li><strong>Unclaimed trials:</strong> stop working at their expiry time; their files follow the same {RETENTION.assetDays}/{RETENTION.outputDays}-day deletion.</li>
        <li><strong>Account information:</strong> until you delete your account.</li>
        <li><strong>Billing records:</strong> kept after account deletion, without your files or personal details, for as long as tax law requires. <Todo>[OWNER: confirm period, e.g. 5 years for Australian tax records.]</Todo></li>
        <li><strong>Backups:</strong> our database provider keeps point-in-time backups for <Todo>[OWNER: Neon history retention, e.g. 7 days]</Todo>; deleted data disappears from them after that.</li>
      </ul>

      <h2 id="deletion">6. Deleting your data</h2>
      <ul>
        <li><strong>One render&apos;s outputs:</strong> <code>DELETE /api/v1/jobs/&lt;id&gt;</code> (after the render has finished).</li>
        <li><strong>An upload:</strong> <code>DELETE /api/v1/uploads/&lt;sha256&gt;</code>.</li>
        <li>
          <strong>Your whole account:</strong> dashboard → Settings → Delete account, or <code>DELETE /api/v1/me</code>. This revokes every API
          key, cancels running renders, deletes your renders and uploads, deletes your sign-in account, and cancels your
          subscription with a final invoice for usage so far. Stored files are deleted within a few hours.
        </li>
        <li>
          We keep the billing records described above, without your files, name or email. Analytics data already sent to
          PostHog is deleted on request: email <a href={`mailto:${email}`}>{email}</a>.
        </li>
      </ul>

      <h2 id="sharing">7. Who we share information with</h2>
      <p>
        Only the service providers listed on the <Link href="/legal/subprocessors">Subprocessors</Link> page, to run Renda for us,
        and authorities where the law requires it. We don&apos;t sell personal information or share it for advertising.
      </p>

      <h2 id="overseas">8. Overseas storage</h2>
      <p>
        Our providers store and process information outside Australia, mainly in the United States
        {" "}<Todo>[OWNER: confirm the Neon and R2 regions and Modal&apos;s regions]</Todo>. We choose providers that protect it to a standard
        comparable to the Australian Privacy Principles.
      </p>

      <h2 id="security">9. Security</h2>
      <p>
        Data is encrypted in transit. API keys are stored only as hashes, uploads are checked against their hash before use,
        and access to files is through short-lived signed links. See <a href="https://github.com/vochsel/renda/blob/main/SECURITY.md">SECURITY.md</a> for
        details and how to report a vulnerability.
      </p>

      <h2 id="rights">10. Access, correction and complaints</h2>
      <p>
        You can ask for a copy of the personal information we hold about you, or ask us to correct it, by emailing{" "}
        <a href={`mailto:${email}`}>{email}</a>. If you have a privacy complaint, email us first; we aim to reply within 30 days.
        If you&apos;re not satisfied, you can complain to the Office of the Australian Information Commissioner (oaic.gov.au).
      </p>

      <h2 id="changes">11. Changes</h2>
      <p>We will update this page when our practices change, and tell you by email or in the dashboard about material changes.</p>

      <h2 id="contact">12. Contact</h2>
      <p>
        {ENTITY.name}, <Todo>[OWNER: registered address]</Todo>. Email <a href={`mailto:${email}`}>{email}</a>.
      </p>
    </LegalPage>
  );
}
