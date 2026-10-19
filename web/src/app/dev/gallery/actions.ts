"use server";

import type { OutputsPage } from "@/lib/outputs";
import { fixturePage } from "./fixtures";

/** Dev-only: fixture pages with a little latency so the loading state is visible. */
export async function loadFixturePage(cursor: string): Promise<OutputsPage> {
  if (process.env.NODE_ENV === "production") throw new Error("Not available.");
  await new Promise((r) => setTimeout(r, 400));
  return fixturePage(cursor, 24);
}
