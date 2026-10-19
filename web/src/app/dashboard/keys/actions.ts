"use server";

import { revalidatePath } from "next/cache";
import { requireAccount } from "@/lib/auth";
import { createApiKey, revokeApiKey } from "@/lib/accounts";

export type CreateKeyState = { apiKey?: string; error?: string };

export async function createKeyAction(_prev: CreateKeyState, formData: FormData): Promise<CreateKeyState> {
  const { org, user } = await requireAccount();
  const name = String(formData.get("name") ?? "").trim().slice(0, 100) || "dashboard key";
  const key = await createApiKey(org.id, user.id, name);
  revalidatePath("/dashboard/keys");
  return { apiKey: key.apiKey };
}

export async function revokeKeyAction(keyId: string) {
  const { org } = await requireAccount();
  await revokeApiKey(org.id, keyId);
  revalidatePath("/dashboard/keys");
}
