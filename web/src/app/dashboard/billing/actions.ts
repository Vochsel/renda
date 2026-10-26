"use server";

import { redirect } from "next/navigation";
import { requireAccount } from "@/lib/auth";
import { checkoutUrl, CREDIT_PURCHASE, portalUrl } from "@/lib/billing";
import { recordTermsAcceptance } from "@/lib/terms";

export async function buyCreditAction(form: FormData) {
  const { org, user } = await requireAccount();
  const amount = Number(form.get("amountUsd"));
  // The button sits under "By paying you agree to the Terms and Privacy Policy".
  await recordTermsAcceptance({ userId: user.id, orgId: org.id }, "checkout");
  redirect(await checkoutUrl(org.id, user.email, Number.isFinite(amount) ? amount : CREDIT_PURCHASE.defaultUsd));
}

export async function portalAction() {
  const { org } = await requireAccount();
  redirect(await portalUrl(org.id));
}
