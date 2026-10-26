import type { Metadata } from "next";
import Link from "next/link";
import { supportEmail } from "@/lib/contact";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { LegalPage } from "../legal-page";

export const metadata: Metadata = { title: "Subprocessors" };

/** Keep in sync with the code: each entry is a service the app or worker actually calls. */
const SUBPROCESSORS = [
  { name: "Vercel Inc.", purpose: "Hosts the website, API and dashboard", data: "All requests, including account data and scene packages in transit; request logs", location: "United States" },
  { name: "Modal Labs, Inc.", purpose: "Render compute (CPU and GPU workers)", data: "Your files and scene packages while rendering; cached input files; outputs in transit", location: "United States" },
  { name: "Cloudflare, Inc. (R2)", purpose: "File storage", data: "Uploaded files and render outputs", location: "[OWNER: bucket location]" },
  { name: "Neon, Inc.", purpose: "Database (Postgres)", data: "Account, job, usage and billing records; scene packages", location: "[OWNER: Neon region]" },
  { name: "Stripe, Inc.", purpose: "Payments (buying credit) and receipts", data: "Card and billing details, credit purchase amounts", location: "United States" },
  { name: "WorkOS, Inc.", purpose: "Sign-in and user accounts", data: "Email, name, IP address and user agent at sign-in", location: "United States" },
  { name: "PostHog, Inc.", purpose: "Product analytics, error tracking, session replay", data: "Usage events, page interactions and recordings, email and workspace name of signed-in users, error reports", location: "United States" },
] as const;

export default function SubprocessorsPage() {
  const email = supportEmail();
  return (
    <LegalPage
      slug="subprocessors"
      intro={<p>The service providers that process customer data for Renda. See the <Link href="/legal/privacy">Privacy Policy</Link> for what we collect and why.</p>}
    >
      <Table className="mt-2">
        <TableHeader>
          <TableRow><TableHead>Provider</TableHead><TableHead>Purpose</TableHead><TableHead>Data</TableHead><TableHead>Location</TableHead></TableRow>
        </TableHeader>
        <TableBody>
          {SUBPROCESSORS.map((s) => (
            <TableRow key={s.name}>
              <TableCell className="align-top font-medium whitespace-normal">{s.name}</TableCell>
              <TableCell className="text-muted-foreground align-top whitespace-normal">{s.purpose}</TableCell>
              <TableCell className="text-muted-foreground align-top whitespace-normal">{s.data}</TableCell>
              <TableCell className="text-muted-foreground align-top whitespace-normal">{s.location}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p>
        We update this page before a new provider starts processing customer data. To be told about changes, email{" "}
        <a href={`mailto:${email}`}>{email}</a>.
      </p>
    </LegalPage>
  );
}
