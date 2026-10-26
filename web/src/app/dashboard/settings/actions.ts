"use server";

import { revalidatePath } from "next/cache";
import { signOut } from "@workos-inc/authkit-nextjs";
import { requireAccount } from "@/lib/auth";
import { deleteAccount } from "@/lib/account-deletion";
import { recordTermsAcceptance } from "@/lib/terms";
import { DELETE_CONFIRMATION } from "@/lib/legal";

export type DeleteState = { error?: string };

export async function deleteAccountAction(_prev: DeleteState, formData: FormData): Promise<DeleteState> {
  const { org, user } = await requireAccount();
  if (String(formData.get("confirm") ?? "").trim().toLowerCase() !== DELETE_CONFIRMATION) {
    return { error: `Type “${DELETE_CONFIRMATION}” to confirm.` };
  }
  await deleteAccount(org.id, { actorUserId: user.id });
  await signOut({ returnTo: "/" });
  return {};
}

/** The dashboard banner shown when the user hasn't accepted the current Terms version. */
export async function acceptTermsAction() {
  const { org, user } = await requireAccount();
  await recordTermsAcceptance({ userId: user.id, orgId: org.id }, "dashboard");
  revalidatePath("/dashboard", "layout");
}
