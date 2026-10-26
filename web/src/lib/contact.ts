/**
 * Contact addresses, from one place. The mail domain isn't set up yet, so the defaults are obviously fake
 * placeholders; set SUPPORT_EMAIL / SECURITY_EMAIL (or change CONTACT_DEFAULTS) to switch everywhere at once:
 * legal pages, the site footer, API error messages and SECURITY.md (kept in sync by hand).
 */
export const CONTACT_DEFAULTS = {
  support: "support@[DOMAIN]",
  security: "security@[DOMAIN]",
} as const;

export function supportEmail(): string {
  return process.env.SUPPORT_EMAIL || CONTACT_DEFAULTS.support;
}

export function securityEmail(): string {
  return process.env.SECURITY_EMAIL || CONTACT_DEFAULTS.security;
}
