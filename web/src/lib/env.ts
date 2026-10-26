/**
 * Lazy env access: nothing is read at import time, so `next build` works without secrets.
 * Call `env("NAME")` inside handlers; it throws a clear error if a required var is missing.
 */
const OPTIONAL_DEFAULTS: Record<string, string> = {
  APP_URL: "http://localhost:3000",
  R2_OUTPUT_TTL_SECONDS: String(60 * 60 * 24),
};

export function env(name: string): string {
  const v = process.env[name] ?? OPTIONAL_DEFAULTS[name];
  if (v === undefined || v === "") {
    throw new Error(`Missing environment variable ${name} (see web/.env.example)`);
  }
  return v;
}

export function envOptional(name: string): string | undefined {
  const v = process.env[name] ?? OPTIONAL_DEFAULTS[name];
  return v === "" ? undefined : v;
}

export function appUrl(): string {
  return (process.env.APP_URL ?? process.env.NEXT_PUBLIC_APP_URL ?? OPTIONAL_DEFAULTS.APP_URL).replace(/\/$/, "");
}
