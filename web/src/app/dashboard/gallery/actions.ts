"use server";

import { requireAccount } from "@/lib/auth";
import { GALLERY_PAGE_SIZE, listOutputs, type OutputsPage } from "@/lib/outputs";

/** Next page for the signed-in user's org (re-authenticates; the cursor is the only client input). */
export async function loadGalleryPage(cursor: string): Promise<OutputsPage> {
  const { org } = await requireAccount();
  return listOutputs(org.id, { cursor, limit: GALLERY_PAGE_SIZE });
}
