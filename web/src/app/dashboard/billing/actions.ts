"use server";

import { redirect } from "next/navigation";
import { requireAccount } from "@/lib/auth";
import { checkoutUrl, portalUrl } from "@/lib/billing";

export async function addCardAction() {
  const { org, user } = await requireAccount();
  redirect(await checkoutUrl(org.id, user.email));
}

export async function portalAction() {
  const { org } = await requireAccount();
  redirect(await portalUrl(org.id));
}
