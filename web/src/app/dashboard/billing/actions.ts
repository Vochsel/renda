"use server";

import { redirect } from "next/navigation";
import { requireAccount } from "@/lib/auth";
import { checkoutUrl, portalUrl } from "@/lib/billing";
import { recordTermsAcceptance } from "@/lib/terms";

export async function addCardAction() {
  const { org, user } = await requireAccount();
  // The button sits under "By adding a card you agree to the Terms and Privacy Policy".
  await recordTermsAcceptance({ userId: user.id, orgId: org.id }, "checkout");
  redirect(await checkoutUrl(org.id, user.email));
}

export async function portalAction() {
  const { org } = await requireAccount();
  redirect(await portalUrl(org.id));
}
