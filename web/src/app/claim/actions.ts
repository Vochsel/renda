"use server";

import { redirect } from "next/navigation";
import { withAuth } from "@workos-inc/authkit-nextjs";
import { upsertUser } from "@/lib/accounts";
import { claimTrial } from "@/lib/trials";
import { ApiError } from "@/lib/errors";
import { recordTermsAcceptance } from "@/lib/terms";

export async function claimAction(formData: FormData) {
  const { user } = await withAuth({ ensureSignedIn: true });
  const token = String(formData.get("token") ?? "");
  // Only the local user row: the claimed trial org becomes this user's workspace.
  const local = await upsertUser(user);
  let orgId: string;
  try {
    orgId = (await claimTrial(token, { id: local.id, workosUserId: user.id, email: user.email })).id;
  } catch (e) {
    if (e instanceof ApiError) redirect(`/claim?result=invalid`);
    throw e;
  }
  // The claim button sits under "By claiming you agree to the Terms and Privacy Policy".
  await recordTermsAcceptance({ userId: local.id, orgId }, "claim");
  redirect("/dashboard?claimed=1");
}
