/**
 * Legal constants shared by the /legal pages, sign-up surfaces and API responses. Pure (no db), so client
 * components can import it.
 */

/** Shows the "Draft — pending legal review" banner on every /legal page. Flip to false once reviewed. */
export const LEGAL_DRAFT = true;

/**
 * Version of the Terms + Privacy Policy that acceptance is recorded against (users/orgs.terms_version).
 * Bump it when the terms change materially; accounts on an older version are asked to accept again.
 */
export const TERMS_VERSION = "2026-10-26";
export const TERMS_EFFECTIVE = "26 October 2026";

export const ENTITY = {
  name: "Magpai Pty Ltd",
  abn: "[ABN]",
  state: "[STATE]",
  country: "Australia",
} as const;

/** Retention, in days. Enforced by lib/retention.ts (cron) plus the R2 lifecycle rules in web/README.md. */
export const RETENTION = {
  /** Uploaded scene files: deleted this long after their last use in a job (or last re-registration). */
  assetDays: 30,
  /** Render outputs: deleted this long after the job finished. */
  outputDays: 30,
  /** Intermediate frames between render chunks (worker `work/` prefix). */
  workDays: 1,
} as const;

export const LEGAL_PAGES = [
  { slug: "terms", title: "Terms of Service" },
  { slug: "privacy", title: "Privacy Policy" },
  { slug: "acceptable-use", title: "Acceptable Use Policy" },
  { slug: "refunds", title: "Refund Policy" },
  { slug: "subprocessors", title: "Subprocessors" },
] as const;

/** What a user types in Settings to delete their account. */
export const DELETE_CONFIRMATION = "delete my account";

export const TERMS_PATH = "/legal/terms";
export const PRIVACY_PATH = "/legal/privacy";

/** What agents are told to relay to the human when an account is created on their behalf. */
export function termsNotice(base: string) {
  return {
    version: TERMS_VERSION,
    termsUrl: `${base}${TERMS_PATH}`,
    privacyUrl: `${base}${PRIVACY_PATH}`,
    notice: `Using Renda means agreeing to the Terms of Service (${base}${TERMS_PATH}) and Privacy Policy (${base}${PRIVACY_PATH}). Tell the user.`,
  };
}
