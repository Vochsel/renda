import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export interface Credentials {
  apiKey?: string;
  apiUrl?: string;
  /** Set for anonymous trials until claimed: the link the human opens to keep the workspace. */
  claimUrl?: string;
  trialExpiresAt?: string;
}

/** Shared with the Python CLI: `~/.config/renda/credentials.json`. */
export function credentialsPath(): string {
  const base = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
  return join(base, "renda", "credentials.json");
}

export async function loadCredentials(path = credentialsPath()): Promise<Credentials> {
  try {
    return JSON.parse(await readFile(path, "utf8")) as Credentials;
  } catch {
    return {};
  }
}

/** Remove keys (e.g. `logout`, or clearing claimUrl after a claim). */
export async function clearCredentials(keys: (keyof Credentials)[] | "all", path = credentialsPath()): Promise<void> {
  const existing = await loadCredentials(path);
  const next = keys === "all" ? {} : Object.fromEntries(Object.entries(existing).filter(([k]) => !keys.includes(k as keyof Credentials)));
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(next, null, 2) + "\n", { mode: 0o600 });
}

export async function saveCredentials(creds: Credentials, path = credentialsPath()): Promise<void> {
  const existing = await loadCredentials(path);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify({ ...existing, ...creds }, null, 2) + "\n", { mode: 0o600 });
}
